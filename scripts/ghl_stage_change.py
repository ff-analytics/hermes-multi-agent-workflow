#!/usr/bin/env python3
"""Prepare and execute tightly scoped GHL opportunity stage changes.

Safety model:
- Preparation is read-only and records an immutable change request.
- Execution is permitted only for crm-automation, only from a Hermes Kanban
  worker, and only when the matching human approval task is done with metadata
  {"decision":"approved","change_id":"..."}.
- The current opportunity must still match the state observed at preparation.
- A separate GHL write credential is used only at execution time.
- Each change id is single-use; replay is refused after a verified execution.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.tool_access import EXECUTE, PREPARE, authorize  # noqa: E402

BASE_URL = "https://services.leadconnectorhq.com"
READ_CONFIG = pathlib.Path(
    os.environ.get("GHL_CRM_CONFIG", "~/.hermes/secrets/ghl-crm-automation.json")
).expanduser()
WRITE_CONFIG = pathlib.Path(
    os.environ.get(
        "GHL_CRM_WRITE_CONFIG",
        "~/.hermes/secrets/ghl-crm-automation-write.json",
    )
).expanduser()
CHANGE_DIR = pathlib.Path(
    os.environ.get("GHL_CHANGE_DIR", "~/.hermes/approvals/ghl")
).expanduser()
KANBAN_DB = pathlib.Path(
    os.environ.get("HERMES_KANBAN_DB", "~/.hermes/kanban.db")
).expanduser()
APPROVAL_HELPER = pathlib.Path(
    os.environ.get(
        "HERMES_APPROVAL_HELPER",
        "~/.hermes/scripts/create_human_approval_pair.py",
    )
).expanduser()
CHANGE_ID_RE = re.compile(r"^ghlstage-[0-9a-f]{20}$")


def _die(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    raise SystemExit(1)


def _read_json(path: pathlib.Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"required config/file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"expected JSON object in {path}")
    return data


def _read_credentials() -> tuple[str, str]:
    data = _read_json(READ_CONFIG)
    token = str(data.get("token") or "").strip()
    location_id = str(data.get("location_id") or "").strip()
    if not token or not location_id:
        raise RuntimeError("read credential must contain token and location_id")
    return token, location_id


def _write_token() -> str:
    data = _read_json(WRITE_CONFIG)
    token = str(data.get("token") or "").strip()
    if not token:
        raise RuntimeError("write credential must contain token")
    return token


def _request(
    method: str,
    token: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict:
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Version": "v3",
            "User-Agent": "donortraffic-hermes-ghl-approval-gate/1",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"GHL HTTP {exc.code}: {response_body}") from None


def _get_opportunity(token: str, opportunity_id: str) -> dict:
    data = _request("GET", token, f"/opportunities/{urllib.parse.quote(opportunity_id)}")
    opportunity = data.get("opportunity") or data
    if not isinstance(opportunity, dict) or not opportunity.get("id"):
        raise RuntimeError("GHL opportunity response did not contain an opportunity")
    return opportunity


def _get_pipelines(token: str, location_id: str) -> list[dict]:
    data = _request(
        "GET",
        token,
        "/opportunities/pipelines",
        params={"locationId": location_id},
    )
    pipelines = data.get("pipelines") or []
    if not isinstance(pipelines, list):
        raise RuntimeError("GHL pipelines response was invalid")
    return pipelines


def _find_pipeline_and_stage(
    pipelines: list[dict], pipeline_id: str, stage_id: str
) -> tuple[dict, dict]:
    for pipeline in pipelines:
        if str(pipeline.get("id") or "") != pipeline_id:
            continue
        for stage in pipeline.get("stages") or []:
            if str(stage.get("id") or "") == stage_id:
                return pipeline, stage
        raise RuntimeError(f"stage {stage_id} is not in pipeline {pipeline_id}")
    raise RuntimeError(f"pipeline {pipeline_id} was not found")


def _change_path(change_id: str) -> pathlib.Path:
    if not CHANGE_ID_RE.fullmatch(change_id):
        raise RuntimeError("invalid change_id")
    return CHANGE_DIR / f"{change_id}.json"


def _execution_path(change_id: str) -> pathlib.Path:
    if not CHANGE_ID_RE.fullmatch(change_id):
        raise RuntimeError("invalid change_id")
    return CHANGE_DIR / f"{change_id}.executed.json"


def _save_private(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _prepare(profile: str, opportunity_id: str, target_stage_id: str, reason: str) -> dict:
    decision = authorize(profile, "ghl", PREPARE)
    if not decision.allowed:
        raise PermissionError(decision.reason)

    read_token, configured_location_id = _read_credentials()
    opportunity = _get_opportunity(read_token, opportunity_id)
    actual_location_id = str(opportunity.get("locationId") or "")
    if actual_location_id and actual_location_id != configured_location_id:
        raise RuntimeError("opportunity belongs to a different GHL location")

    pipeline_id = str(opportunity.get("pipelineId") or "")
    current_stage_id = str(opportunity.get("pipelineStageId") or "")
    if not pipeline_id or not current_stage_id:
        raise RuntimeError("opportunity is missing pipeline or stage information")
    if target_stage_id == current_stage_id:
        raise RuntimeError("target stage is already the current stage")

    pipelines = _get_pipelines(read_token, configured_location_id)
    pipeline, current_stage = _find_pipeline_and_stage(
        pipelines, pipeline_id, current_stage_id
    )
    _, target_stage = _find_pipeline_and_stage(pipelines, pipeline_id, target_stage_id)

    basis = {
        "type": "ghl_opportunity_stage_change",
        "location_id": configured_location_id,
        "opportunity_id": str(opportunity.get("id") or opportunity_id),
        "pipeline_id": pipeline_id,
        "expected_current_stage_id": current_stage_id,
        "target_stage_id": target_stage_id,
        "expected_updated_at": str(opportunity.get("updatedAt") or ""),
    }
    digest = hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    change_id = "ghlstage-" + digest

    record = {
        "version": 1,
        "change_id": change_id,
        **basis,
        "opportunity_name": str(opportunity.get("name") or ""),
        "pipeline_name": str(pipeline.get("name") or ""),
        "expected_current_stage_name": str(current_stage.get("name") or ""),
        "target_stage_name": str(target_stage.get("name") or ""),
        "status": str(opportunity.get("status") or "open"),
        "monetary_value": opportunity.get("monetaryValue"),
        "reason": reason.strip(),
        "prepared_by": profile,
        "prepared_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    path = _change_path(change_id)
    if path.exists():
        existing = _read_json(path)
        if existing.get("change_id") != change_id or any(
            existing.get(key) != record.get(key)
            for key in basis
        ):
            raise RuntimeError("existing prepared change does not match this request")
    else:
        _save_private(path, record)

    return record


def _request_human_approval(record: dict, human_owner: str, priority: int) -> dict:
    source_task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    source_profile = os.environ.get("HERMES_PROFILE", "").strip()
    if not source_task_id or not source_profile:
        raise RuntimeError(
            "request-approval must run inside a Hermes Kanban worker"
        )
    if source_profile != "crm-automation":
        raise RuntimeError("only crm-automation may request this GHL approval")
    if not APPROVAL_HELPER.exists():
        raise RuntimeError(f"approval helper not found: {APPROVAL_HELPER}")

    change_id = record["change_id"]
    requested_action = (
        f"Approve exact GHL change {change_id}: move opportunity "
        f"'{record['opportunity_name']}' ({record['opportunity_id']}) from "
        f"'{record['expected_current_stage_name']}' to '{record['target_stage_name']}' "
        f"in pipeline '{record['pipeline_name']}'. To APPROVE, complete this card "
        f"with metadata {{\"decision\":\"approved\",\"change_id\":\"{change_id}\"}}. "
        "For rejection/revision, do not use decision=approved."
    )
    verification = (
        f"change_id={change_id}; opportunity_id={record['opportunity_id']}; "
        f"pipeline={record['pipeline_name']} ({record['pipeline_id']}); "
        f"current_stage={record['expected_current_stage_name']} "
        f"({record['expected_current_stage_id']}); target_stage={record['target_stage_name']} "
        f"({record['target_stage_id']}); expected_updated_at={record['expected_updated_at'] or 'unknown'}."
    )
    continuation_body = (
        f"Execute ONLY prepared GHL change {change_id}. The approval task id is "
        "included by the approval helper in this continuation. Invoke the governed "
        "executor with that exact approval task id. If execution refuses because "
        "state changed or approval metadata does not match, do not bypass it; "
        "re-inspect and create a new approval round."
    )

    cmd = [
        sys.executable,
        str(APPROVAL_HELPER),
        "--title",
        f"Approve GHL stage change: {record['opportunity_name']}",
        "--human-owner",
        human_owner,
        "--requested-action",
        requested_action,
        "--what-completed",
        "Read the live GHL opportunity and validated the target stage. No GHL mutation was performed.",
        "--verification-summary",
        verification,
        "--resume-profile",
        "crm-automation",
        "--continuation-title",
        f"Execute approved GHL stage change {change_id}",
        "--continuation-body",
        continuation_body,
        "--risk",
        "Reversible CRM mutation: one opportunity stage movement. No message sending, deletion, publishing, or spend.",
        "--priority",
        str(priority),
        "--idempotency-prefix",
        f"ghl-{change_id}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            "approval helper failed: " + (proc.stderr or proc.stdout).strip()[:1000]
        )
    try:
        result = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        raise RuntimeError("approval helper returned non-JSON output") from None
    if not result.get("ok"):
        raise RuntimeError("approval helper reported failure")
    return result


def _approval_metadata(conn: sqlite3.Connection, approval_task_id: str) -> dict:
    row = conn.execute(
        "SELECT id, title, body, status, result FROM tasks WHERE id = ?",
        (approval_task_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("approval task not found")
    if row["status"] != "done":
        raise RuntimeError(f"approval task is not done (status={row['status']})")

    run_rows = conn.execute(
        "SELECT id, metadata, summary, outcome, ended_at "
        "FROM task_runs WHERE task_id = ? AND metadata IS NOT NULL "
        "ORDER BY id DESC",
        (approval_task_id,),
    ).fetchall()
    for run in run_rows:
        raw = run["metadata"]
        if not raw:
            continue
        try:
            metadata = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        if isinstance(metadata, dict):
            return {
                "task": dict(row),
                "run": dict(run),
                "metadata": metadata,
            }
    raise RuntimeError("approval task has no structured completion metadata")


def _verify_execution_context(change_id: str, approval_task_id: str) -> dict:
    profile = os.environ.get("HERMES_PROFILE", "").strip()
    worker_task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    if profile != "crm-automation":
        raise RuntimeError("execution is restricted to crm-automation")
    if not worker_task_id:
        raise RuntimeError("execution must run inside a Hermes Kanban continuation worker")

    decision = authorize(profile, "ghl", EXECUTE)
    if not decision.allowed or not decision.needs_approval:
        raise RuntimeError("GHL execute policy is not approval-gated as expected")

    if not KANBAN_DB.exists():
        raise RuntimeError(f"Kanban DB not found: {KANBAN_DB}")
    conn = sqlite3.connect(KANBAN_DB)
    conn.row_factory = sqlite3.Row
    try:
        worker = conn.execute(
            "SELECT id, title, body, assignee, status FROM tasks WHERE id = ?",
            (worker_task_id,),
        ).fetchone()
        if worker is None:
            raise RuntimeError("current continuation task not found")
        if worker["assignee"] != "crm-automation":
            raise RuntimeError("current continuation is not assigned to crm-automation")
        worker_body = str(worker["body"] or "")
        if change_id not in worker_body or approval_task_id not in worker_body:
            raise RuntimeError(
                "current continuation is not bound to this change and approval task"
            )

        approval = _approval_metadata(conn, approval_task_id)
        approval_body = str(approval["task"].get("body") or "")
        metadata = approval["metadata"]
        if change_id not in approval_body:
            raise RuntimeError("approval card is not bound to this change_id")
        if str(metadata.get("decision") or "").strip().lower() != "approved":
            raise RuntimeError("approval metadata decision is not 'approved'")
        if str(metadata.get("change_id") or "").strip() != change_id:
            raise RuntimeError("approval metadata change_id does not match")
        return approval
    finally:
        conn.close()


def _execute(change_id: str, approval_task_id: str) -> dict:
    record = _read_json(_change_path(change_id))
    if record.get("change_id") != change_id:
        raise RuntimeError("prepared change record is invalid")

    executed_path = _execution_path(change_id)
    if executed_path.exists():
        previous = _read_json(executed_path)
        return {
            "ok": True,
            "already_executed": True,
            "change_id": change_id,
            "execution": previous,
        }

    approval = _verify_execution_context(change_id, approval_task_id)
    read_token, configured_location_id = _read_credentials()
    current = _get_opportunity(read_token, record["opportunity_id"])

    if str(current.get("locationId") or configured_location_id) != record["location_id"]:
        raise RuntimeError("opportunity location changed or does not match")
    if str(current.get("pipelineId") or "") != record["pipeline_id"]:
        raise RuntimeError("opportunity pipeline changed since preparation")
    if str(current.get("pipelineStageId") or "") != record["expected_current_stage_id"]:
        raise RuntimeError("opportunity stage changed since preparation; create a new approval round")
    expected_updated_at = str(record.get("expected_updated_at") or "")
    current_updated_at = str(current.get("updatedAt") or "")
    if expected_updated_at and current_updated_at and current_updated_at != expected_updated_at:
        raise RuntimeError("opportunity was updated since preparation; create a new approval round")

    pipelines = _get_pipelines(read_token, configured_location_id)
    _, target_stage = _find_pipeline_and_stage(
        pipelines, record["pipeline_id"], record["target_stage_id"]
    )

    write_token = _write_token()
    payload: dict[str, Any] = {
        "pipelineId": str(current.get("pipelineId") or ""),
        "name": str(current.get("name") or ""),
        "pipelineStageId": record["target_stage_id"],
        "status": str(current.get("status") or "open"),
    }
    if current.get("monetaryValue") is not None:
        payload["monetaryValue"] = current.get("monetaryValue")
    if current.get("assignedTo"):
        payload["assignedTo"] = current.get("assignedTo")

    response = _request(
        "PUT",
        write_token,
        f"/opportunities/{urllib.parse.quote(record['opportunity_id'])}",
        body=payload,
    )
    verified = _get_opportunity(read_token, record["opportunity_id"])
    if str(verified.get("pipelineStageId") or "") != record["target_stage_id"]:
        raise RuntimeError("GHL update returned but post-write verification did not match target stage")

    execution = {
        "change_id": change_id,
        "approval_task_id": approval_task_id,
        "approval_run_id": approval["run"].get("id"),
        "opportunity_id": record["opportunity_id"],
        "from_stage_id": record["expected_current_stage_id"],
        "to_stage_id": record["target_stage_id"],
        "to_stage_name": str(target_stage.get("name") or record.get("target_stage_name") or ""),
        "executed_by_profile": "crm-automation",
        "continuation_task_id": os.environ.get("HERMES_KANBAN_TASK"),
        "executed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "ghl_response_opportunity_id": str(
            ((response.get("opportunity") or {}).get("id") if isinstance(response, dict) else "")
            or record["opportunity_id"]
        ),
        "verified_stage_id": str(verified.get("pipelineStageId") or ""),
    }
    _save_private(executed_path, execution)
    return {"ok": True, "already_executed": False, "execution": execution}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Approval-gated GHL opportunity stage change tool"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare", help="Prepare a read-only stage change record")
    p_prepare.add_argument("--opportunity-id", required=True)
    p_prepare.add_argument("--target-stage-id", required=True)
    p_prepare.add_argument("--reason", default="")
    p_prepare.add_argument("--profile", default="crm-automation")

    p_request = sub.add_parser(
        "request-approval",
        help="Prepare a stage change and create the existing human approval pair",
    )
    p_request.add_argument("--opportunity-id", required=True)
    p_request.add_argument("--target-stage-id", required=True)
    p_request.add_argument("--reason", default="")
    p_request.add_argument("--human-owner", default="Eddie")
    p_request.add_argument("--priority", type=int, default=10)

    p_execute = sub.add_parser(
        "execute",
        help="Execute one exact prepared stage change after structured human approval",
    )
    p_execute.add_argument("--change-id", required=True)
    p_execute.add_argument("--approval-task-id", required=True)

    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = _prepare(
                args.profile,
                args.opportunity_id,
                args.target_stage_id,
                args.reason,
            )
            print(json.dumps({"ok": True, "change": result}, indent=2, ensure_ascii=False))
        elif args.command == "request-approval":
            record = _prepare(
                "crm-automation",
                args.opportunity_id,
                args.target_stage_id,
                args.reason,
            )
            approval = _request_human_approval(record, args.human_owner, args.priority)
            print(
                json.dumps(
                    {"ok": True, "change": record, "approval": approval},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(
                json.dumps(
                    _execute(args.change_id, args.approval_task_id),
                    indent=2,
                    ensure_ascii=False,
                )
            )
    except Exception as exc:
        _die(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()

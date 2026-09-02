#!/usr/bin/env python3
"""No-op smoke test for the Hermes human approval gate.

This script never contacts GoHighLevel or any other external system. It exists
only to prove that a steward Kanban worker can create the existing human
approval pair and that the dependent continuation refuses to proceed unless the
approval task is completed with exact structured metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

APPROVAL_HELPER = pathlib.Path(
    os.environ.get(
        "HERMES_APPROVAL_HELPER",
        "~/.hermes/scripts/create_human_approval_pair.py",
    )
).expanduser()
KANBAN_DB = pathlib.Path(
    os.environ.get("HERMES_KANBAN_DB", "~/.hermes/kanban.db")
).expanduser()


def die(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    raise SystemExit(1)


def change_id_for(source_task_id: str) -> str:
    digest = hashlib.sha256(
        ("crm-approval-smoke:" + source_task_id).encode("utf-8")
    ).hexdigest()[:20]
    return "smoke-" + digest


def request_approval(human_owner: str) -> dict:
    source_task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    source_profile = os.environ.get("HERMES_PROFILE", "").strip()
    if not source_task_id:
        raise RuntimeError("request must run inside a Hermes Kanban worker")
    if source_profile != "steward":
        raise RuntimeError("request is restricted to steward")
    if not APPROVAL_HELPER.exists():
        raise RuntimeError(f"approval helper not found: {APPROVAL_HELPER}")

    change_id = change_id_for(source_task_id)
    requested_action = (
        f"Approve NO-OP control test {change_id}. This test performs no GHL or "
        "external mutation. To APPROVE, complete this card with metadata "
        f"{{\"decision\":\"approved\",\"change_id\":\"{change_id}\"}}. "
        "For rejection/revision, do not use decision=approved."
    )
    continuation_body = (
        f"This is a NO-OP approval smoke test. Verify exact structured approval "
        f"for change {change_id} using: python3 "
        "/home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/"
        f"approval_gate_smoke_test.py verify --change-id {change_id} "
        "--approval-task-id <HUMAN_APPROVAL_TASK_ID>. The helper includes the "
        "human approval task id in this continuation body. Do not contact GHL "
        "or mutate any external system. If verification succeeds, complete this "
        "continuation and report APPROVAL GATE VERIFIED."
    )

    cmd = [
        sys.executable,
        str(APPROVAL_HELPER),
        "--title",
        "Approve CRM human-gate smoke test",
        "--human-owner",
        human_owner,
        "--requested-action",
        requested_action,
        "--what-completed",
        "Prepared a no-op approval test only. No external system was changed.",
        "--verification-summary",
        f"No-op smoke change_id={change_id}. No GHL API call or mutation is part of this test.",
        "--resume-profile",
        "steward",
        "--continuation-title",
        f"Verify CRM approval gate {change_id}",
        "--continuation-body",
        continuation_body,
        "--risk",
        "No external side effect. This test only validates Kanban human-approval metadata and dependency gating.",
        "--priority",
        "10",
        "--idempotency-prefix",
        f"crm-approval-smoke-{change_id}",
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
    return {"ok": True, "change_id": change_id, **result}


def verify(change_id: str, approval_task_id: str) -> dict:
    profile = os.environ.get("HERMES_PROFILE", "").strip()
    worker_task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    if profile != "steward":
        raise RuntimeError("verification is restricted to steward")
    if not worker_task_id:
        raise RuntimeError("verification must run inside a Hermes Kanban continuation worker")
    if not KANBAN_DB.exists():
        raise RuntimeError(f"Kanban DB not found: {KANBAN_DB}")

    conn = sqlite3.connect(KANBAN_DB)
    conn.row_factory = sqlite3.Row
    try:
        worker = conn.execute(
            "SELECT id, body, assignee, status FROM tasks WHERE id = ?",
            (worker_task_id,),
        ).fetchone()
        if worker is None:
            raise RuntimeError("current continuation task not found")
        if worker["assignee"] != "steward":
            raise RuntimeError("current continuation is not assigned to steward")
        worker_body = str(worker["body"] or "")
        if change_id not in worker_body or approval_task_id not in worker_body:
            raise RuntimeError("continuation is not bound to this approval and change id")

        approval = conn.execute(
            "SELECT id, body, status FROM tasks WHERE id = ?",
            (approval_task_id,),
        ).fetchone()
        if approval is None:
            raise RuntimeError("approval task not found")
        if approval["status"] != "done":
            raise RuntimeError(f"approval task is not done (status={approval['status']})")
        if change_id not in str(approval["body"] or ""):
            raise RuntimeError("approval card is not bound to this change id")

        runs = conn.execute(
            "SELECT id, metadata, summary, outcome, ended_at FROM task_runs "
            "WHERE task_id = ? AND metadata IS NOT NULL ORDER BY id DESC",
            (approval_task_id,),
        ).fetchall()
        metadata = None
        run_id = None
        for run in runs:
            raw = run["metadata"]
            if not raw:
                continue
            try:
                candidate = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                continue
            if isinstance(candidate, dict):
                metadata = candidate
                run_id = run["id"]
                break
        if metadata is None:
            raise RuntimeError("approval task has no structured completion metadata")
        if str(metadata.get("decision") or "").strip().lower() != "approved":
            raise RuntimeError("approval metadata decision is not 'approved'")
        if str(metadata.get("change_id") or "").strip() != change_id:
            raise RuntimeError("approval metadata change_id does not match")

        return {
            "ok": True,
            "verified": True,
            "change_id": change_id,
            "approval_task_id": approval_task_id,
            "approval_run_id": run_id,
            "continuation_task_id": worker_task_id,
            "external_mutation": False,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="No-op Hermes human approval smoke test")
    sub = parser.add_subparsers(dest="command", required=True)

    p_request = sub.add_parser("request", help="Create no-op human approval pair")
    p_request.add_argument("--human-owner", default="Eddie")

    p_verify = sub.add_parser("verify", help="Verify exact structured approval")
    p_verify.add_argument("--change-id", required=True)
    p_verify.add_argument("--approval-task-id", required=True)

    args = parser.parse_args()
    try:
        if args.command == "request":
            result = request_approval(args.human_owner)
        else:
            result = verify(args.change_id, args.approval_task_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as exc:
        die(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Prepare and execute approval-gated GitHub file changes on an isolated branch.

Safety model:
- The configured repository is fixed in private config.
- Preparation is read-only and records the exact base commit, file path, and content hash.
- Execution is restricted to the `web-dev` Hermes profile running as a Kanban continuation.
- Exact structured approval metadata must match the prepared `change_id`.
- The approved change is written only to a new `hermes/approved-<change_id>` branch.
- This tool never pushes directly to the default branch and never merges a pull request.
- `.github/workflows/` is explicitly denied in this first pilot.
- Read and write credentials are separate.
- A post-write governed read verifies the exact file content on the new branch.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
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

API = "https://api.github.com"
READ_CONFIG = pathlib.Path(
    os.environ.get(
        "GITHUB_WEB_DEV_READ_CONFIG",
        "~/.hermes/secrets/github-web-dev-read.json",
    )
).expanduser()
WRITE_CONFIG = pathlib.Path(
    os.environ.get(
        "GITHUB_WEB_DEV_WRITE_CONFIG",
        "~/.hermes/secrets/github-web-dev-write.json",
    )
).expanduser()
CHANGE_DIR = pathlib.Path(
    os.environ.get("GITHUB_CHANGE_DIR", "~/.hermes/approvals/github")
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
CHANGE_ID_PREFIX = "ghbranch-"
MAX_CONTENT_BYTES = 100_000


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


def _read_config() -> tuple[str, str]:
    data = _read_json(READ_CONFIG)
    token = str(data.get("token") or "").strip()
    repository = str(data.get("repository") or "").strip()
    if not token or repository.count("/") != 1:
        raise RuntimeError("read config must contain token and owner/name repository")
    return token, repository


def _write_token(repository: str) -> str:
    data = _read_json(WRITE_CONFIG)
    token = str(data.get("token") or "").strip()
    configured_repository = str(data.get("repository") or "").strip()
    if not token:
        raise RuntimeError("write token is not configured")
    if configured_repository != repository:
        raise RuntimeError("write credential repository does not match read credential repository")
    return token


def _request(
    method: str,
    token: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
) -> Any:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        API + path,
        data=payload,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "donortraffic-hermes-web-dev-approval-gate/1",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"GitHub HTTP {exc.code}: {error_body}") from None


def _repo_path(repository: str, suffix: str = "") -> str:
    owner, repo = repository.split("/", 1)
    return f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}{suffix}"


def _safe_repo_file_path(value: str) -> str:
    path = value.strip().replace("\\", "/").lstrip("/")
    if not path or path.endswith("/"):
        raise RuntimeError("file path must identify one file")
    parts = pathlib.PurePosixPath(path).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError("unsafe repository file path")
    lowered = path.lower()
    if lowered.startswith(".github/workflows/"):
        raise RuntimeError("workflow-file writes are not allowed in this pilot")
    if lowered == ".gitmodules" or lowered.startswith(".git/"):
        raise RuntimeError("git control-file writes are not allowed")
    return path


def _repo(token: str, repository: str) -> dict:
    data = _request("GET", token, _repo_path(repository))
    if not isinstance(data, dict):
        raise RuntimeError("invalid GitHub repository response")
    return data


def _branch_head(token: str, repository: str, branch: str) -> str:
    encoded = urllib.parse.quote("heads/" + branch, safe="")
    data = _request("GET", token, _repo_path(repository, f"/git/ref/{encoded}"))
    sha = str(((data.get("object") or {}).get("sha")) or "").strip()
    if not sha:
        raise RuntimeError(f"could not resolve branch head for {branch}")
    return sha


def _contents(token: str, repository: str, path: str, ref: str) -> dict | None:
    suffix = "/contents/" + urllib.parse.quote(path, safe="/")
    url_path = _repo_path(repository, suffix) + "?ref=" + urllib.parse.quote(ref, safe="")
    try:
        data = _request("GET", token, url_path)
    except RuntimeError as exc:
        if "GitHub HTTP 404:" in str(exc):
            return None
        raise
    if not isinstance(data, dict):
        raise RuntimeError("unexpected GitHub contents response")
    return data


def _decode_content(item: dict | None) -> bytes | None:
    if item is None:
        return None
    if item.get("type") != "file":
        raise RuntimeError("target path exists but is not a file")
    if item.get("encoding") != "base64":
        raise RuntimeError("GitHub file response is not base64 encoded")
    return base64.b64decode(str(item.get("content") or ""))


def _change_path(change_id: str) -> pathlib.Path:
    if not change_id.startswith(CHANGE_ID_PREFIX) or len(change_id) != len(CHANGE_ID_PREFIX) + 20:
        raise RuntimeError("invalid change_id")
    return CHANGE_DIR / f"{change_id}.json"


def _execution_path(change_id: str) -> pathlib.Path:
    if not change_id.startswith(CHANGE_ID_PREFIX) or len(change_id) != len(CHANGE_ID_PREFIX) + 20:
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


def _prepare(profile: str, path: str, content: str, reason: str) -> dict:
    decision = authorize(profile, "github", PREPARE)
    if not decision.allowed:
        raise PermissionError(decision.reason)

    encoded = content.encode("utf-8")
    if len(encoded) > MAX_CONTENT_BYTES:
        raise RuntimeError(f"content exceeds {MAX_CONTENT_BYTES} bytes")
    safe_path = _safe_repo_file_path(path)
    read_token, repository = _read_config()
    repo = _repo(read_token, repository)
    default_branch = str(repo.get("default_branch") or "").strip()
    if not default_branch:
        raise RuntimeError("repository default branch is unavailable")
    base_sha = _branch_head(read_token, repository, default_branch)
    existing = _contents(read_token, repository, safe_path, base_sha)
    existing_sha = str((existing or {}).get("sha") or "")
    existing_bytes = _decode_content(existing)
    existing_content_sha256 = (
        hashlib.sha256(existing_bytes).hexdigest() if existing_bytes is not None else None
    )
    content_sha256 = hashlib.sha256(encoded).hexdigest()

    basis = {
        "type": "github_branch_file_change",
        "repository": repository,
        "default_branch": default_branch,
        "expected_base_sha": base_sha,
        "path": safe_path,
        "expected_existing_blob_sha": existing_sha or None,
        "expected_existing_content_sha256": existing_content_sha256,
        "new_content_sha256": content_sha256,
    }
    digest = hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    change_id = CHANGE_ID_PREFIX + digest
    branch = f"hermes/approved-{change_id}"
    record = {
        "version": 1,
        "change_id": change_id,
        **basis,
        "branch": branch,
        "content_base64": base64.b64encode(encoded).decode("ascii"),
        "reason": reason.strip(),
        "prepared_by": profile,
        "prepared_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    output = _change_path(change_id)
    if output.exists():
        existing_record = _read_json(output)
        for key, value in basis.items():
            if existing_record.get(key) != value:
                raise RuntimeError("existing prepared change does not match this request")
        if existing_record.get("content_base64") != record["content_base64"]:
            raise RuntimeError("existing prepared content does not match")
    else:
        _save_private(output, record)
    return record


def _request_human_approval(record: dict, human_owner: str, priority: int) -> dict:
    source_task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    source_profile = os.environ.get("HERMES_PROFILE", "").strip()
    if not source_task_id or not source_profile:
        raise RuntimeError("request-approval must run inside a Hermes Kanban worker")
    if source_profile != "web-dev":
        raise RuntimeError("only web-dev may request this GitHub approval")
    if not APPROVAL_HELPER.exists():
        raise RuntimeError(f"approval helper not found: {APPROVAL_HELPER}")

    change_id = record["change_id"]
    requested_action = (
        f"Approve exact GitHub change {change_id}: create isolated branch '{record['branch']}' "
        f"from {record['default_branch']}@{record['expected_base_sha']} in {record['repository']} "
        f"and write only file '{record['path']}' with content SHA-256 {record['new_content_sha256']}. "
        "This does NOT merge or modify the default branch. To APPROVE, complete this card with "
        f"metadata {{\"decision\":\"approved\",\"change_id\":\"{change_id}\"}}. "
        "For rejection/revision, do not use decision=approved."
    )
    verification = (
        f"change_id={change_id}; repository={record['repository']}; branch={record['branch']}; "
        f"base={record['default_branch']}@{record['expected_base_sha']}; path={record['path']}; "
        f"existing_blob_sha={record['expected_existing_blob_sha'] or 'absent'}; "
        f"new_content_sha256={record['new_content_sha256']}."
    )
    continuation_body = (
        f"Execute ONLY prepared GitHub change {change_id}. Invoke the governed executor with the exact "
        "human approval task id from this continuation. Do not use the host's broad gh credential. "
        "Do not merge, modify the default branch, open a PR, or change any other file. If the executor "
        "refuses because approval or repository state does not match, do not bypass it; prepare a new round."
    )
    cmd = [
        sys.executable,
        str(APPROVAL_HELPER),
        "--title",
        f"Approve GitHub branch change: {record['path']}",
        "--human-owner",
        human_owner,
        "--requested-action",
        requested_action,
        "--what-completed",
        "Read the live repository and prepared an exact isolated-branch file change. No GitHub mutation was performed.",
        "--verification-summary",
        verification,
        "--resume-profile",
        "web-dev",
        "--continuation-title",
        f"Execute approved GitHub branch change {change_id}",
        "--continuation-body",
        continuation_body,
        "--risk",
        "Reversible repository mutation on a new non-default branch only. No merge, workflow-file write, release, or default-branch mutation.",
        "--priority",
        str(priority),
        "--idempotency-prefix",
        f"github-{change_id}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError("approval helper failed: " + (proc.stderr or proc.stdout).strip()[:1200])
    try:
        result = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        raise RuntimeError("approval helper returned non-JSON output") from None
    if not result.get("ok"):
        raise RuntimeError("approval helper reported failure")
    return result


def _approval_metadata(conn: sqlite3.Connection, approval_task_id: str) -> dict:
    task = conn.execute(
        "SELECT id, title, body, status, result FROM tasks WHERE id = ?",
        (approval_task_id,),
    ).fetchone()
    if task is None:
        raise RuntimeError("approval task not found")
    if task["status"] != "done":
        raise RuntimeError(f"approval task is not done (status={task['status']})")
    runs = conn.execute(
        "SELECT id, metadata, summary, outcome, ended_at FROM task_runs "
        "WHERE task_id = ? AND metadata IS NOT NULL ORDER BY id DESC",
        (approval_task_id,),
    ).fetchall()
    for run in runs:
        raw = run["metadata"]
        if not raw:
            continue
        try:
            metadata = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        if isinstance(metadata, dict):
            return {"task": dict(task), "run": dict(run), "metadata": metadata}
    raise RuntimeError("approval task has no structured completion metadata")


def _verify_execution_context(change_id: str, approval_task_id: str) -> dict:
    profile = os.environ.get("HERMES_PROFILE", "").strip()
    worker_task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    if profile != "web-dev":
        raise RuntimeError("execution is restricted to web-dev")
    if not worker_task_id:
        raise RuntimeError("execution must run inside a Hermes Kanban continuation worker")
    decision = authorize(profile, "github", EXECUTE)
    if not decision.allowed or not decision.needs_approval:
        raise RuntimeError("GitHub execute policy is not approval-gated as expected")
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
        if worker["assignee"] != "web-dev":
            raise RuntimeError("current continuation is not assigned to web-dev")
        body = str(worker["body"] or "")
        if change_id not in body or approval_task_id not in body:
            raise RuntimeError("current continuation is not bound to this change and approval task")

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
        return {
            "ok": True,
            "already_executed": True,
            "change_id": change_id,
            "execution": _read_json(executed_path),
        }

    approval = _verify_execution_context(change_id, approval_task_id)
    read_token, repository = _read_config()
    if repository != record["repository"]:
        raise RuntimeError("configured repository changed since preparation")
    current_base_sha = _branch_head(read_token, repository, record["default_branch"])
    if current_base_sha != record["expected_base_sha"]:
        raise RuntimeError("default branch moved since preparation; create a new approval round")
    current_item = _contents(read_token, repository, record["path"], current_base_sha)
    current_existing_sha = str((current_item or {}).get("sha") or "") or None
    current_bytes = _decode_content(current_item)
    current_content_hash = hashlib.sha256(current_bytes).hexdigest() if current_bytes is not None else None
    if current_existing_sha != record.get("expected_existing_blob_sha"):
        raise RuntimeError("target file blob changed since preparation; create a new approval round")
    if current_content_hash != record.get("expected_existing_content_sha256"):
        raise RuntimeError("target file content changed since preparation; create a new approval round")

    write_token = _write_token(repository)
    branch_ref = "refs/heads/" + record["branch"]
    _request(
        "POST",
        write_token,
        _repo_path(repository, "/git/refs"),
        body={"ref": branch_ref, "sha": record["expected_base_sha"]},
    )

    put_body: dict[str, Any] = {
        "message": f"Hermes approved change {change_id}",
        "content": record["content_base64"],
        "branch": record["branch"],
    }
    if record.get("expected_existing_blob_sha"):
        put_body["sha"] = record["expected_existing_blob_sha"]
    response = _request(
        "PUT",
        write_token,
        _repo_path(repository, "/contents/" + urllib.parse.quote(record["path"], safe="/")),
        body=put_body,
    )

    verified_item = _contents(read_token, repository, record["path"], record["branch"])
    verified_bytes = _decode_content(verified_item)
    if verified_bytes is None:
        raise RuntimeError("post-write verification could not read the branch file")
    verified_hash = hashlib.sha256(verified_bytes).hexdigest()
    if verified_hash != record["new_content_sha256"]:
        raise RuntimeError("post-write verification content hash does not match approved content")

    execution = {
        "change_id": change_id,
        "approval_task_id": approval_task_id,
        "approval_run_id": approval["run"].get("id"),
        "repository": repository,
        "branch": record["branch"],
        "path": record["path"],
        "base_sha": record["expected_base_sha"],
        "commit_sha": str(((response.get("commit") or {}).get("sha")) or ""),
        "content_sha": str(((response.get("content") or {}).get("sha")) or ""),
        "verified_content_sha256": verified_hash,
        "executed_by_profile": "web-dev",
        "continuation_task_id": os.environ.get("HERMES_KANBAN_TASK"),
        "executed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "default_branch_modified": False,
        "merged": False,
    }
    _save_private(executed_path, execution)
    return {"ok": True, "already_executed": False, "execution": execution}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Approval-gated GitHub isolated-branch file change tool"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare", help="Prepare an exact branch file change")
    p_prepare.add_argument("--path", required=True)
    p_prepare.add_argument("--content-file", required=True)
    p_prepare.add_argument("--reason", default="")
    p_prepare.add_argument("--profile", default="web-dev")

    p_request = sub.add_parser(
        "request-approval",
        help="Prepare an exact branch file change and create a human approval pair",
    )
    p_request.add_argument("--path", required=True)
    p_request.add_argument("--content-file", required=True)
    p_request.add_argument("--reason", default="")
    p_request.add_argument("--human-owner", default="Eddie")
    p_request.add_argument("--priority", type=int, default=10)

    p_execute = sub.add_parser(
        "execute",
        help="Execute one exact prepared branch file change after structured approval",
    )
    p_execute.add_argument("--change-id", required=True)
    p_execute.add_argument("--approval-task-id", required=True)

    args = parser.parse_args()
    try:
        if args.command in {"prepare", "request-approval"}:
            content_path = pathlib.Path(args.content_file).expanduser()
            if not content_path.exists() or not content_path.is_file():
                raise RuntimeError("content-file does not exist")
            content = content_path.read_text(encoding="utf-8")
            profile = args.profile if args.command == "prepare" else "web-dev"
            record = _prepare(profile, args.path, content, args.reason)
            if args.command == "prepare":
                result = {"ok": True, "change": record}
            else:
                approval = _request_human_approval(record, args.human_owner, args.priority)
                result = {"ok": True, "change": record, "approval": approval}
        else:
            result = _execute(args.change_id, args.approval_task_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as exc:
        _die(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()

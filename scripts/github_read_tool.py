#!/usr/bin/env python3
"""Governed, read-only GitHub tool for the Hermes Web Dev specialist.

The tool is intentionally restricted to the single repository named in the
private config file. It performs GET requests only and checks the central
specialist policy before accessing GitHub.

Default config:
  ~/.hermes/secrets/github-web-dev-read.json

Config shape:
  {"token":"github_pat_...","repository":"ff-analytics/daily-brief"}
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.tool_access import READ, authorize  # noqa: E402

API = "https://api.github.com"
DEFAULT_CONFIG = pathlib.Path(
    os.environ.get(
        "GITHUB_WEB_DEV_READ_CONFIG",
        "~/.hermes/secrets/github-web-dev-read.json",
    )
).expanduser()
MAX_FILE_BYTES = 120_000


def _load_config() -> tuple[str, str]:
    if not DEFAULT_CONFIG.exists():
        raise RuntimeError(f"GitHub Web Dev config not found: {DEFAULT_CONFIG}")
    data = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    token = str(data.get("token") or "").strip()
    repository = str(data.get("repository") or "").strip()
    if not token:
        raise RuntimeError("GitHub Web Dev token is not configured")
    if not repository or repository.count("/") != 1:
        raise RuntimeError("GitHub Web Dev repository must be owner/name")
    return token, repository


def _authorize(profile: str) -> None:
    decision = authorize(profile, "github", READ)
    if not decision.allowed:
        raise PermissionError(decision.reason)


def _get(token: str, path: str, params: dict | None = None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "donortraffic-hermes-web-dev-read/1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"GitHub HTTP {exc.code}: {body}") from None


def _repo_path(repository: str, suffix: str = "") -> str:
    owner, repo = repository.split("/", 1)
    return f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}{suffix}"


def health(token: str, repository: str) -> dict:
    repo = _get(token, _repo_path(repository))
    return {
        "ok": True,
        "repository": repo.get("full_name"),
        "private": repo.get("private"),
        "default_branch": repo.get("default_branch"),
        "archived": repo.get("archived"),
        "visibility": repo.get("visibility"),
        "updated_at": repo.get("updated_at"),
    }


def list_path(token: str, repository: str, path: str, ref: str | None) -> dict:
    safe_path = path.strip("/")
    suffix = "/contents" + ("/" + urllib.parse.quote(safe_path, safe="/") if safe_path else "")
    params = {"ref": ref} if ref else None
    data = _get(token, _repo_path(repository, suffix), params=params)
    if isinstance(data, list):
        return {
            "path": safe_path or "/",
            "entries": [
                {
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "type": item.get("type"),
                    "sha": item.get("sha"),
                    "size": item.get("size"),
                }
                for item in data[:100]
            ],
        }
    if not isinstance(data, dict):
        raise RuntimeError("unexpected GitHub contents response")
    if data.get("type") != "file":
        return {
            "path": data.get("path") or safe_path,
            "type": data.get("type"),
            "sha": data.get("sha"),
            "size": data.get("size"),
        }
    size = int(data.get("size") or 0)
    if size > MAX_FILE_BYTES:
        raise RuntimeError(f"file is too large for governed reader ({size} bytes)")
    content = str(data.get("content") or "")
    if data.get("encoding") == "base64":
        decoded = base64.b64decode(content).decode("utf-8", errors="replace")
    else:
        decoded = content
    return {
        "path": data.get("path"),
        "type": "file",
        "sha": data.get("sha"),
        "size": size,
        "content": decoded,
    }


def commits(token: str, repository: str, limit: int) -> dict:
    rows = _get(token, _repo_path(repository, "/commits"), params={"per_page": limit})
    return {
        "commits": [
            {
                "sha": row.get("sha"),
                "message": ((row.get("commit") or {}).get("message") or "").split("\n", 1)[0],
                "author": (((row.get("commit") or {}).get("author") or {}).get("name")),
                "date": (((row.get("commit") or {}).get("author") or {}).get("date")),
            }
            for row in (rows or [])[:limit]
        ]
    }


def pulls(token: str, repository: str, state: str, limit: int) -> dict:
    rows = _get(
        token,
        _repo_path(repository, "/pulls"),
        params={"state": state, "per_page": limit, "sort": "updated", "direction": "desc"},
    )
    return {
        "pull_requests": [
            {
                "number": row.get("number"),
                "title": row.get("title"),
                "state": row.get("state"),
                "draft": row.get("draft"),
                "user": ((row.get("user") or {}).get("login")),
                "head": ((row.get("head") or {}).get("ref")),
                "base": ((row.get("base") or {}).get("ref")),
                "updated_at": row.get("updated_at"),
            }
            for row in (rows or [])[:limit]
        ]
    }


def workflow_runs(token: str, repository: str, limit: int) -> dict:
    data = _get(
        token,
        _repo_path(repository, "/actions/runs"),
        params={"per_page": limit},
    )
    runs = data.get("workflow_runs") or []
    return {
        "workflow_runs": [
            {
                "id": run.get("id"),
                "name": run.get("name"),
                "event": run.get("event"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "head_branch": run.get("head_branch"),
                "head_sha": run.get("head_sha"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
            }
            for run in runs[:limit]
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Governed read-only GitHub tool for Web Dev")
    parser.add_argument("command", choices=("health", "path", "commits", "pulls", "workflow-runs"))
    parser.add_argument("--profile", default="web-dev")
    parser.add_argument("--path", default="")
    parser.add_argument("--ref", default="")
    parser.add_argument("--state", choices=("open", "closed", "all"), default="open")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    _authorize(args.profile)
    token, repository = _load_config()
    limit = max(1, min(args.limit, 25))

    if args.command == "health":
        result = health(token, repository)
    elif args.command == "path":
        result = list_path(token, repository, args.path, args.ref or None)
    elif args.command == "commits":
        result = commits(token, repository, limit)
    elif args.command == "pulls":
        result = pulls(token, repository, args.state, limit)
    else:
        result = workflow_runs(token, repository, limit)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

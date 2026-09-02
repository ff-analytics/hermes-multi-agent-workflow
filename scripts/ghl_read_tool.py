#!/usr/bin/env python3
"""Governed, read-only GoHighLevel tool for the Hermes CRM Automation specialist.

This is intentionally narrow: it only exposes fixed GET operations and checks
engine.tool_access before touching GHL. It never accepts an arbitrary URL and
contains no mutation methods.

Secrets live on the Hermes host, not in prompts or the repo. Default config:
  ~/.hermes/secrets/ghl-crm-automation.json

Config shape:
  {"token":"pit-...","location_id":"..."}
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.tool_access import READ, authorize  # noqa: E402

BASE_URL = "https://services.leadconnectorhq.com"
DEFAULT_CONFIG = pathlib.Path(
    os.environ.get(
        "GHL_CRM_CONFIG",
        "~/.hermes/secrets/ghl-crm-automation.json",
    )
).expanduser()


def _load_config() -> tuple[str, str]:
    token = os.environ.get("GHL_TOKEN", "").strip()
    location_id = os.environ.get("GHL_LOCATION_ID", "").strip()

    if DEFAULT_CONFIG.exists():
        payload = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        token = token or str(payload.get("token") or "").strip()
        location_id = location_id or str(payload.get("location_id") or "").strip()

    if not token:
        raise RuntimeError("GHL token is not configured")
    if not location_id:
        raise RuntimeError("GHL location_id is not configured")
    return token, location_id


def _authorize(profile: str) -> None:
    decision = authorize(profile, "ghl", READ)
    if not decision.allowed:
        raise PermissionError(decision.reason)


def _get(token: str, path: str, *, version: str, params: dict | None = None) -> dict:
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Version": version,
            "User-Agent": "donortraffic-hermes-ghl-read/1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"GHL HTTP {exc.code}: {body}") from None


def get_location(token: str, location_id: str) -> dict:
    data = _get(token, f"/locations/{location_id}", version="v3")
    loc = data.get("location") or data
    return {
        "id": loc.get("id"),
        "name": loc.get("name"),
        "companyId": loc.get("companyId"),
        "domain": loc.get("domain"),
        "website": loc.get("website"),
        "timezone": loc.get("timezone"),
    }


def get_workflows(token: str, location_id: str, limit: int) -> dict:
    data = _get(
        token,
        "/workflows/",
        version="v3",
        params={"locationId": location_id},
    )
    workflows = data.get("workflows") or []
    return {
        "count": len(workflows),
        "workflows": [
            {
                "id": w.get("id"),
                "name": w.get("name"),
                "status": w.get("status"),
                "version": w.get("version"),
                "updatedAt": w.get("updatedAt"),
            }
            for w in workflows[:limit]
        ],
    }


def get_pipelines(token: str, location_id: str, limit: int) -> dict:
    data = _get(
        token,
        "/opportunities/pipelines",
        version="v3",
        params={"locationId": location_id},
    )
    pipelines = data.get("pipelines") or []
    return {
        "count": len(pipelines),
        "pipelines": [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "stages": [
                    {"id": s.get("id"), "name": s.get("name"), "position": s.get("position")}
                    for s in (p.get("stages") or [])
                ],
            }
            for p in pipelines[:limit]
        ],
    }


def search_opportunities(token: str, location_id: str, query: str, status: str, limit: int) -> dict:
    params = {
        "locationId": location_id,
        "status": status,
        "limit": min(limit, 25),
    }
    if query:
        params["q"] = query[:75]
    data = _get(token, "/opportunities/search", version="v3", params=params)
    opportunities = data.get("opportunities") or []
    return {
        "count": len(opportunities),
        "opportunities": [
            {
                "id": o.get("id"),
                "name": o.get("name"),
                "status": o.get("status"),
                "pipelineId": o.get("pipelineId"),
                "pipelineStageId": o.get("pipelineStageId"),
                "assignedTo": o.get("assignedTo"),
                "contactId": o.get("contactId"),
                "monetaryValue": o.get("monetaryValue"),
                "createdAt": o.get("createdAt"),
                "updatedAt": o.get("updatedAt"),
            }
            for o in opportunities[:limit]
        ],
    }


def get_contact(token: str, contact_id: str) -> dict:
    data = _get(token, f"/contacts/{urllib.parse.quote(contact_id)}", version="v3")
    c = data.get("contact") or data
    return {
        "id": c.get("id"),
        "firstName": c.get("firstName"),
        "lastName": c.get("lastName"),
        "email": c.get("email"),
        "phone": c.get("phone"),
        "tags": c.get("tags") or [],
        "assignedTo": c.get("assignedTo"),
        "source": c.get("source"),
        "dateAdded": c.get("dateAdded"),
        "dateUpdated": c.get("dateUpdated"),
        "customFields": c.get("customFields") or [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Governed read-only GHL tool")
    parser.add_argument("command", choices=("health", "workflows", "pipelines", "opportunities", "contact"))
    parser.add_argument("--profile", default="steward")
    parser.add_argument("--query", default="")
    parser.add_argument("--status", default="all", choices=("open", "won", "lost", "abandoned", "all"))
    parser.add_argument("--contact-id", default="")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    _authorize(args.profile)
    token, location_id = _load_config()
    limit = max(1, min(args.limit, 25))

    if args.command == "health":
        result = {"ok": True, "profile": args.profile, "location": get_location(token, location_id)}
    elif args.command == "workflows":
        result = get_workflows(token, location_id, limit)
    elif args.command == "pipelines":
        result = get_pipelines(token, location_id, limit)
    elif args.command == "opportunities":
        result = search_opportunities(token, location_id, args.query, args.status, limit)
    else:
        if not args.contact_id:
            raise SystemExit("--contact-id is required for contact")
        result = get_contact(token, args.contact_id)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

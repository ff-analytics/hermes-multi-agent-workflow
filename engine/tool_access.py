"""Governed external-tool access for Hermes specialist agents.

This module is deliberately provider-agnostic. Tool adapters call `authorize()`
before reading or mutating an external system. Mutating/high-risk actions return
`needs_approval=True` unless the action is explicitly safe for that specialist.

The policy is default-deny: a specialist never inherits access merely because a
credential exists on the host.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


READ = "read"
PREPARE = "prepare"
EXECUTE = "execute"
DELETE = "delete"
SPEND = "spend"
PUBLISH = "publish"
SEND = "send"


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    needs_approval: bool = False
    reason: str = ""


# Capabilities are intentionally narrow. Credentials should be separately
# scoped to the minimum OAuth/API permissions required by these capabilities.
POLICY: dict[str, dict[str, FrozenSet[str]]] = {
    "steward": {
        "github": frozenset({READ, PREPARE}),
        "slack": frozenset({READ, PREPARE}),
        "gmail": frozenset({READ, PREPARE}),
        "calendar": frozenset({READ, PREPARE}),
        "asana": frozenset({READ, PREPARE, EXECUTE}),
        "ghl": frozenset({READ, PREPARE, EXECUTE}),
        "google-ads": frozenset({READ, PREPARE}),
        "meta-ads": frozenset({READ, PREPARE}),
    },
    "web-dev": {
        "github": frozenset({READ, PREPARE, EXECUTE}),
        "slack": frozenset({READ, PREPARE}),
        "asana": frozenset({READ, PREPARE}),
        "website": frozenset({READ, PREPARE, EXECUTE}),
    },
    "default": {
        "github": frozenset({READ}),
        "slack": frozenset({READ}),
    },
}

# Even when an agent has EXECUTE in its role policy, these classes of action
# must use the existing human-approval pair before execution.
ALWAYS_APPROVAL = frozenset({DELETE, SPEND, PUBLISH, SEND})

# External mutations that are reversible but still material. We keep approval
# on by default and can selectively relax individual operations after testing.
EXECUTE_REQUIRES_APPROVAL = True


def authorize(profile: str, system: str, capability: str) -> AccessDecision:
    profile = str(profile or "default").strip().lower()
    system = str(system or "").strip().lower()
    capability = str(capability or "").strip().lower()

    profile_policy = POLICY.get(profile)
    if profile_policy is None:
        return AccessDecision(
            False,
            False,
            f"{profile} has no active tool-access policy",
        )

    allowed = profile_policy.get(system, frozenset())
    if capability not in allowed:
        return AccessDecision(
            False,
            False,
            f"{profile} is not allowed to {capability} on {system}",
        )

    if capability in ALWAYS_APPROVAL:
        return AccessDecision(True, True, f"{capability} always requires human approval")

    if capability == EXECUTE and EXECUTE_REQUIRES_APPROVAL:
        return AccessDecision(True, True, "external mutation requires human approval")

    return AccessDecision(True, False, "allowed by specialist policy")


def systems_for(profile: str) -> dict[str, list[str]]:
    """Return a serializable capability map for diagnostics/UI."""
    profile = str(profile or "default").strip().lower()
    policy = POLICY.get(profile, POLICY["default"])
    return {system: sorted(capabilities) for system, capabilities in sorted(policy.items())}

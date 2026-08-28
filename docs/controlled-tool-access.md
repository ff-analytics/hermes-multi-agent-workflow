# Controlled Tool Access + Slack Specialist Participation

This implements the operating model requested for Hermes specialists: agents may inspect and prepare work inside business systems, but credentials alone never imply permission to act.

## Access model

Every external action is classified as one of:

- `read` — inspect/search/retrieve.
- `prepare` — draft a change, response, campaign, workflow, code change, or configuration without applying it.
- `execute` — apply a reversible external mutation.
- `send`, `publish`, `spend`, `delete` — high-impact actions.

`engine/tool_access.py` is the default-deny policy gate. Each adapter must call `authorize(profile, system, capability)` before performing an external action.

### Approval rules

- Read and prepare actions may run automatically when that specialist has the capability.
- Execute actions currently require the existing human-approval workflow.
- Send, publish, spend, and delete always require explicit human approval.
- Credentials should be individually scoped per service and kept outside prompts, SOUL files, Kanban task bodies, and logs.

## Initial specialist/system map

- CRM Automation: GHL read/prepare; execute only after approval.
- Web Dev: GitHub/website read/prepare; execute only after approval.
- Paid Media: Google Ads / Meta read/prepare; no spend or publish without approval.
- Admin Ops: Gmail/Calendar/Slack/Asana read/prepare; external sends require approval.
- Director/Strategist: primarily read/prepare and coordinate specialists.

This map can be tightened by client account so a specialist receives only the client-specific credential or delegated connection for the task being worked.

## Verified implementation status

### CRM Automation → GoHighLevel — VERIFIED

The first controlled-access pilot is live and has passed an end-to-end test against the DonorTraffic GHL sub-account.

Verified controls:

- `crm-automation` is a dedicated Hermes profile with isolated profile state.
- A profile-specific `crm-automation-ghl` skill exposes governed read access only.
- The read credential and write credential are separate files with mode `0600`.
- The write credential is not used by the normal reader.
- `engine/tool_access.py` allows GHL execute for `crm-automation` but marks it as requiring human approval.
- The human approval helper creates an unassigned `needs_input` card plus a dependent continuation assigned back to `crm-automation`.
- Approval is machine-verifiable: the approval task must be `done` and its completion metadata must contain the exact `decision=approved` and matching `change_id`.
- The continuation must be bound to the same approval task and change id.
- The executor checks the live GHL opportunity still matches the prepared pipeline/stage snapshot before writing.
- The executor performs one exact approved opportunity-stage move and then verifies the live stage afterward.
- Replay of an already executed change id is blocked by a private execution record.
- Tirith is available on the system PATH for Hermes command scanning.

End-to-end controlled test completed successfully: a disposable Support Ticket opportunity moved from `In Progress` to `Waiting on Client` only after the matching structured approval was recorded, and a governed live read verified the resulting target stage.

### Web Dev → GitHub — VERIFIED

The Web Dev controlled-access pilot is live for `ff-analytics/daily-brief` and has passed an end-to-end branch-only mutation test.

Verified controls:

- `web-dev` uses the profile-specific `web-dev-github` skill.
- The governed reader is restricted to the configured repository and uses a separate fine-grained read token.
- The host's broader `gh` credential is not part of the specialist workflow.
- Read and write credentials are separate files with mode `0600` and are scoped to the same single repository.
- The write credential has only the repository content capability needed for approved isolated-branch changes; Actions, Workflows, Pull Requests, administration, and similar broader scopes are not required for this pilot.
- The governed change flow is `Read → Prepare → Human approval → Execute on isolated branch → Verify`.
- Preparation records the exact default-branch SHA, file path, existing file state, proposed content hash, and deterministic `change_id` without mutating GitHub.
- Execution attempted from an ordinary shell fails closed because execution is restricted to the `web-dev` Hermes continuation worker.
- Human approval is machine-verifiable: the approval task must be `done` and completion metadata must contain exact `decision=approved` plus the matching `change_id`.
- The continuation is bound to the exact approval task and change id.
- Before writing, the executor confirms the configured repository, default-branch SHA, and target file state still match the prepared snapshot.
- The executor creates only `hermes/approved-<change_id>` and writes only the exact approved file content.
- `.github/workflows/` writes are denied in this pilot.
- Direct default-branch writes, PR creation, and merge are outside this executor's scope.
- Post-write verification reads the created branch through the governed reader and confirms the exact approved SHA-256.
- Successful execution is recorded privately so replay does not repeat the mutation.

End-to-end controlled test completed successfully with change `ghbranch-3b672313193d39336845`: the approved continuation created branch `hermes/approved-ghbranch-3b672313193d39336845`, added only `docs/hermes-web-dev-approval-gate-test.md`, verified the expected content hash, and left `main` unchanged. No PR or merge was created.

## Slack conversation layer

The CEO Daily Brief Slack reader and the interactive specialist layer are intentionally separate.

The interactive design is:

1. Slack event/mention arrives at the Hermes Slack intake bridge.
2. Intake records channel, thread, author, message, and any explicitly mentioned specialist.
3. If a specialist is explicitly mentioned, route there; otherwise use triage.
4. The specialist may read thread context and approved connected systems.
5. The specialist replies in the same Slack thread only when it has useful output.
6. Cross-specialist handoffs remain Kanban dependencies so ownership is visible.
7. Any execution needing approval creates the existing `needs_input` human-approval card and a dependent continuation.
8. After approval, the continuation resumes and reports the result back to the originating Slack thread.

### Noise controls

- Do not have every specialist listen/respond to every message.
- Explicit @mention wins over automatic triage.
- Automatic participation should require a task-like request or clear blocker.
- One thread has one active owning specialist unless a dependency is deliberately created.
- Status chatter should be suppressed; report only useful findings, requests for approval/input, and completion.

## Rollout order

1. ~~Connect one read-first system to one specialist: CRM Automation → GHL.~~ VERIFIED
2. ~~Validate read + prepare behavior and approval-gated mutation.~~ VERIFIED
3. ~~Add GitHub to Web Dev using the same policy gate.~~ VERIFIED
4. Add Slack interactive intake/reply bridge.
5. Add Admin/Google and paid-media accounts.
6. Add client-specific credential isolation and reusable client agent fleets.

The goal is not maximum autonomy. The goal is useful autonomous research/preparation with explicit controls around external side effects.

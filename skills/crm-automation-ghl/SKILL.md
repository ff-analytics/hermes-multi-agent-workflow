---
name: crm-automation-ghl
description: Read DonorTraffic GoHighLevel safely.
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    tags: [ghl, crm, automation, donortraffic, read-only]
    category: crm
---

# Steward — GoHighLevel Read Access

Use this skill when a CRM/automation task requires inspecting the DonorTraffic GoHighLevel sub-account.

## Security boundary

This skill is **read-only**.

- Never read, print, copy, summarize, or expose the GHL credential file or token.
- Never call GoHighLevel directly with `curl`, `requests`, browser automation, or another script.
- Use only the governed command below. It checks the central Hermes tool-access policy before making a request.
- Do not create, update, delete, publish, send, move, assign, or otherwise mutate GHL records.
- If a requested solution requires a GHL change, inspect what is needed, prepare the exact proposed change, and stop for human approval. Do not execute the change from this skill.
- Return only the minimum contact/customer data needed for the task; avoid unnecessary PII in summaries or Kanban notes.

## Governed command

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/ghl_read_tool.py <command> --profile steward
```

Available commands:

### Confirm account access

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/ghl_read_tool.py health --profile steward
```

Use this only when connection/account identity needs verification.

### List workflows

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/ghl_read_tool.py workflows --profile steward --limit 20
```

Use this to find workflow names, IDs, publish state, version, and updated timestamp.

### List pipelines and stages

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/ghl_read_tool.py pipelines --profile steward --limit 20
```

Use this to inspect pipeline IDs, names, and stages.

### Search opportunities

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/ghl_read_tool.py opportunities --profile steward --status all --limit 20
```

Optional text filter:

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/ghl_read_tool.py opportunities --profile steward --query "search text" --status all --limit 20
```

### Read one contact

Only after a task gives or legitimately discovers the contact ID:

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/ghl_read_tool.py contact --profile steward --contact-id "CONTACT_ID"
```

Do not enumerate contacts just to collect data.

## Working method

1. Identify the CRM question before querying GHL.
2. Run the smallest read command that can answer it.
3. Cross-reference IDs rather than guessing from names.
4. Explain what GHL currently shows and distinguish observation from recommendation.
5. If a fix is needed, produce a concise proposed change and its expected effect.
6. For any mutation, stop and hand off to the existing human-approval path before execution.

## Example

Request: "Check whether the DonorTraffic Support Ticket System and Support Tickets pipeline exist and tell me what you find."

Use `workflows` and `pipelines`, locate the exact names, and report their current state. Do not edit either object.

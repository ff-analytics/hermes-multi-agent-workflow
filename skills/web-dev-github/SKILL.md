---
name: web-dev-github
description: Governed GitHub read, prepare, and human-approved isolated-branch changes for Web Dev.
version: 1.1.0
platforms: [linux]
metadata:
  hermes:
    tags: [github, web-dev, repository, actions, approval-gated]
    category: development
---

# Web Dev — Governed GitHub Access

Use this skill when the Web Dev specialist needs to inspect the configured GitHub repository or prepare an exact repository change for human approval.

## Security boundary

Access is repository-scoped and default-deny.

- Never read, print, copy, summarize, or expose either GitHub credential file or token.
- Do not use the globally authenticated `gh` CLI for specialist repository access; that host credential is broader than this specialist's scope.
- Never call GitHub directly with `curl`, `requests`, browser automation, or an ungoverned script.
- Read operations must use the governed read tool below.
- Repository mutations must use only the governed approval-gated branch-change tool below.
- Read and write credentials are separate fine-grained tokens for the configured repository.
- Never write directly to the default branch.
- Never merge a pull request from this skill.
- Never create a pull request from this skill in the current pilot.
- Never write `.github/workflows/` files through the branch-change tool; the executor denies them.
- Never create releases, tags, deployments, workflow runs, repository settings changes, secret changes, or other GitHub mutations.
- An approval for one `change_id` authorizes only that exact prepared change. It does not authorize related edits.
- If live repository state changes after preparation, do not bypass the executor's refusal; prepare a new change and request a new approval.

## Governed read command

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_read_tool.py <command> --profile web-dev
```

The configured repository is fixed in the private credential config and cannot be overridden by the agent.

### Confirm repository access

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_read_tool.py health --profile web-dev
```

### List a repository path

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_read_tool.py path --profile web-dev --path "runner"
```

Root listing:

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_read_tool.py path --profile web-dev --path ""
```

### Read a text file

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_read_tool.py path --profile web-dev --path ".github/workflows/ceo-daily-brief.yml"
```

Optional ref:

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_read_tool.py path --profile web-dev --path "runner/run_brief_v2.py" --ref main
```

### Recent commits

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_read_tool.py commits --profile web-dev --limit 10
```

### Pull requests

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_read_tool.py pulls --profile web-dev --state open --limit 10
```

### Workflow runs

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_read_tool.py workflow-runs --profile web-dev --limit 10
```

## Governed change workflow

All GitHub changes follow:

**Read → Prepare → Human approval → Execute on isolated branch → Verify**

The governed tool is:

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_branch_change.py <command>
```

### 1. Prepare only

Create the complete proposed file content in a local temporary file, then prepare the immutable change record:

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_branch_change.py prepare \
  --path "path/to/file" \
  --content-file /tmp/proposed-file \
  --reason "Why this change is needed"
```

Preparation is read-only. Record the returned `change_id`, exact default-branch SHA, path, proposed branch, existing file state, and new content SHA-256.

### 2. Request human approval

This command must run inside a `web-dev` Hermes Kanban worker so the approval card and dependent continuation are correctly bound:

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_branch_change.py request-approval \
  --path "path/to/file" \
  --content-file /tmp/proposed-file \
  --reason "Why this change is needed" \
  --human-owner "Eddie" \
  --priority 10
```

After the approval pair is created, stop. Do not execute the change from the source task.

The human approval must be recorded as structured completion metadata matching the exact prepared change:

```json
{"decision":"approved","change_id":"ghbranch-..."}
```

Free-text approval alone is not sufficient.

### 3. Execute only from the approved continuation

The dependent `web-dev` continuation may invoke:

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_branch_change.py execute \
  --change-id "ghbranch-..." \
  --approval-task-id "t_..."
```

The executor must refuse unless all of the following are true:

- it is running as profile `web-dev`;
- it is running inside the correct Hermes Kanban continuation;
- the continuation is bound to the exact approval task and `change_id`;
- the approval task is completed;
- approval metadata contains `decision=approved` and the exact `change_id`;
- the configured repository still matches preparation;
- the default branch SHA still matches the prepared snapshot;
- the target file state still matches the prepared snapshot;
- the target path is allowed;
- the change has not already been executed.

Successful execution creates only the prepared `hermes/approved-<change_id>` branch, writes the exact approved file content, and verifies the resulting content hash through the governed reader. It does not modify the default branch, open a PR, or merge.

## Working method

1. Identify the development question or requested fix.
2. Use the smallest governed read operations needed to understand live state.
3. For informational requests, report findings and stop.
4. For changes, state the exact file and intended effect before preparation.
5. Build the complete proposed file content locally without using GitHub write credentials.
6. Prepare the immutable change and inspect the returned snapshot details.
7. Request a human approval pair from a `web-dev` Kanban source task and stop.
8. Let only the dependent continuation execute after exact structured approval.
9. Report the created branch, commit SHA, changed file, verification hash, and whether the default branch remained unchanged.
10. Never fall back to the broader host `gh` authentication if a governed operation fails; report the scoped-access or approval failure instead.

## Verified pilot behavior

The first controlled pilot proved that the Web Dev specialist could:

- read the private configured repository through the scoped read token;
- prepare an exact change without mutating GitHub;
- fail closed when execution was attempted outside the authorized continuation;
- create an unassigned blocked human approval card plus dependent `web-dev` continuation;
- execute only after exact structured approval;
- create one isolated non-default branch and one approved file;
- verify the exact content SHA-256 after execution;
- leave the default branch unchanged;
- avoid opening or merging a pull request.

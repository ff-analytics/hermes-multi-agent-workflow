---
name: web-dev-github
description: Read the scoped GitHub repository safely for Web Dev work.
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    tags: [github, web-dev, repository, actions, read-only]
    category: development
---

# Web Dev — Governed GitHub Read Access

Use this skill when the Web Dev specialist needs to inspect the configured GitHub repository.

## Security boundary

This skill is **read-only** and repository-scoped.

- Never read, print, copy, summarize, or expose the GitHub credential file or token.
- Do not use the globally authenticated `gh` CLI for repository access from this skill; that account credential is broader than this specialist's scope.
- Never call GitHub directly with `curl`, `requests`, browser automation, or another script.
- Use only the governed command below. It checks the central Hermes tool-access policy and uses the separately scoped fine-grained token.
- Do not create branches, commits, tags, issues, pull requests, reviews, merges, workflow runs, releases, or any other GitHub mutation from this skill.
- If a requested fix needs a repository change, inspect the current state, prepare the exact proposed change/diff, and stop for human approval before any write-capable path is used.

## Governed command

```bash
python3 /home/ubuntu/hermes/.portfolio-audit/hermes-multi-agent-workflow/scripts/github_read_tool.py <command> --profile web-dev
```

The configured repository is fixed in the private credential config and cannot be overridden by the agent.

## Available commands

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

## Working method

1. Identify the development question before querying GitHub.
2. Use the smallest read operation that can answer it.
3. Treat repository contents, commits, pull requests, and workflow runs as observations, not permission to mutate them.
4. When proposing code or workflow changes, state the exact files and intended effect.
5. Prepare changes in analysis/draft form only until the approved write path is implemented and a human approval is recorded.
6. Never fall back to the broader host `gh` authentication when this governed reader fails; report the scoped-access failure instead.

## Example

Request: "Check which model fallbacks the CEO Daily Brief currently uses and whether the workflow is passing the Abacus secret."

Read `.github/workflows/ceo-daily-brief.yml` plus the relevant runner/router files, summarize the observed configuration, and do not modify the repository.

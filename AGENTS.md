# AGENTS.md

Guidelines for AI coding agents (Claude Code, Codex, Copilot, etc.) working in this repository.

## Repository context

- This repository is a **fork**: `h0ffmann/astrbot_plugin_disaster_warning`.
- The **original (upstream)** repository is `Pancakes-Labs/astrbot_plugin_disaster_warning`.

## Rule #1 — Never interact with the upstream repository without explicit approval

Without the fork owner's **explicit, per-action approval**, agents must **never**:

- Create, open, or draft a pull request against `Pancakes-Labs/astrbot_plugin_disaster_warning`.
- Comment on, open, close, react to, label, or otherwise modify any issue or PR in the upstream repository.
- Mention or reference upstream issues/PRs (e.g. `Pancakes-Labs/astrbot_plugin_disaster_warning#123`, `Fixes #123`, `Closes #123`) in commits, PR titles/descriptions, or comments in a way that creates a cross-reference or notification on the upstream repository.
- Push branches to, or otherwise write to, the upstream repository.

Reading upstream issues/PRs (e.g. `gh issue view`) is allowed.

All pull requests go to the fork only. Note that `gh pr create` defaults to the **parent** repository when run in a fork, so always pass the target explicitly:

```bash
gh pr create --repo h0ffmann/astrbot_plugin_disaster_warning --base main ...
```

Approval given for one action does not carry over to the next one.

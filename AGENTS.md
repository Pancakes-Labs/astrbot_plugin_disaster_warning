# AGENTS.md

These rules are for AI coding agents (Claude Code, Codex, Copilot and others) that work in this repository.

## Repository context

- This repository is a **fork**: `h0ffmann/astrbot_plugin_disaster_warning`.
- The **original (upstream)** repository is `Pancakes-Labs/astrbot_plugin_disaster_warning`.

## Rule 1: Do not use the upstream repository without approval

Get the explicit approval of the fork owner for each action. Without this approval, do not do these actions:

- Do not create a pull request (or a draft pull request) in `Pancakes-Labs/astrbot_plugin_disaster_warning`.
- Do not comment on, open, close, label or change an issue or a pull request in the upstream repository.
- Do not refer to upstream issues or pull requests in commits, pull request text or comments. Do not write `Fixes #123`, `Closes #123` or `Pancakes-Labs/astrbot_plugin_disaster_warning#123`. These references send notifications to the upstream repository.
- Do not push branches to the upstream repository.

You can read upstream issues and pull requests (for example, with `gh issue view`).

Send all pull requests to the fork. In a fork, `gh pr create` uses the **parent** repository by default. Always give the target:

```bash
gh pr create --repo h0ffmann/astrbot_plugin_disaster_warning --base main ...
```

An approval is for one action only. Get a new approval for each new action.

## Rule 2: Language (hard rule)

- Write all new comments, docstrings and documentation in English.
- Do not add text in Portuguese (pt-BR). Proper nouns and raw data values from external sources are not text that you write; you can keep them.
- Write all English text in ASD-STE100 (Simplified Technical English):
  - Write short sentences: 20 words maximum for an instruction, 25 words maximum for a description.
  - Write one instruction in each sentence.
  - Use the active voice. Use the present tense for descriptions and the imperative for instructions.
  - Use simple words that have one meaning. Use the same word for the same thing. Do not use idioms, slang or contractions.
  - Use articles ("the", "a"). Do not use noun clusters of more than three words.
- Do not change the Chinese comments that are already in the upstream code. Keep the log message style of the upstream code (`[灾害预警] ...`).

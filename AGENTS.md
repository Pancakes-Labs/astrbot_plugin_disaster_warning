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
- Do not add text in Portuguese (pt-BR) to comments, docstrings or documentation. The pt-BR text of an extension is permitted only in its locale file (`locale/pt-BR.json`). Proper nouns and raw data values from external sources are not text that you write; you can keep them.
- Write the pt-BR locale text with the ptbr-writer skill (level `estrito`): an explicit subject, one statement in each sentence, the decimal comma and ISO 8601 dates.
- Write all English text in ASD-STE100 (Simplified Technical English):
  - Write short sentences: 20 words maximum for an instruction, 25 words maximum for a description.
  - Write one instruction in each sentence.
  - Use the active voice. Use the present tense for descriptions and the imperative for instructions.
  - Use simple words that have one meaning. Use the same word for the same thing. Do not use idioms, slang or contractions.
  - Use articles ("the", "a"). Do not use noun clusters of more than three words.
- Do not change the Chinese comments that are already in the upstream code. Keep the log message style of the upstream code (`[灾害预警] ...`).

## Rule 3: A pull request only adds lines (hard rule)

- Do not change or remove existing upstream code in a feature pull request. This makes the review easy.
- Before you push, run `git diff --numstat main | awk '$2>0'`. The command must give no output.
- Insert a hook as new lines. If you must change an existing function, add a wrapper after it.
- Put simplifications of the existing code in `docs/ponytail-audit.md` as suggestions. Do not apply them.

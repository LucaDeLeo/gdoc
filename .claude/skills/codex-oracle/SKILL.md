---
name: codex-oracle
description: Query OpenAI Codex (GPT-5.2-codex) for second opinions, alternative implementations, or specialized knowledge. Use when you want to consult another AI model, need a different perspective on a coding problem, or want to verify an approach.
argument-hint: "[prompt]"
---

# Codex Oracle

Invoke OpenAI's Codex CLI to get responses from GPT-5.2-codex.

## Prerequisites

- `codex` CLI installed (`npm i -g @openai/codex`)
- Authenticated via `codex auth login` (uses account auth, not API key)

## Usage

Run the script with a prompt. Codex runs in read-only mode and sees the same working directory.

```bash
# Determine script location (project-local or global install)
CODEX_SCRIPT=".claude/skills/codex-oracle/scripts/ask_codex.sh"
[[ ! -f "$CODEX_SCRIPT" ]] && CODEX_SCRIPT="$HOME/.claude/skills/codex-oracle/scripts/ask_codex.sh"

# Default: gpt-5.2-codex with medium reasoning
bash "$CODEX_SCRIPT" "What's the most efficient way to implement a bloom filter in Rust?"

# With model override
bash "$CODEX_SCRIPT" "Review error handling in this file" codex-mini-latest

# Full output (all thinking/exec traces)
bash "$CODEX_SCRIPT" "Debug this complex race condition" gpt-5.2-codex xhigh 600 full
```

## Arguments

| Arg | Default | Description |
|-----|---------|-------------|
| `$1` | (required) | The prompt |
| `$2` | `gpt-5.2-codex` | Model (`gpt-5.2-codex`, `codex-mini-latest`) |
| `$3` | `xhigh` | Reasoning effort (`low`, `medium`, `high`, `xhigh`) |
| `$4` | `600` | Timeout in seconds (10 min) |
| `$5` | `brief` | Output verbosity (`brief` = final answer only, `full` = all traces) |

## When to use

- Need an alternative implementation approach
- Want to verify Claude's solution with another model
- Specialized domain where Codex may have different training
- Debugging: "Why might this code fail under X conditions?"
- Security review or vulnerability analysis (Codex has strong cybersec capabilities)

## Prompting tips

- Be specific: include language, constraints, context
- For code review: reference file paths (Codex sees the repo)
- For debugging: include error messages verbatim
- Use `xhigh` effort for complex multi-step reasoning

## Limitations

- Read-only: Codex cannot modify files (by design)
- Timeout default 10 minutes for xhigh reasoning
- No streaming—waits for full response

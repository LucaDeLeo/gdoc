---
name: gsd:second-opinion
description: Get a second opinion from OpenAI Codex (GPT-5.2-codex) on code, architecture, or implementation decisions. Use when you want independent verification or an alternative perspective.
argument-hint: "[question or topic]"
---

# GSD Second Opinion

Query OpenAI's Codex CLI for an independent perspective on code, architecture, or implementation decisions.

## Usage

```
/gsd:second-opinion [your question]
```

## Examples

```
/gsd:second-opinion Review the auth flow for security issues
/gsd:second-opinion Is there a better way to structure the API routes?
/gsd:second-opinion Check if the database schema handles edge cases
/gsd:second-opinion What did I miss in this implementation?
```

## How It Works

1. Runs codex-oracle with `gpt-5.2-codex` model and `xhigh` reasoning
2. Codex sees the same codebase (read-only access)
3. Returns Codex's analysis without the full execution trace

## Execution

Run the codex-oracle script with the user's question:

```bash
QUESTION="$ARGUMENTS"
if [ -z "$QUESTION" ]; then
  echo "Usage: /gsd:second-opinion [your question]"
  echo ""
  echo "Examples:"
  echo "  /gsd:second-opinion Review the error handling in src/api/"
  echo "  /gsd:second-opinion Is this the right architecture for real-time updates?"
  exit 1
fi

# Support both project-local (.claude/) and global (~/.claude/) installs
if [[ -f ".claude/skills/codex-oracle/scripts/ask_codex.sh" ]]; then
  RESULT=$(bash .claude/skills/codex-oracle/scripts/ask_codex.sh "$QUESTION")
else
  RESULT=$(bash ~/.claude/skills/codex-oracle/scripts/ask_codex.sh "$QUESTION")
fi
echo "$RESULT"
```

## When to Use

- **Before major decisions**: Get Codex's take on architectural choices
- **After implementation**: Independent code review
- **When stuck**: Different perspective on debugging
- **For verification**: Confirm Claude's analysis is complete

## Integration with GSD

This skill is also called automatically by GSD workflows:
- `verify-phase.md` - Parallel verification + final check
- `diagnose-issues.md` - Alternative hypotheses
- `execute-plan.md` - Post-completion code review

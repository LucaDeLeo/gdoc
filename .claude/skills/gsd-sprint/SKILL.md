---
name: gsd:sprint
description: Autonomous phase execution with Codex validation. Runs plan/execute/verify cycles in a bash loop until done or halted.
argument-hint: "<start> <end> [--yolo] [--skip-codex] [--resume]"
---

# GSD Sprint

Run multiple phases autonomously with Codex validation at each boundary. Based on the Ralph pattern - the loop is bash, state lives in files.

## Usage

```bash
# Interactive mode (default) - pause between phases
/gsd:sprint 3 6

# AFK mode - runs until complete or halt
/gsd:sprint 3 6 --yolo

# Skip Codex validation (faster, less safe)
/gsd:sprint 3 6 --skip-codex

# Resume interrupted sprint
/gsd:sprint --resume
```

## What It Does

For each phase in range:

1. **Plan** - Calls `/gsd:plan-phase` if no PLAN.md exists
2. **Codex validates plans** - Checks feasibility before execution
3. **Execute** - Calls `/gsd:execute-phase`
4. **Codex reviews code** - Checks for bugs, security issues
5. **Checkpoint** - Pauses for review (unless `--yolo`)

## Halt Conditions

**Always halts (even in yolo):**
- Codex `[HALT] critical` - security/major bugs
- Auth gates - require credentials
- Verification failures - gaps found
- Git errors, build failures

**Mode-dependent:**
- Codex warnings - interactive: ask, yolo: log and continue
- Human-verify checkpoints - interactive: wait, yolo: continue

## State Tracking

Sprint progress is tracked in `.planning/SPRINT.md`:
- Current phase position
- Completion status per phase
- Codex validation results
- Checkpoints for resume

## Modes

| Mode | Behavior |
|------|----------|
| Interactive (default) | Pause between phases, present checkpoints |
| AFK (`--yolo`) | Auto-continue, use defaults, still halt on critical |
| No Codex (`--skip-codex`) | Skip validation (faster but risky) |
| Resume (`--resume`) | Continue from SPRINT.md checkpoint |

## Examples

```bash
# Run phases 3-6 interactively
/gsd:sprint 3 6

# Run overnight - will halt if anything critical
/gsd:sprint 3 10 --yolo

# Quick run without Codex overhead
/gsd:sprint 5 5 --skip-codex

# Resume after fixing an issue
/gsd:sprint --resume
```

## Execution

This runs in a tmux session so you can detach and reattach.

```bash
ARGS="$ARGUMENTS"
SESSION_NAME="gsd-sprint"

# Find the script (project-local or global)
if [[ -f ".claude/skills/gsd-sprint/scripts/sprint.sh" ]]; then
  SCRIPT=".claude/skills/gsd-sprint/scripts/sprint.sh"
else
  SCRIPT="$HOME/.claude/skills/gsd-sprint/scripts/sprint.sh"
fi

# Kill existing session if running, create new one
tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
tmux new-session -d -s "$SESSION_NAME" -c "$(pwd)"

# Run the sprint script in the tmux session
tmux send-keys -t "$SESSION_NAME" "bash $SCRIPT $ARGS" Enter

# Tell user how to attach
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "GSD Sprint started in tmux session: $SESSION_NAME"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "To attach and watch progress:"
echo "  tmux attach -t $SESSION_NAME"
echo ""
echo "To detach (while attached):"
echo "  Ctrl+b then d"
echo ""
echo "To check if still running:"
echo "  tmux has-session -t $SESSION_NAME 2>/dev/null && echo 'Running' || echo 'Finished'"
echo ""
```

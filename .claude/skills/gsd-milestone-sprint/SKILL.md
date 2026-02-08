---
name: gsd:milestone-sprint
description: Run entire milestone autonomously with Codex validation. Auto-detects current milestone, executes all phases, and runs audit.
argument-hint: "[milestone] [--yolo] [--skip-codex] [--resume] [--complete]"
---

# Milestone Sprint

Run an entire milestone autonomously, from current position to milestone completion.

## How It Differs from `/gsd:sprint`

| Aspect | `/gsd:sprint` | `/gsd:milestone-sprint` |
|--------|---------------|------------------------|
| Input | Manual phase range (`3 6`) | Auto-detect from ROADMAP.md |
| Scope | Arbitrary phases | One complete milestone |
| Discussion | Skips (assumes done) | Auto-discuss via Claude ↔ Codex |
| Completion | Just finishes phases | Optionally runs `/gsd:complete-milestone` |
| State file | `SPRINT.md` | `MILESTONE-SPRINT.md` |

## Usage

```bash
# Run current milestone (auto-detect from 🚧 marker in ROADMAP.md)
/gsd:milestone-sprint

# Run specific milestone
/gsd:milestone-sprint v1.2

# AFK mode (still halts on critical issues, still runs audit)
/gsd:milestone-sprint --yolo

# Skip codex validation (audit still runs)
/gsd:milestone-sprint --skip-codex

# Resume interrupted milestone sprint
/gsd:milestone-sprint --resume

# Auto-complete milestone when done (run audit → /gsd:complete-milestone)
/gsd:milestone-sprint --complete
```

## Flow

1. **Detect/parse milestone** → get phase range from ROADMAP.md
2. **For each phase:**
   - **Auto-discuss** (Claude ↔ Codex dialogue) → creates CONTEXT.md
   - **Plan** (`/gsd:plan-phase`) → creates PLAN.md files
   - **Execute** (`/gsd:execute-phase`) → runs tasks, creates SUMMARY.md
   - **Codex reviews** at each step (unless --skip-codex)
3. **Always run audit** (`/gsd:audit-milestone`) — halt if gaps found
4. If `--complete`: run `/gsd:complete-milestone`
5. Otherwise: stop, user finalizes manually

## Auto-Discuss (Claude ↔ Codex Conversation)

Instead of human Q&A, phases are discussed via AI dialogue:

1. **Claude proposes** — Analyzes phase, makes decisions grounded in milestone goal
2. **Codex challenges** — Plays devil's advocate, suggests alternatives
3. **Claude incorporates** — Finalizes decisions, flags genuine uncertainties
4. **Output** — CONTEXT.md with locked decisions + flagged items

Max 5 rounds. Early exit if Codex returns `[READY]` with no questions.

**Uncertainties:**
- In **yolo mode**: Uses reasonable defaults, logs what was skipped
- In **interactive mode**: Prompts user for flagged items only

## Modes

| Mode | Behavior |
|------|----------|
| **Interactive** (default) | Pause between phases, prompt on warnings, ask about uncertainties |
| **YOLO** (`--yolo`) | Auto-continue, use defaults for uncertainties, only halt on critical issues |
| **No Codex** (`--skip-codex`) | Skip Codex validation steps (audit still runs at end) |

## State File

Creates `.planning/MILESTONE-SPRINT.md` with progress tracking:

```yaml
---
started: "2024-01-25T..."
milestone: v1.2
milestone_name: "Org CRM"
mode: yolo
phase_range: "11-16"
current_phase: 13
phases_remaining: [14, 15, 16]
status: running
auto_complete: false
---
```

## Execution

This runs in a tmux session so you can detach and reattach.

```bash
ARGS="$ARGUMENTS"
SESSION_NAME="gsd-milestone"

# Find the script (project-local or global)
if [[ -f ".claude/skills/gsd-milestone-sprint/scripts/milestone-sprint.sh" ]]; then
  SCRIPT=".claude/skills/gsd-milestone-sprint/scripts/milestone-sprint.sh"
else
  SCRIPT="$HOME/.claude/skills/gsd-milestone-sprint/scripts/milestone-sprint.sh"
fi

# Kill existing session if running, create new one
tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
tmux new-session -d -s "$SESSION_NAME" -c "$(pwd)"

# Run the milestone sprint script in the tmux session
tmux send-keys -t "$SESSION_NAME" "bash $SCRIPT $ARGS" Enter

# Tell user how to attach
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Milestone Sprint started in tmux session: $SESSION_NAME"
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

This executes the milestone autonomously in a bash loop, calling Claude for each phase operation.

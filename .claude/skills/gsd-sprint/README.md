# GSD Sprint System

Autonomous phase execution with AI-powered validation and self-healing fix loops.

## Overview

The GSD Sprint system runs multiple phases autonomously, using a **bash loop** for orchestration and **Claude** for intelligent task execution. OpenAI Codex acts as a validator, reviewing plans and code at each step.

**Key innovation:** When Codex finds issues, Claude automatically fixes them and Codex re-reviews — up to 3 attempts before halting. This "fix loop" pattern enables true autonomous operation.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        GSD SPRINT SYSTEM                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   /gsd:sprint 3 6              /gsd:milestone-sprint                │
│   ─────────────────            ─────────────────────                │
│   Manual phase range           Auto-detect from ROADMAP.md          │
│   Assumes context exists       Auto-discuss via Claude ↔ Codex     │
│   State: SPRINT.md             State: MILESTONE-SPRINT.md           │
│                                                                     │
│   Both use the same core loop:                                      │
│                                                                     │
│   ┌──────────────────────────────────────────────────────────┐      │
│   │  FOR each phase:                                         │      │
│   │    1. Plan (if needed)  ───► Codex validates ───┐        │      │
│   │    2. Execute           ───► Codex reviews  ────┤        │      │
│   │                                                 │        │      │
│   │         ┌───────────────────────────────────────┘        │      │
│   │         ▼                                                │      │
│   │    ┌─────────┐                                           │      │
│   │    │ Issues? │─── NO ──► Continue                        │      │
│   │    └────┬────┘                                           │      │
│   │         │ YES                                            │      │
│   │         ▼                                                │      │
│   │    ┌───────────────┐                                     │      │
│   │    │ Critical?     │─── YES ──► HALT                     │      │
│   │    └───────┬───────┘                                     │      │
│   │            │ NO (warning)                                │      │
│   │            ▼                                             │      │
│   │    ┌───────────────┐     ┌───────────────┐               │      │
│   │    │ Claude Fixer  │────►│ Codex Review  │◄──┐           │      │
│   │    │ applies fix   │     │   (again)     │───┘           │      │
│   │    └───────────────┘     └───────────────┘               │      │
│   │                           (max 3 attempts)               │      │
│   └──────────────────────────────────────────────────────────┘      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Two Sprint Modes

### `/gsd:sprint` — Phase Range Sprint

Run a specific range of phases. Best when you've already discussed context.

```bash
# Interactive mode (pause between phases)
/gsd:sprint 3 6

# AFK mode (runs until complete or critical halt)
/gsd:sprint 3 6 --yolo

# Skip Codex validation (faster, less safe)
/gsd:sprint 3 6 --skip-codex

# Resume interrupted sprint
/gsd:sprint --resume
```

### `/gsd:milestone-sprint` — Full Milestone Sprint

Run an entire milestone from current position to completion. Auto-detects milestone from ROADMAP.md.

```bash
# Run current milestone
/gsd:milestone-sprint

# Run specific milestone
/gsd:milestone-sprint v1.2

# AFK mode with auto-complete
/gsd:milestone-sprint --yolo --complete

# Resume interrupted milestone sprint
/gsd:milestone-sprint --resume
```

**Additional features:**
- **Auto-discuss:** Claude ↔ Codex dialogue creates CONTEXT.md before planning
- **Audit:** Always runs `/gsd:audit-milestone` at the end
- **Complete:** Optional `--complete` flag archives the milestone

## The Codex Fix Loop

When Codex validates plans or reviews code, it returns one of:

| Response | Meaning | Action |
|----------|---------|--------|
| `[PROCEED]` | No issues | Continue to next step |
| `[HALT] warning: {issue}` | Fixable issue | Claude fixes → Codex re-reviews |
| `[HALT] critical: {issue}` | Unfixable | Halt immediately |

**Fix loop behavior:**

1. Codex reviews (plan or code)
2. If warning found → Claude fixer agent applies fix
3. Codex re-reviews
4. Repeat up to 3 times
5. If still failing after 3 attempts → Halt

```
Codex Review ──► [PROCEED] ──► Done ✓
     │
     ▼
  [HALT]? ──► critical ──► HALT (unfixable)
     │
     ▼ (warning)
Claude Fixer ──► Fix applied
     │
     ▼
Codex Re-review ◄──┐
     │             │
     ▼             │
  [PROCEED]?       │
  YES ──► Done ✓   │
  NO ───► attempt++│
          (max 3) ─┘
```

## Phase Lifecycle

Each phase goes through these steps:

```
┌─────────────────────────────────────────────────────────────────┐
│                      PHASE LIFECYCLE                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. CONTEXT (milestone-sprint only)                             │
│     └─► Claude proposes → Codex challenges → CONTEXT.md         │
│                                                                 │
│  2. PLAN                                                        │
│     └─► /gsd:plan-phase creates PLAN.md files                   │
│     └─► Codex validates plans (fix loop if needed)              │
│                                                                 │
│  3. EXECUTE                                                     │
│     └─► /gsd:execute-phase runs all tasks                       │
│     └─► Atomic commits after each significant change            │
│     └─► Creates SUMMARY.md for each plan                        │
│     └─► Codex reviews code (fix loop if needed)                 │
│                                                                 │
│  4. CHECKPOINT (interactive mode only)                          │
│     └─► User confirms before next phase                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Skills Called by Sprint

The sprint loop calls these GSD skills:

| Skill | Purpose | When Called |
|-------|---------|-------------|
| `/gsd:plan-phase` | Create PLAN.md files | If no plans exist |
| `/gsd:execute-phase` | Run tasks, create SUMMARY.md | Every phase |
| `/gsd:audit-milestone` | Verify requirements met | End of milestone-sprint |
| `/gsd:complete-milestone` | Archive milestone | If `--complete` flag |

## Sprint Signals

Claude emits signals that the bash loop interprets:

| Signal | Meaning | Loop Action |
|--------|---------|-------------|
| `[SPRINT:PLANNING_COMPLETE]` | Plans created | Proceed to Codex validation |
| `[SPRINT:PHASE_COMPLETE]` | Execution done | Proceed to next phase |
| `[SPRINT:FIX_COMPLETE]` | Fix applied | Re-run Codex review |
| `[SPRINT:VERIFICATION_FAILED]` | Gaps found | Halt sprint |
| `[SPRINT:CHECKPOINT]` | Human action needed | Halt (auth gates) or prompt |
| `[SPRINT:ERROR]` | Unrecoverable error | Halt sprint |

## State Files

Sprint progress is tracked in state files for resume capability:

**`/gsd:sprint`** → `.planning/SPRINT.md`
```yaml
---
started: "2024-01-25T10:30:00"
mode: yolo
start_phase: 3
end_phase: 6
current_phase: 4
status: running
---

## Log
| Phase | Duration | Codex | Result |
|-------|----------|-------|--------|
| 3 | 12m | ✓ | complete |
| 4 | ... | ... | running |
```

**`/gsd:milestone-sprint`** → `.planning/MILESTONE-SPRINT.md`
```yaml
---
started: "2024-01-25T10:30:00"
milestone: v1.2
milestone_name: "Org CRM"
mode: yolo
phase_range: "11-16"
current_phase: 13
status: running
auto_complete: true
---
```

## Halt Conditions

**Always halt (even in yolo mode):**
- `[HALT] critical` — security issues, major bugs
- Auth gates — credentials/API keys required
- Verification failures — gaps found by verifier
- Git errors, build failures
- Max fix attempts (3) exceeded

**Mode-dependent:**
- Codex warnings → Interactive: prompt, Yolo: fix loop
- Checkpoints → Interactive: wait, Yolo: continue (except auth)

## Modes Comparison

| Aspect | Interactive (default) | Yolo (`--yolo`) |
|--------|----------------------|-----------------|
| Between phases | Pause for confirmation | Auto-continue |
| Codex warnings | Fix loop, then prompt if stuck | Fix loop, continue if fixed |
| Uncertainties | Prompt user | Use reasonable defaults |
| Auth gates | Halt | Halt (always) |
| Critical issues | Halt | Halt (always) |

## Running in tmux

Sprints run in tmux sessions so you can detach and reattach:

```bash
# Check if running
tmux has-session -t gsd-sprint 2>/dev/null && echo 'Running' || echo 'Finished'

# Attach to watch
tmux attach -t gsd-sprint

# Detach (while attached)
Ctrl+b then d

# For milestone sprint
tmux attach -t gsd-milestone
```

## Examples

### Run a few phases interactively
```bash
/gsd:sprint 3 6
# Pause between each phase, review progress
```

### Run overnight
```bash
/gsd:milestone-sprint --yolo --complete
# Auto-continue, complete milestone, only halt on critical
```

### Resume after fixing an issue
```bash
# Sprint halted due to auth gate
# You add the API key
/gsd:sprint --resume
```

### Quick phase without validation
```bash
/gsd:sprint 5 5 --skip-codex
# Fast, but less safe
```

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          SPRINT ARCHITECTURE                          │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌─────────────────┐                                                  │
│  │   User Input    │  /gsd:sprint 3 6 --yolo                         │
│  └────────┬────────┘                                                  │
│           │                                                           │
│           ▼                                                           │
│  ┌─────────────────┐                                                  │
│  │   SKILL.md      │  Starts tmux session, runs sprint.sh            │
│  └────────┬────────┘                                                  │
│           │                                                           │
│           ▼                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                      sprint.sh (bash loop)                       │  │
│  │                                                                  │  │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │  │
│  │  │   Claude    │◄──►│   Codex     │◄──►│   Claude    │          │  │
│  │  │  (execute)  │    │ (validate)  │    │   (fixer)   │          │  │
│  │  └─────────────┘    └─────────────┘    └─────────────┘          │  │
│  │         │                                     │                  │  │
│  │         │         sprint-helpers.sh           │                  │  │
│  │         │  ┌──────────────────────────────┐   │                  │  │
│  │         │  │ - run_claude_streaming()     │   │                  │  │
│  │         │  │ - run_codex_fix_loop()       │   │                  │  │
│  │         │  │ - run_claude_fixer()         │   │                  │  │
│  │         │  │ - check_signals()            │   │                  │  │
│  │         │  │ - validate_plans_with_codex()│   │                  │  │
│  │         │  │ - review_code_with_codex()   │   │                  │  │
│  │         │  └──────────────────────────────┘   │                  │  │
│  │         │                                     │                  │  │
│  │         ▼                                     ▼                  │  │
│  │  ┌──────────────────────────────────────────────────────────┐   │  │
│  │  │                    .planning/                             │   │  │
│  │  │  ├── SPRINT.md          (state tracking)                  │   │  │
│  │  │  ├── ROADMAP.md         (phase definitions)               │   │  │
│  │  │  └── phases/                                              │   │  │
│  │  │      └── 03-feature/                                      │   │  │
│  │  │          ├── CONTEXT.md  (decisions)                      │   │  │
│  │  │          ├── PLAN.md     (tasks)                          │   │  │
│  │  │          └── SUMMARY.md  (results)                        │   │  │
│  │  └──────────────────────────────────────────────────────────┘   │  │
│  │                                                                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

## File Structure

```
.claude/skills/
├── gsd-sprint/
│   ├── SKILL.md              # Entry point for /gsd:sprint
│   ├── README.md             # This file
│   └── scripts/
│       ├── sprint.sh         # Main loop for phase range sprint
│       └── sprint-helpers.sh # Shared functions (fix loop, signals, etc.)
│
├── gsd-milestone-sprint/
│   ├── SKILL.md              # Entry point for /gsd:milestone-sprint
│   └── scripts/
│       ├── milestone-sprint.sh   # Main loop for milestone sprint
│       ├── milestone-helpers.sh  # Milestone-specific helpers
│       └── auto-discuss.sh       # Claude ↔ Codex dialogue
│
└── gsd/
    ├── plan-phase.md         # Called by sprint for planning
    ├── execute-phase.md      # Called by sprint for execution
    ├── audit-milestone.md    # Called by milestone-sprint
    └── complete-milestone.md # Called if --complete flag
```

## Troubleshooting

**Sprint stuck / not progressing:**
```bash
# Check tmux session
tmux attach -t gsd-sprint
# Look for waiting prompts or errors
```

**Codex keeps failing:**
```bash
# Check if it's a critical issue
cat .planning/SPRINT.md
# If max attempts exceeded, fix manually then resume
/gsd:sprint --resume
```

**Need to abort:**
```bash
# Kill the tmux session
tmux kill-session -t gsd-sprint
```

**Resume from checkpoint:**
```bash
# State is saved, just resume
/gsd:sprint --resume
# or
/gsd:milestone-sprint --resume
```

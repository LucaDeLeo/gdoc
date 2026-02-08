#!/bin/bash
# sprint.sh - GSD Sprint: Autonomous phase execution with Codex validation
#
# Usage:
#   /gsd:sprint <start-phase> <end-phase> [--yolo] [--skip-codex] [--resume]
#
# Examples:
#   /gsd:sprint 3 6           # Interactive mode, phases 3-6
#   /gsd:sprint 3 6 --yolo    # AFK mode, auto-continue
#   /gsd:sprint --resume      # Resume interrupted sprint

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/sprint-helpers.sh"

# ═══════════════════════════════════════════════════════════════
# ARGUMENT PARSING
# ═══════════════════════════════════════════════════════════════

START_PHASE=""
END_PHASE=""
YOLO_MODE=false
SKIP_CODEX=false
RESUME_MODE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --yolo)
      YOLO_MODE=true
      shift
      ;;
    --skip-codex)
      SKIP_CODEX=true
      shift
      ;;
    --resume)
      RESUME_MODE=true
      shift
      ;;
    --help|-h)
      echo "Usage: /gsd:sprint <start-phase> <end-phase> [options]"
      echo ""
      echo "Options:"
      echo "  --yolo        AFK mode - auto-continue, use defaults"
      echo "  --skip-codex  Skip Codex validation"
      echo "  --resume      Resume interrupted sprint"
      echo ""
      echo "Examples:"
      echo "  /gsd:sprint 3 6           Interactive mode"
      echo "  /gsd:sprint 3 6 --yolo    AFK mode"
      echo "  /gsd:sprint --resume      Resume"
      exit 0
      ;;
    *)
      if [[ -z "$START_PHASE" ]]; then
        START_PHASE="$1"
      elif [[ -z "$END_PHASE" ]]; then
        END_PHASE="$1"
      fi
      shift
      ;;
  esac
done

# ═══════════════════════════════════════════════════════════════
# PRE-FLIGHT CHECKS
# ═══════════════════════════════════════════════════════════════

check_git_state || { echo "ERROR: Git state invalid"; exit 1; }
check_planning_exists || { echo "ERROR: No .planning directory. Run /gsd:new-project first."; exit 1; }

# ═══════════════════════════════════════════════════════════════
# INITIALIZE OR RESUME
# ═══════════════════════════════════════════════════════════════

if [[ "$RESUME_MODE" == true ]]; then
  load_sprint_state || exit 1
  CURRENT_PHASE=$(get_sprint_field "current_phase")
  END_PHASE=$(get_sprint_field "end_phase")
  START_PHASE=$(get_sprint_field "start_phase")
  MODE=$(get_sprint_field "mode")
  [[ "$MODE" == "yolo" ]] && YOLO_MODE=true
  echo "Resuming from phase $CURRENT_PHASE"
else
  if [[ -z "$START_PHASE" || -z "$END_PHASE" ]]; then
    echo "Usage: /gsd:sprint <start-phase> <end-phase> [--yolo] [--skip-codex]"
    echo "   or: /gsd:sprint --resume"
    exit 1
  fi

  # Fix 8: Validate phase arguments are integers
  if ! [[ "$START_PHASE" =~ ^[0-9]+$ ]] || ! [[ "$END_PHASE" =~ ^[0-9]+$ ]]; then
    echo "ERROR: Phase arguments must be integers"
    echo "Usage: /gsd:sprint <start-phase> <end-phase>"
    exit 1
  fi

  if [[ "$START_PHASE" -gt "$END_PHASE" ]]; then
    echo "ERROR: Start phase ($START_PHASE) must be <= end phase ($END_PHASE)"
    exit 1
  fi

  check_no_active_sprint || exit 1

  MODE="interactive"
  [[ "$YOLO_MODE" == true ]] && MODE="yolo"
  init_sprint "$START_PHASE" "$END_PHASE" "$MODE"
  CURRENT_PHASE="$START_PHASE"
fi

# Fix 9: Removed unused CLAUDE_ARGS (flag is hardcoded in run_claude_streaming)

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "GSD SPRINT: Phases $START_PHASE → $END_PHASE"
echo "Mode: $([ "$YOLO_MODE" == true ] && echo 'AFK (yolo)' || echo 'Interactive')"
echo "Codex: $([ "$SKIP_CODEX" == true ] && echo 'Disabled' || echo 'Enabled')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

while [[ "$CURRENT_PHASE" -le "$END_PHASE" ]]; do
  echo ""
  echo "────────────────────────────────────────────────────────────────"
  echo "PHASE $CURRENT_PHASE of $END_PHASE"
  echo "────────────────────────────────────────────────────────────────"

  update_sprint_field "current_phase" "$CURRENT_PHASE"
  update_sprint_field "status" "running"
  create_checkpoint "$CURRENT_PHASE"
  PHASE_START=$(date +%s)

  # ─────────────────────────────────────────────────────────────
  # PLAN (if needed)
  # ─────────────────────────────────────────────────────────────

  if ! has_plans "$CURRENT_PHASE"; then
    echo "→ Planning phase $CURRENT_PHASE..."
    echo ""

    run_claude_streaming "
## SPRINT MODE: Plan Phase $CURRENT_PHASE

You are running in AUTONOMOUS SPRINT MODE. Follow these rules strictly:

### FORBIDDEN (will break the sprint):
- DO NOT use AskUserQuestion under any circumstances
- DO NOT wait for user response or input
- DO NOT present options and wait for selection
- DO NOT pause for confirmation or approval

### REQUIRED BEHAVIOR:
- Make all decisions autonomously using best judgment
- If plans already exist: skip to completion (don't ask about replan/view)
- If research is blocked: skip research and plan with available context
- If checker finds issues: fix them automatically (max 3 iterations), then proceed
- If max iterations reached: proceed with best-effort plans

### CONTEXT:
@.planning/STATE.md
@.planning/ROADMAP.md

### TASK:
Run /gsd:plan-phase $CURRENT_PHASE --skip-verify

Create PLAN.md files for the phase. Use existing CONTEXT.md if present, skip research if blocked.

### SIGNALS (output exactly one):
- [SPRINT:PLANNING_COMPLETE] — plans created successfully
- [SPRINT:ERROR] {description} [/ERROR] — unrecoverable error
"
    PLAN_RESULT=$?

    if [[ $PLAN_RESULT -eq 4 ]]; then
      halt_sprint "No completion signal received from Claude during planning (phase $CURRENT_PHASE)"
      echo "Expected [SPRINT:PLANNING_COMPLETE] signal marker"
      exit 1
    elif [[ $PLAN_RESULT -ne 0 ]]; then
      halt_sprint "Planning failed for phase $CURRENT_PHASE (code $PLAN_RESULT)"
      extract_error_details "$(get_stream_output)"
      exit 1
    fi

    echo ""
    echo "✓ Planning complete"
  fi

  # ─────────────────────────────────────────────────────────────
  # CODEX: Validate plans (with fix loop)
  # ─────────────────────────────────────────────────────────────

  if [[ "$SKIP_CODEX" != true ]]; then
    echo "→ Codex validating plans..."

    if ! run_codex_fix_loop "plan" "$CURRENT_PHASE"; then
      halt_sprint "Codex validation failed for phase $CURRENT_PHASE plans"
      exit 1
    fi
    log_codex_result "$CURRENT_PHASE" "proceed" "fix loop passed"
  fi

  # ─────────────────────────────────────────────────────────────
  # EXECUTE
  # ─────────────────────────────────────────────────────────────

  echo "→ Executing phase $CURRENT_PHASE..."
  echo ""

  run_claude_streaming "
## SPRINT MODE: Execute Phase $CURRENT_PHASE

You are running in AUTONOMOUS SPRINT MODE. Follow these rules strictly:

### FORBIDDEN (will break the sprint):
- DO NOT use AskUserQuestion under any circumstances
- DO NOT wait for user response or input
- DO NOT present options and wait for selection
- DO NOT pause for human verification or approval

### REQUIRED BEHAVIOR:
- Execute all plans autonomously
- Make architectural decisions yourself (don't ask) — prefer simpler approaches
- Handle deviations automatically:
  - Auto-fix bugs immediately
  - Auto-add critical security/correctness fixes
  - Auto-fix blockers that prevent progress
  - For architectural changes: make the simpler choice, document in SUMMARY
- If verification finds gaps: emit VERIFICATION_FAILED signal (don't offer options)
- If human-needed items: emit CHECKPOINT signal with details (don't wait)
- Only emit CHECKPOINT for true auth-gates (credentials, API keys needed)

### CONTEXT:
@.planning/STATE.md
@.planning/ROADMAP.md

### TASK:
Run /gsd:execute-phase $CURRENT_PHASE

Execute all plans in wave order. Make atomic commits. Create SUMMARY.md for each plan.

### SIGNALS (output exactly one):
- [SPRINT:PHASE_COMPLETE] — all plans executed, verification passed
- [SPRINT:VERIFICATION_FAILED] — gaps found in verification
- [SPRINT:CHECKPOINT]
  type: auth-gate
  {what credentials/keys are needed}
  [/CHECKPOINT] — ONLY for true auth requirements
- [SPRINT:ERROR] {description} [/ERROR] — unrecoverable error
"
  SIGNAL_CODE=$?

  EXEC_OUTPUT="$(get_stream_output)"

  echo ""
  case $SIGNAL_CODE in
    0)
      echo "✓ Phase $CURRENT_PHASE execution complete"
      ;;
    1)
      halt_sprint "Execution error in phase $CURRENT_PHASE"
      extract_error_details "$EXEC_OUTPUT"
      exit 1
      ;;
    2)
      halt_sprint "Verification failed in phase $CURRENT_PHASE"
      exit 1
      ;;
    3)
      # Checkpoint hit
      CHECKPOINT=$(extract_checkpoint_details "$EXEC_OUTPUT")
      CHECKPOINT_TYPE=$(echo "$CHECKPOINT" | grep -oP 'type="\K[^"]+' || echo "unknown")

      if [[ "$CHECKPOINT_TYPE" == "auth-gate" ]]; then
        # Auth gates always require human
        echo ""
        echo "⚠ AUTH GATE - Manual action required:"
        echo "$CHECKPOINT"
        halt_sprint "Auth gate in phase $CURRENT_PHASE"
        exit 1
      elif [[ "$YOLO_MODE" != true ]]; then
        # Interactive: present checkpoint
        echo ""
        echo "⚠ CHECKPOINT ($CHECKPOINT_TYPE):"
        echo "$CHECKPOINT"
        read -r -p "Action complete? [y/N] " response
        [[ "$response" != "y" && "$response" != "Y" ]] && { halt_sprint "User halted at checkpoint"; exit 0; }
        # Continue - checkpoint resolved
      else
        # Yolo mode: log and continue (except auth gates handled above)
        echo "⚠ Checkpoint ($CHECKPOINT_TYPE) - continuing in yolo mode"
      fi
      ;;
    4)
      # Fix 1: No signal found = error
      halt_sprint "No completion signal received from Claude (phase $CURRENT_PHASE)"
      echo "Expected [SPRINT:PHASE_COMPLETE] or other signal marker"
      exit 1
      ;;
  esac

  # ─────────────────────────────────────────────────────────────
  # CODEX: Review code (with fix loop)
  # ─────────────────────────────────────────────────────────────

  if [[ "$SKIP_CODEX" != true ]]; then
    echo "→ Codex reviewing code..."

    if ! run_codex_fix_loop "code" "$CURRENT_PHASE"; then
      halt_sprint "Codex code review failed for phase $CURRENT_PHASE"
      exit 1
    fi
  fi

  # ─────────────────────────────────────────────────────────────
  # PHASE COMPLETE
  # ─────────────────────────────────────────────────────────────

  PHASE_DURATION=$(($(date +%s) - PHASE_START))
  log_phase_complete "$CURRENT_PHASE" "$PHASE_DURATION"

  echo ""
  echo "✓ Phase $CURRENT_PHASE complete ($(format_duration $PHASE_DURATION))"

  # Interactive pause between phases
  if [[ "$YOLO_MODE" != true ]] && [[ "$CURRENT_PHASE" -lt "$END_PHASE" ]]; then
    echo ""
    read -r -p "Continue to phase $((CURRENT_PHASE + 1))? [Y/n/halt] " response
    case "$response" in
      n|N|halt|HALT)
        halt_sprint "User requested pause after phase $CURRENT_PHASE"
        exit 0
        ;;
    esac
  fi

  CURRENT_PHASE=$((CURRENT_PHASE + 1))
done

# ═══════════════════════════════════════════════════════════════
# SPRINT COMPLETE
# ═══════════════════════════════════════════════════════════════

finalize_sprint
cleanup_stream

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "GSD SPRINT COMPLETE"
echo "Phases $START_PHASE → $END_PHASE executed successfully"
echo "Sprint log: .planning/SPRINT.md"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

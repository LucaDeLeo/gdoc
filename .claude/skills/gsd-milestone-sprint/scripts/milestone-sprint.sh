#!/bin/bash
# milestone-sprint.sh - Run entire milestone autonomously with Codex validation
# Auto-detects current milestone, executes all phases, runs audit

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source helpers
source "$SCRIPT_DIR/milestone-helpers.sh"
source "$SCRIPT_DIR/auto-discuss.sh"

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

YOLO_MODE=true
SKIP_CODEX=false
RESUME_MODE=false
AUTO_COMPLETE=false
MILESTONE_ARG=""

# ─────────────────────────────────────────────────────────────────────────────
# Argument Parsing
# ─────────────────────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case $1 in
    --yolo)
      YOLO_MODE=true
      shift
      ;;
    --interactive)
      YOLO_MODE=false
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
    --complete)
      AUTO_COMPLETE=true
      shift
      ;;
    -*)
      echo "Unknown option: $1"
      echo "Usage: /gsd:milestone-sprint [milestone] [--interactive] [--skip-codex] [--resume] [--complete]"
      exit 1
      ;;
    *)
      MILESTONE_ARG="$1"
      shift
      ;;
  esac
done

# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight Checks
# ─────────────────────────────────────────────────────────────────────────────

check_git_state
check_planning_exists
ensure_phases_dir || exit 1

# ─────────────────────────────────────────────────────────────────────────────
# Initialize or Resume
# ─────────────────────────────────────────────────────────────────────────────

MILESTONE_NAME=""
START_PHASE=""
END_PHASE=""
CURRENT_PHASE=""

if [[ "$RESUME_MODE" == true ]]; then
  # Resume from existing state
  load_milestone_sprint_state

  MILESTONE_NAME=$(get_milestone_sprint_field "milestone_name")
  START_PHASE=$(get_milestone_sprint_field "phase_range" | cut -d'-' -f1)
  END_PHASE=$(get_milestone_sprint_field "phase_range" | cut -d'-' -f2)
  CURRENT_PHASE=$(get_milestone_sprint_field "current_phase")
  MODE=$(get_milestone_sprint_field "mode")
  AUTO_COMPLETE=$(get_milestone_sprint_field "auto_complete")

  if [[ "$MODE" == "yolo" ]]; then
    YOLO_MODE=true
  fi

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "RESUMING MILESTONE SPRINT"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "Milestone: $MILESTONE_NAME"
  echo "Current phase: $CURRENT_PHASE"
  echo "Mode: $(if [[ "$YOLO_MODE" == true ]]; then echo "yolo"; else echo "interactive"; fi)"
  echo ""

else
  # New milestone sprint
  check_no_active_milestone_sprint

  # Detect milestone
  MILESTONE_INFO=""
  if [[ -n "$MILESTONE_ARG" ]]; then
    MILESTONE_INFO=$(get_milestone_by_name "$MILESTONE_ARG")
  else
    MILESTONE_INFO=$(get_current_milestone)
  fi

  MILESTONE_NAME=$(echo "$MILESTONE_INFO" | cut -d'|' -f1)
  START_PHASE=$(echo "$MILESTONE_INFO" | cut -d'|' -f2)
  END_PHASE=$(echo "$MILESTONE_INFO" | cut -d'|' -f3)

  # Get remaining phases
  REMAINING=""
  REMAINING=$(get_remaining_phases "$START_PHASE" "$END_PHASE")

  if [[ -z "$REMAINING" ]]; then
    echo "All phases in milestone '$MILESTONE_NAME' are already complete."
    echo ""
    echo "Run audit: /gsd:audit-milestone $MILESTONE_NAME"
    exit 0
  fi

  CURRENT_PHASE=$(echo "$REMAINING" | awk '{print $1}')

  # Initialize state
  MODE=""
  if [[ "$YOLO_MODE" == true ]]; then
    MODE="yolo"
  else
    MODE="interactive"
  fi

  init_milestone_sprint "$MILESTONE_NAME" "$START_PHASE" "$END_PHASE" "$MODE" "$AUTO_COMPLETE"

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "MILESTONE SPRINT: $MILESTONE_NAME"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "Phases: $START_PHASE → $END_PHASE"
  echo "Remaining: $REMAINING"
  echo "Mode: $MODE"
  echo "Auto-complete: $AUTO_COMPLETE"
  echo ""
fi

# ─────────────────────────────────────────────────────────────────────────────
# Get Milestone Goal
# ─────────────────────────────────────────────────────────────────────────────

MILESTONE_GOAL=$(get_milestone_goal "$START_PHASE" "$END_PHASE")
if [[ -z "$MILESTONE_GOAL" ]]; then
  MILESTONE_GOAL="Complete milestone $MILESTONE_NAME"
fi

echo "Goal: $MILESTONE_GOAL"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Main Loop
# ─────────────────────────────────────────────────────────────────────────────

SPRINT_START_TIME=$(date +%s)
REMAINING_PHASES=$(get_remaining_phases "$START_PHASE" "$END_PHASE")

for PHASE in $REMAINING_PHASES; do
  PHASE_START_TIME=$(date +%s)

  echo ""
  echo "┌─────────────────────────────────────────────────────"
  echo "│ PHASE $PHASE"
  echo "└─────────────────────────────────────────────────────"
  echo ""

  update_milestone_sprint_field "current_phase" "$PHASE"

  # ─── AUTO-DISCUSS ───
  if ! has_context "$PHASE"; then
    STEP_START=$(date +%s)
    echo "→ Auto-discussing phase $PHASE..."
    echo ""

    auto_discuss_phase "$PHASE" "$MILESTONE_GOAL" "$YOLO_MODE"
    DISCUSS_RESULT=$?

    STEP_END=$(date +%s)
    STEP_DURATION=$(format_duration $((STEP_END - STEP_START)))

    if [[ $DISCUSS_RESULT -eq 2 ]]; then
      # Checkpoint - needs human review
      halt_milestone_sprint "Auto-discuss needs human review for phase $PHASE"
      exit 2
    fi

    echo "  ✓ Auto-discuss complete ($STEP_DURATION)"
    log_auto_discuss "$PHASE" "3" "see CONTEXT.md" "auto"
  else
    echo "→ Context exists for phase $PHASE, skipping auto-discuss"
  fi

  # ─── PLAN ───
  if ! has_plans "$PHASE"; then
    STEP_START=$(date +%s)
    echo ""
    echo "→ Planning phase $PHASE..."

    init_temp_file

    run_claude_streaming "
## SPRINT MODE: Plan Phase $PHASE

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
- Use CONTEXT.md in the phase directory for implementation decisions

### CONTEXT:
@.planning/ROADMAP.md
@.planning/PROJECT.md

### TASK:
Run /gsd:plan-phase $PHASE

Create PLAN.md files for the phase. Focus on actionable plans that can be executed autonomously.

### SIGNALS (output exactly one):
- [SPRINT:PLANNING_COMPLETE] — plans created successfully
- [SPRINT:ERROR] {description} [/ERROR] — unrecoverable error
"

    PLAN_SIGNAL=$?

    STEP_END=$(date +%s)
    STEP_DURATION=$(format_duration $((STEP_END - STEP_START)))

    case $PLAN_SIGNAL in
      0)
        echo "  ✓ Planning complete ($STEP_DURATION)"
        ;;
      1|2|3|4)
        halt_milestone_sprint "Planning failed for phase $PHASE (signal: $PLAN_SIGNAL)"
        exit 1
        ;;
    esac

    # Codex validates plans (with fix loop)
    if [[ "$SKIP_CODEX" != true ]]; then
      STEP_START=$(date +%s)
      echo ""
      echo "→ Codex validating plans..."

      if ! run_codex_fix_loop "plan" "$PHASE"; then
        log_codex_result "$PHASE" "plan" "FAIL" "fix loop failed"
        halt_milestone_sprint "Codex validation failed for phase $PHASE plans"
        exit 1
      fi

      STEP_END=$(date +%s)
      STEP_DURATION=$(format_duration $((STEP_END - STEP_START)))
      echo "  ✓ Codex plan validation complete ($STEP_DURATION)"
      log_codex_result "$PHASE" "plan" "OK" ""
    fi
  else
    echo "→ Plans exist for phase $PHASE, skipping planning"
  fi

  # ─── EXECUTE ───
  STEP_START=$(date +%s)
  echo ""
  echo "→ Executing phase $PHASE..."

  init_temp_file

  run_claude_streaming "
## SPRINT MODE: Execute Phase $PHASE

You are running in AUTONOMOUS SPRINT MODE. Follow these rules strictly:

### FORBIDDEN (will break the sprint):
- DO NOT use AskUserQuestion under any circumstances
- DO NOT wait for user response or input
- DO NOT present options and wait for selection
- DO NOT pause for human verification or approval

### REQUIRED BEHAVIOR:
- Execute all plans autonomously in wave order
- Make atomic commits after each significant change
- Follow the plans exactly - do not add scope or skip steps
- Make architectural decisions yourself (don't ask) — prefer simpler approaches
- Handle deviations automatically:
  - Auto-fix bugs immediately
  - Auto-add critical security/correctness fixes
  - Auto-fix blockers that prevent progress
  - For architectural changes: make the simpler choice, document in SUMMARY
- If verification finds gaps: emit VERIFICATION_FAILED signal (don't offer options)
- Only emit CHECKPOINT for true auth-gates (credentials, API keys needed)

### CONTEXT:
@.planning/ROADMAP.md
@.planning/PROJECT.md

### TASK:
Run /gsd:execute-phase $PHASE

Execute all plans. Create SUMMARY.md for each plan.

### SIGNALS (output exactly one):
- [SPRINT:PHASE_COMPLETE] — all plans executed, verification passed
- [SPRINT:VERIFICATION_FAILED] — gaps found in verification
- [SPRINT:CHECKPOINT]
  type: auth-gate
  {what credentials/keys are needed}
  [/CHECKPOINT] — ONLY for true auth requirements
- [SPRINT:ERROR] {description} [/ERROR] — unrecoverable error
"

  EXEC_SIGNAL=$?
  STEP_END=$(date +%s)
  STEP_DURATION=$(format_duration $((STEP_END - STEP_START)))

  case $EXEC_SIGNAL in
    0)
      echo "  ✓ Phase execution complete ($STEP_DURATION)"
      ;;
    1)
      ERROR_DETAILS=""
      ERROR_DETAILS=$(extract_error_details)
      halt_milestone_sprint "Execution error in phase $PHASE: $ERROR_DETAILS"
      exit 1
      ;;
    2)
      halt_milestone_sprint "Verification failed in phase $PHASE"
      exit 1
      ;;
    3)
      CHECKPOINT_DETAILS=""
      CHECKPOINT_DETAILS=$(extract_checkpoint_details)
      halt_milestone_sprint "Checkpoint in phase $PHASE: $CHECKPOINT_DETAILS"
      exit 2
      ;;
    4)
      halt_milestone_sprint "No completion signal from phase $PHASE execution"
      exit 1
      ;;
  esac

  # Codex reviews code (with fix loop)
  if [[ "$SKIP_CODEX" != true ]]; then
    STEP_START=$(date +%s)
    echo ""
    echo "→ Codex reviewing code..."

    if ! run_codex_fix_loop "code" "$PHASE"; then
      log_codex_result "$PHASE" "code" "FAIL" "fix loop failed"
      halt_milestone_sprint "Codex code review failed for phase $PHASE"
      exit 1
    fi

    STEP_END=$(date +%s)
    STEP_DURATION=$(format_duration $((STEP_END - STEP_START)))
    echo "  ✓ Codex code review complete ($STEP_DURATION)"
    log_codex_result "$PHASE" "code" "OK" ""
  fi

  # ─── PHASE COMPLETE ───
  PHASE_END_TIME=$(date +%s)
  PHASE_DURATION=$(format_duration $((PHASE_END_TIME - PHASE_START_TIME)))

  log_milestone_phase_complete "$PHASE" "$PHASE_DURATION" "OK" ""

  echo ""
  echo "✓ Phase $PHASE complete ($PHASE_DURATION)"

  # Interactive checkpoint between phases
  if [[ "$YOLO_MODE" != true ]]; then
    # Check if there are more phases
    NEXT_REMAINING=""
    NEXT_REMAINING=$(get_remaining_phases "$START_PHASE" "$END_PHASE")

    if [[ -n "$NEXT_REMAINING" ]]; then
      echo ""
      read -r -p "Continue to next phase? [Y/n/halt] " response < /dev/tty || response="y"
      case "$response" in
        [Nn]*)
          echo "Pausing. Resume with: /gsd:milestone-sprint --resume"
          exit 0
          ;;
        halt)
          halt_milestone_sprint "User requested halt after phase $PHASE"
          exit 0
          ;;
      esac
    fi
  fi
done

# ─────────────────────────────────────────────────────────────────────────────
# All Phases Complete - Run Audit
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "ALL PHASES COMPLETE - RUNNING AUDIT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

AUDIT_START=$(date +%s)
init_temp_file

run_claude_streaming "
## SPRINT MODE: Audit Milestone '$MILESTONE_NAME'

You are running in AUTONOMOUS SPRINT MODE. Follow these rules strictly:

### FORBIDDEN (will break the sprint):
- DO NOT use AskUserQuestion under any circumstances
- DO NOT wait for user response or input
- DO NOT present options and wait for selection
- DO NOT pause for confirmation or approval

### REQUIRED BEHAVIOR:
- Run the audit autonomously
- Check all requirements and success criteria
- Report results via signals only

### CONTEXT:
@.planning/ROADMAP.md
@.planning/PROJECT.md

### TASK:
Run /gsd:audit-milestone

Verify that all requirements for milestone '$MILESTONE_NAME' have been met.
Check each success criterion from the ROADMAP.md phase definitions.

### SIGNALS (output exactly one):
- [SPRINT:PHASE_COMPLETE] — audit passed, all requirements met
- [SPRINT:VERIFICATION_FAILED] — gaps found (list them before signal)
- [SPRINT:ERROR] {description} [/ERROR] — unrecoverable error
"

AUDIT_SIGNAL=$?
AUDIT_END=$(date +%s)
AUDIT_DURATION=$(format_duration $((AUDIT_END - AUDIT_START)))

case $AUDIT_SIGNAL in
  0)
    echo ""
    echo "✓ Audit passed ($AUDIT_DURATION)"
    ;;
  2)
    halt_milestone_sprint "Audit found gaps in milestone $MILESTONE_NAME"
    echo ""
    echo "Fix gaps and resume with: /gsd:milestone-sprint --resume"
    exit 1
    ;;
  *)
    halt_milestone_sprint "Audit failed for milestone $MILESTONE_NAME"
    exit 1
    ;;
esac

# ─────────────────────────────────────────────────────────────────────────────
# Completion Gate
# ─────────────────────────────────────────────────────────────────────────────

if [[ "$AUTO_COMPLETE" == true ]]; then
  echo ""
  echo "→ Completing milestone..."

  run_claude_streaming "
Run /gsd:complete-milestone $MILESTONE_NAME

Archive the milestone and update ROADMAP.md.
"

  echo ""
  echo "✓ Milestone '$MILESTONE_NAME' completed and archived"
else
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "MILESTONE PHASES COMPLETE"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "All phases executed and audit passed."
  echo ""
  echo "To finalize: /gsd:complete-milestone $MILESTONE_NAME"
  echo "Or re-run with --complete to auto-finalize"
  echo ""
fi

finalize_milestone_sprint

SPRINT_END_TIME=$(date +%s)
TOTAL_SPRINT_DURATION=$(format_duration $((SPRINT_END_TIME - SPRINT_START_TIME)))

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TOTAL SPRINT TIME: $TOTAL_SPRINT_DURATION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Milestone sprint log: .planning/MILESTONE-SPRINT.md"
echo ""

#!/bin/bash
# sprint-helpers.sh - State management and validation functions for GSD Sprint

# Enable nullglob for safe glob handling (Fix 2)
shopt -s nullglob

# Paths
PLANNING_DIR=".planning"
SPRINT_FILE="$PLANNING_DIR/SPRINT.md"
STATE_FILE="$PLANNING_DIR/STATE.md"
ROADMAP_FILE="$PLANNING_DIR/ROADMAP.md"
# Discover skills directory relative to this script (supports both local and global installs)
_HELPERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$(cd "$_HELPERS_DIR/../.." && pwd)"
CODEX_SCRIPT="$SKILLS_DIR/codex-oracle/scripts/ask_codex.sh"

# Temp file - initialized lazily (Fix 4)
STREAM_OUTPUT_FILE=""

# ═══════════════════════════════════════════════════════════════
# TEMP FILE MANAGEMENT (Fix 4)
# ═══════════════════════════════════════════════════════════════

init_temp_file() {
  if [[ -z "$STREAM_OUTPUT_FILE" ]]; then
    STREAM_OUTPUT_FILE=$(mktemp -t gsd-sprint)
    trap 'rm -f "$STREAM_OUTPUT_FILE"' EXIT
  fi
}

# ═══════════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════

init_sprint() {
  local START_PHASE=$1
  local END_PHASE=$2
  local MODE=${3:-interactive}

  local TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  cat > "$SPRINT_FILE" << EOF
---
started: "$TIMESTAMP"
mode: $MODE
start_phase: $START_PHASE
end_phase: $END_PHASE
current_phase: $START_PHASE
status: running
last_action: "Sprint initialized"
halt_reason: null
---

# Sprint: Phases $START_PHASE - $END_PHASE

## Progress

| Phase | Status | Duration | Codex | Notes |
|-------|--------|----------|-------|-------|
$(for p in $(seq $START_PHASE $END_PHASE); do echo "| $p | pending | - | - | - |"; done)

## Checkpoints

*(checkpoint details will be recorded here for resume)*

## Validation History

| Phase | Time | Claude | Codex | Outcome |
|-------|------|--------|-------|---------|

EOF

  echo "Sprint initialized: phases $START_PHASE-$END_PHASE ($MODE mode)"
}

get_sprint_field() {
  local FIELD=$1
  grep "^$FIELD:" "$SPRINT_FILE" 2>/dev/null | sed "s/^$FIELD:[[:space:]]*//" | tr -d '"'
}

# Fix 5: Escape YAML values
update_sprint_field() {
  local FIELD=$1
  local VALUE=$2

  # Escape sed special chars in value
  local ESCAPED_VALUE=$(printf '%s' "$VALUE" | sed 's/[&/\]/\\&/g; s/"/\\"/g')

  if grep -q "^$FIELD:" "$SPRINT_FILE" 2>/dev/null; then
    sed -i.bak "s|^$FIELD:.*|$FIELD: $ESCAPED_VALUE|" "$SPRINT_FILE" && rm -f "$SPRINT_FILE.bak"
  fi
}

get_current_phase() {
  # Try SPRINT.md first, fall back to STATE.md
  local PHASE=$(get_sprint_field "current_phase")

  if [[ -z "$PHASE" ]] && [[ -f "$STATE_FILE" ]]; then
    # Match "Phase: X of Y" format in STATE.md
    PHASE=$(grep "^Phase:" "$STATE_FILE" 2>/dev/null | sed 's/Phase: \([0-9.]*\).*/\1/' | head -1)
  fi

  echo "${PHASE:-1}"
}

# Fix 2: Safe glob handling
get_phase_status() {
  local PHASE=$1
  local PADDED=$(printf "%02d" "$PHASE" 2>/dev/null || echo "$PHASE")

  # Find phase dir safely with nullglob
  local dirs=(.planning/phases/${PADDED}-* .planning/phases/${PHASE}-*)
  if [[ ${#dirs[@]} -eq 0 ]]; then
    echo "not_found"
    return
  fi
  local PHASE_DIR="${dirs[0]}"

  # Check for plans and summaries safely
  local plans=("$PHASE_DIR"/*-PLAN.md)
  local summaries=("$PHASE_DIR"/*-SUMMARY.md)

  if [[ ${#plans[@]} -eq 0 ]]; then
    echo "needs_planning"
  elif [[ ${#summaries[@]} -lt ${#plans[@]} ]]; then
    echo "ready_to_execute"
  else
    echo "phase_complete"
  fi
}

# Fix 2: Safe glob handling
has_plans() {
  local PHASE=$1
  local PADDED=$(printf "%02d" "$PHASE" 2>/dev/null || echo "$PHASE")

  # Find phase dir safely
  local dirs=(.planning/phases/${PADDED}-* .planning/phases/${PHASE}-*)
  [[ ${#dirs[@]} -eq 0 ]] && return 1
  local PHASE_DIR="${dirs[0]}"

  # Check for plans safely
  local plans=("$PHASE_DIR"/*-PLAN.md)
  [[ ${#plans[@]} -gt 0 ]]
}

# ═══════════════════════════════════════════════════════════════
# CODEX VALIDATION
# ═══════════════════════════════════════════════════════════════

# Validate plans by pointing Codex at the phase directory (it can read files)
validate_plans_with_codex() {
  local PHASE=$1
  local PADDED=$(printf "%02d" "$PHASE" 2>/dev/null || echo "$PHASE")

  local dirs=(.planning/phases/${PADDED}-* .planning/phases/${PHASE}-*)
  if [[ ${#dirs[@]} -eq 0 ]]; then
    echo "[HALT] warning: Phase directory not found"
    return 1
  fi
  local PHASE_DIR="${dirs[0]}"

  local plans=("$PHASE_DIR"/*-PLAN.md)
  if [[ ${#plans[@]} -eq 0 ]]; then
    echo "[HALT] warning: No plans found in $PHASE_DIR"
    return 1
  fi

  # List plan files for Codex to read
  local PLAN_FILES=""
  for plan in "${plans[@]}"; do
    PLAN_FILES+="- $plan
"
  done

  local PROMPT="Sprint plan validation for Phase $PHASE.

Read and review these plan files:
$PLAN_FILES

Check EXACTLY these criteria - cover root issues only:
1. Achievability - Any impossible or underspecified tasks?
2. Completeness - Missing steps that would block execution?
3. Dependencies - Correct ordering? Missing prerequisites?
4. Risks - Technical risks not addressed?

Response format:
- If no issues: [PROCEED]
- If issues found: [HALT] followed by ALL issues you find, each on its own line"

  bash "$CODEX_SCRIPT" "$PROMPT" gpt-5.2-codex xhigh 300 brief
}

review_code_with_codex() {
  local PHASE=$1

  local COMMITS=$(git log --oneline -15 2>/dev/null | head -15)
  local DIFF_STAT=$(git diff HEAD~15..HEAD --stat 2>/dev/null | tail -20)

  local PROMPT="Sprint code review for Phase $PHASE.

Recent commits:
$COMMITS

Files changed:
$DIFF_STAT

Check for root issues - be specific with file:line:
1. Logic errors or bugs
2. Security vulnerabilities
3. Missing error handling
4. Code that doesn't achieve stated objective

Response format:
- If no issues: [PROCEED]
- If issues found: [HALT] followed by ALL issues you find, each on its own line with file:line reference"

  bash "$CODEX_SCRIPT" "$PROMPT" gpt-5.2-codex xhigh 300 brief
}

# Validate phase completion by pointing Codex at verification files
validate_phase_complete_with_codex() {
  local PHASE=$1
  local PADDED=$(printf "%02d" "$PHASE" 2>/dev/null || echo "$PHASE")

  local dirs=(.planning/phases/${PADDED}-* .planning/phases/${PHASE}-*)
  if [[ ${#dirs[@]} -eq 0 ]]; then
    echo "[HALT] warning: Phase directory not found"
    return 1
  fi
  local PHASE_DIR="${dirs[0]}"

  # List any verification files
  local VERIFICATION_FILES=""
  local verifications=("$PHASE_DIR"/*-VERIFICATION.md "$PHASE_DIR"/*-SUMMARY.md)
  for v in "${verifications[@]}"; do
    [[ -f "$v" ]] && VERIFICATION_FILES+="- $v
"
  done

  local PROMPT="Sprint validation for Phase $PHASE completion.

Phase directory: $PHASE_DIR
${VERIFICATION_FILES:+Read these files:
$VERIFICATION_FILES}

Recent commits: $(git log --oneline -10)
Files changed: $(git diff --stat HEAD~10 2>/dev/null | tail -10)

Check for root issues:
1. Code quality - bugs, security, anti-patterns?
2. Completeness - code matches deliverables?
3. Verification accuracy - agree with Claude?
4. Proceed safety - risks in continuing?

Response format:
- If no issues: [PROCEED]
- If issues found: [HALT] followed by ALL issues you find"

  bash "$CODEX_SCRIPT" "$PROMPT" gpt-5.2-codex xhigh 300 brief
}

# ═══════════════════════════════════════════════════════════════
# CODEX FIX LOOP
# ═══════════════════════════════════════════════════════════════

# Review CONTEXT.md with Codex (points to file, Codex reads it)
review_context_with_codex() {
  local PHASE="$1"
  local PADDED=$(printf "%02d" "$PHASE" 2>/dev/null || echo "$PHASE")

  local dirs=(.planning/phases/${PADDED}-* .planning/phases/${PHASE}-*)
  [[ ${#dirs[@]} -eq 0 ]] && { echo "[PROCEED]"; return 0; }
  local PHASE_DIR="${dirs[0]}"

  [[ ! -f "$PHASE_DIR/CONTEXT.md" ]] && { echo "[PROCEED]"; return 0; }

  bash "$CODEX_SCRIPT" "Review implementation context for Phase $PHASE.

Read this file: $PHASE_DIR/CONTEXT.md

Check for: contradictory decisions, missing considerations, misaligned goals, unrealistic assumptions.

Response format:
- If no issues: [PROCEED]
- If issues found: [HALT] followed by ALL issues you find" "gpt-5.2-codex" "high" "120" "brief" 2>/dev/null || echo "[PROCEED]"
}

# Invoke Claude to fix issues identified by Codex
run_claude_fixer() {
  local TYPE="$1"
  local PHASE="$2"
  local ISSUE="$3"

  local PADDED=$(printf "%02d" "$PHASE" 2>/dev/null || echo "$PHASE")
  local dirs=(.planning/phases/${PADDED}-* .planning/phases/${PHASE}-*)
  local PHASE_DIR="${dirs[0]:-}"

  local PROMPT=""
  local PREAMBLE="## SPRINT MODE: Fix Issues

AUTONOMOUS MODE - DO NOT use AskUserQuestion. Fix ALL issues directly.

"

  case "$TYPE" in
    plan)
      PROMPT="${PREAMBLE}@$PHASE_DIR

Fix these issues in the PLAN.md files for phase $PHASE:

$ISSUE

Read the plans, fix ALL issues listed above, keep changes minimal.
Make fixes autonomously - do not ask for clarification.

When done: [SPRINT:FIX_COMPLETE]"
      ;;
    code)
      PROMPT="${PREAMBLE}Fix these code issues identified by Codex for phase $PHASE:

$ISSUE

Fix ALL issues listed above. Commit with: 'fix: address Codex review feedback'
Make fixes autonomously - do not ask for clarification.

When done: [SPRINT:FIX_COMPLETE]"
      ;;
    context)
      PROMPT="${PREAMBLE}@$PHASE_DIR/CONTEXT.md

Improve CONTEXT.md for phase $PHASE based on Codex feedback:

$ISSUE

Address ALL concerns listed above. Keep decisions locked unless directly challenged.
Make fixes autonomously - do not ask for clarification.

When done: [SPRINT:FIX_COMPLETE]"
      ;;
  esac

  init_temp_file
  run_claude_streaming "$PROMPT"

  grep -q "\[SPRINT:FIX_COMPLETE\]" "$STREAM_OUTPUT_FILE" 2>/dev/null
}

# Run Codex validation with Claude fix loop
# Usage: run_codex_fix_loop TYPE PHASE
# Returns: 0=approved, 1=critical/unfixable, 2=max attempts exceeded
run_codex_fix_loop() {
  local TYPE="$1"
  local PHASE="$2"
  local MAX_FIX_ROUNDS=5
  local fix_round=0

  while true; do
    echo "  → Codex review (round $((fix_round + 1)))..."

    local CODEX_RESULT=""
    case "$TYPE" in
      plan)    CODEX_RESULT=$(validate_plans_with_codex "$PHASE" 2>&1) ;;
      code)    CODEX_RESULT=$(review_code_with_codex "$PHASE" 2>&1) ;;
      context) CODEX_RESULT=$(review_context_with_codex "$PHASE" 2>&1) ;;
    esac

    if echo "$CODEX_RESULT" | grep -q "\[PROCEED\]"; then
      echo "  ✓ Codex approved"
      return 0
    elif echo "$CODEX_RESULT" | grep -q "\[HALT\]"; then
      # Check if we've exhausted fix rounds
      if [[ $fix_round -ge $MAX_FIX_ROUNDS ]]; then
        echo "  ✗ Max fix rounds ($MAX_FIX_ROUNDS) exceeded"
        return 2
      fi

      # Extract everything after [HALT] - may be multiple issues
      local ISSUES
      ISSUES=$(echo "$CODEX_RESULT" | sed -n '/\[HALT\]/,$p' | sed '1s/.*\[HALT\] *//')
      local ISSUE_COUNT
      ISSUE_COUNT=$(echo "$ISSUES" | grep -c . || echo "1")
      echo "  ⚠ Found $ISSUE_COUNT issue(s):"
      echo "$ISSUES" | sed 's/^/    /'
      echo "  → Claude fixer applying fixes..."

      if run_claude_fixer "$TYPE" "$PHASE" "$ISSUES"; then
        echo "  ✓ Fixes applied, re-reviewing..."
        fix_round=$((fix_round + 1))
      else
        echo "  ✗ Fixer failed"
        return 1
      fi
    else
      # No clear signal - treat as pass
      echo "  ✓ Codex approved (no issues found)"
      return 0
    fi
  done
}

# ═══════════════════════════════════════════════════════════════
# STREAMING EXECUTION
# ═══════════════════════════════════════════════════════════════

# Run Claude with streaming output - displays in real-time and captures to file
# Usage: run_claude_streaming "prompt"
# Returns: exit code based on signals found
# Output captured in: $STREAM_OUTPUT_FILE
run_claude_streaming() {
  local PROMPT="$1"

  # Initialize temp file securely (Fix 4)
  init_temp_file

  # Clear output file
  > "$STREAM_OUTPUT_FILE"

  # Run Claude with stream-json and process output
  claude -p "$PROMPT" --dangerously-skip-permissions --output-format stream-json --verbose 2>&1 | \
  while IFS= read -r line; do
    # Try to parse as JSON and extract content
    if echo "$line" | jq -e '.type == "assistant"' &>/dev/null 2>&1; then
      # Main assistant message - show content
      content=$(echo "$line" | jq -r '.message.content[].text // empty' 2>/dev/null)
      if [[ -n "$content" ]]; then
        echo "$content"
        echo "$content" >> "$STREAM_OUTPUT_FILE"
      fi
    elif echo "$line" | jq -e '.type == "result"' &>/dev/null 2>&1; then
      # Final result - also capture
      result=$(echo "$line" | jq -r '.result // empty' 2>/dev/null)
      if [[ -n "$result" ]] && [[ ! -s "$STREAM_OUTPUT_FILE" ]]; then
        # Only use result if we didn't already capture content
        echo "$result"
        echo "$result" >> "$STREAM_OUTPUT_FILE"
      fi
    fi
  done

  # Check signals in captured output
  check_signals "$(cat "$STREAM_OUTPUT_FILE" 2>/dev/null)"
  return $?
}

# Get the captured output from last streaming run
get_stream_output() {
  cat "$STREAM_OUTPUT_FILE" 2>/dev/null
}

# Cleanup temp files
cleanup_stream() {
  rm -f "$STREAM_OUTPUT_FILE"
}

# ═══════════════════════════════════════════════════════════════
# SIGNAL HANDLING
# ═══════════════════════════════════════════════════════════════

# Fix 1: No signal = error (return 4)
check_signals() {
  local OUTPUT="$1"

  # Success signals (bracket format to avoid XML issues)
  if echo "$OUTPUT" | grep -q "\[SPRINT:PHASE_COMPLETE\]"; then
    return 0
  fi
  if echo "$OUTPUT" | grep -q "\[SPRINT:PLANNING_COMPLETE\]"; then
    return 0
  fi
  if echo "$OUTPUT" | grep -q "\[SPRINT:FIX_COMPLETE\]"; then
    return 0
  fi

  # Halt signals
  if echo "$OUTPUT" | grep -q "\[SPRINT:ERROR\]"; then
    return 1
  fi
  if echo "$OUTPUT" | grep -q "\[SPRINT:VERIFICATION_FAILED\]"; then
    return 2
  fi
  if echo "$OUTPUT" | grep -q "\[SPRINT:CHECKPOINT\]"; then
    return 3
  fi

  # NO SIGNAL FOUND = ERROR (Fix 1)
  return 4
}

extract_checkpoint_details() {
  local OUTPUT="$1"
  # Extract checkpoint block between markers
  echo "$OUTPUT" | sed -n '/\[SPRINT:CHECKPOINT\]/,/\[\/CHECKPOINT\]/p' | head -10
}

extract_error_details() {
  local OUTPUT="$1"
  # Extract error block between markers
  echo "$OUTPUT" | sed -n '/\[SPRINT:ERROR\]/,/\[\/ERROR\]/p' | head -5
}

# ═══════════════════════════════════════════════════════════════
# CHECKPOINT/RESUME
# ═══════════════════════════════════════════════════════════════

create_checkpoint() {
  local PHASE=$1
  local TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  local GIT_REF=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
  local STATE_HASH=$(md5sum "$STATE_FILE" 2>/dev/null | cut -d' ' -f1 || echo "unknown")

  # Append to checkpoints section
  cat >> "$SPRINT_FILE" << EOF

### Phase $PHASE Checkpoint
- timestamp: $TIMESTAMP
- git_ref: $GIT_REF
- state_hash: $STATE_HASH
EOF
}

load_sprint_state() {
  if [[ ! -f "$SPRINT_FILE" ]]; then
    echo "No SPRINT.md found. Cannot resume."
    return 1
  fi

  local STATUS=$(get_sprint_field "status")
  if [[ "$STATUS" == "complete" ]]; then
    echo "Sprint already complete."
    return 1
  fi

  echo "Resuming sprint from phase $(get_sprint_field current_phase)"
  return 0
}

# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

log_phase_complete() {
  local PHASE=$1
  local DURATION=$2
  local TIMESTAMP=$(date +"%H:%M")

  # Update progress table in SPRINT.md
  sed -i.bak "s/| $PHASE | pending |/| $PHASE | complete |/" "$SPRINT_FILE" && rm -f "$SPRINT_FILE.bak"
  sed -i.bak "s/| $PHASE | running |/| $PHASE | complete |/" "$SPRINT_FILE" && rm -f "$SPRINT_FILE.bak"

  update_sprint_field "last_action" "Phase $PHASE completed"
}

# Fix 7: Sanitize log output
log_codex_result() {
  local PHASE=$1
  local OUTCOME=$2
  local RESULT=$3
  local TIMESTAMP=$(date +"%H:%M")

  # Sanitize: remove pipes, newlines, truncate
  local SAFE_RESULT=$(echo "$RESULT" | tr '|\n' '  ' | cut -c1-50)

  echo "| $PHASE | $TIMESTAMP | passed | $OUTCOME | $SAFE_RESULT |" >> "$SPRINT_FILE"
}

halt_sprint() {
  local REASON=$1

  update_sprint_field "status" "halted"
  update_sprint_field "halt_reason" "\"$REASON\""

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "SPRINT HALTED"
  echo "Reason: $REASON"
  echo "Resume with: /gsd:sprint --resume"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

finalize_sprint() {
  update_sprint_field "status" "complete"
  update_sprint_field "last_action" "Sprint completed successfully"
}

# ═══════════════════════════════════════════════════════════════
# PRE-FLIGHT CHECKS
# ═══════════════════════════════════════════════════════════════

check_git_state() {
  if ! git rev-parse --git-dir &>/dev/null; then
    echo "Not a git repository"
    return 1
  fi

  # Check for merge conflicts
  if git ls-files -u | grep -q .; then
    echo "Git has unresolved merge conflicts"
    return 1
  fi

  return 0
}

check_planning_exists() {
  [[ -d "$PLANNING_DIR" ]] && [[ -f "$STATE_FILE" || -f "$ROADMAP_FILE" ]]
}

check_no_active_sprint() {
  if [[ -f "$SPRINT_FILE" ]]; then
    local STATUS=$(get_sprint_field "status")
    if [[ "$STATUS" == "running" ]]; then
      echo "Sprint already running. Use --resume or delete SPRINT.md"
      return 1
    fi
  fi
  return 0
}

# ═══════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════

format_duration() {
  local SECONDS=$1
  local MINUTES=$((SECONDS / 60))
  local SECS=$((SECONDS % 60))

  if [[ $MINUTES -gt 0 ]]; then
    echo "${MINUTES}m ${SECS}s"
  else
    echo "${SECS}s"
  fi
}

pause_for_review() {
  echo ""
  read -r -p "Continue to next phase? [Y/n/halt] " response
  case "$response" in
    n|N|halt|HALT)
      halt_sprint "User requested pause"
      exit 0
      ;;
  esac
}

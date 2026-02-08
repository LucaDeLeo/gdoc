#!/bin/bash
# milestone-helpers.sh - Helper functions for milestone-sprint
# Sources sprint-helpers.sh for common functionality

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Source common sprint helpers
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Discover skills directory relative to this script (supports both local and global installs)
SKILLS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SPRINT_HELPERS="$SKILLS_DIR/gsd-sprint/scripts/sprint-helpers.sh"
CODEX_SCRIPT="$SKILLS_DIR/codex-oracle/scripts/ask_codex.sh"
AUTO_DISCUSS_SCRIPT="$SCRIPT_DIR/auto-discuss.sh"

if [[ -f "$SPRINT_HELPERS" ]]; then
  source "$SPRINT_HELPERS"
else
  echo "ERROR: sprint-helpers.sh not found at $SPRINT_HELPERS"
  echo "Please ensure gsd-sprint skill is installed."
  exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase Number Normalization
# ─────────────────────────────────────────────────────────────────────────────

# Normalize phase number to zero-padded format (1 -> 01, 2.1 -> 02.1, 1.1.1 -> 01.1.1)
# This is THE canonical format - all directory names use this
normalize_phase() {
  local PHASE="$1"
  if [[ "$PHASE" =~ ^[0-9]+$ ]]; then
    printf "%02d" "$PHASE"
  elif [[ "$PHASE" =~ ^([0-9]+)(\.[0-9]+)+$ ]]; then
    # Handle multi-level decimals (1.1, 1.1.1, etc.)
    local major="${PHASE%%.*}"
    local rest="${PHASE#*.}"
    printf "%02d.%s" "$major" "$rest"
  else
    printf "%s" "$PHASE"
  fi
}

# Find phase directory - handles both padded (01-*) and unpadded (1-*) lookups
# Returns the directory path if found, empty string otherwise
find_phase_dir() {
  local PHASE="$1"
  local PLANNING_DIR="${2:-.planning/phases}"

  # Ensure directory exists
  [[ ! -d "$PLANNING_DIR" ]] && return 0

  local NORMALIZED
  NORMALIZED=$(normalize_phase "$PHASE")

  # Try normalized (zero-padded) first - this is the canonical format
  local DIR
  DIR=$(find "$PLANNING_DIR" -maxdepth 1 -type d -name "${NORMALIZED}-*" 2>/dev/null | head -1)

  if [[ -n "$DIR" ]]; then
    echo "$DIR"
    return 0
  fi

  # Fallback: try unpadded format for backwards compatibility
  if [[ "$PHASE" != "$NORMALIZED" ]]; then
    DIR=$(find "$PLANNING_DIR" -maxdepth 1 -type d -name "${PHASE}-*" 2>/dev/null | head -1)
    if [[ -n "$DIR" ]]; then
      echo "$DIR"
      return 0
    fi
  fi

  return 0
}

# Ensure .planning/phases directory exists
ensure_phases_dir() {
  if ! mkdir -p ".planning/phases" 2>/dev/null; then
    echo "ERROR: Failed to create .planning/phases directory" >&2
    return 1
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Milestone Detection
# ─────────────────────────────────────────────────────────────────────────────

# Parse ROADMAP.md to find current milestone (marked with 🚧)
# Returns: "name|start|end" (e.g., "v2.0 Mobile + Tauri|21|26")
get_current_milestone() {
  local ROADMAP=".planning/ROADMAP.md"

  if [[ ! -f "$ROADMAP" ]]; then
    echo "ERROR: ROADMAP.md not found" >&2
    return 1
  fi

  # Find line with 🚧 marker (in progress)
  local MILESTONE_LINE
  MILESTONE_LINE=$(grep "^- 🚧" "$ROADMAP" 2>/dev/null | head -1) || true

  if [[ -z "$MILESTONE_LINE" ]]; then
    echo "ERROR: No in-progress milestone found (look for 🚧 marker)" >&2
    return 1
  fi

  # Extract milestone name (text between ** markers)
  local NAME
  NAME=$(echo "$MILESTONE_LINE" | sed -E 's/.*\*\*([^*]+)\*\*.*/\1/')

  # Extract phase range (e.g., "Phases 21-23, 25-26")
  local PHASE_TEXT
  PHASE_TEXT=$(echo "$MILESTONE_LINE" | sed -E 's/.*Phases? ([0-9, -]+).*/\1/')

  # Parse phase range to find start and end
  local START END
  START=$(echo "$PHASE_TEXT" | grep -oE '[0-9]+' | head -1)
  END=$(echo "$PHASE_TEXT" | grep -oE '[0-9]+' | tail -1)

  if [[ -z "$START" || -z "$END" ]]; then
    echo "ERROR: Could not parse phase range from: $MILESTONE_LINE" >&2
    return 1
  fi

  echo "${NAME}|${START}|${END}"
}

# Get specific milestone by name
# Usage: get_milestone_by_name "v1.2"
get_milestone_by_name() {
  local TARGET_NAME="$1"
  local ROADMAP=".planning/ROADMAP.md"

  if [[ ! -f "$ROADMAP" ]]; then
    echo "ERROR: ROADMAP.md not found" >&2
    return 1
  fi

  # Find line matching milestone name (with or without status marker)
  local MILESTONE_LINE
  MILESTONE_LINE=$(grep -E "^- .* \*\*$TARGET_NAME" "$ROADMAP" | head -1) || true

  if [[ -z "$MILESTONE_LINE" ]]; then
    # Try partial match
    MILESTONE_LINE=$(grep -E "^- .* \*\*[^*]*$TARGET_NAME[^*]*\*\*" "$ROADMAP" | head -1) || true
  fi

  if [[ -z "$MILESTONE_LINE" ]]; then
    echo "ERROR: Milestone '$TARGET_NAME' not found in ROADMAP.md" >&2
    return 1
  fi

  # Extract full name and phase range
  local NAME
  NAME=$(echo "$MILESTONE_LINE" | sed -E 's/.*\*\*([^*]+)\*\*.*/\1/')

  local PHASE_TEXT
  PHASE_TEXT=$(echo "$MILESTONE_LINE" | sed -E 's/.*Phases? ([0-9, -]+).*/\1/')

  local START END
  START=$(echo "$PHASE_TEXT" | grep -oE '[0-9]+' | head -1)
  END=$(echo "$PHASE_TEXT" | grep -oE '[0-9]+' | tail -1)

  if [[ -z "$START" || -z "$END" ]]; then
    echo "ERROR: Could not parse phase range for milestone '$TARGET_NAME'" >&2
    return 1
  fi

  echo "${NAME}|${START}|${END}"
}

# Get all phases within a milestone range (handles decimal phases like 11.1)
# Usage: get_milestone_phases 11 16
# Returns: space-separated list of phase numbers
# Always includes full integer range PLUS any decimal phases found in directories
get_milestone_phases() {
  local START="$1"
  local END="$2"
  local PLANNING_DIR=".planning/phases"

  # Always start with the full integer range
  local ALL_PHASES
  ALL_PHASES=$(seq "$START" "$END")

  # If phases directory exists, also find any decimal phases (like 2.1, 3.5)
  if [[ -d "$PLANNING_DIR" ]]; then
    local DECIMAL_PHASES
    DECIMAL_PHASES=$(ls "$PLANNING_DIR" 2>/dev/null | \
      grep -E '^[0-9]+\.[0-9]+' | \
      sed 's/-.*$//' | \
      awk -v start="$START" -v end="$END" '
        {
          split($1, parts, ".")
          major = parts[1] + 0
          if (major >= start && major <= end) {
            print $1
          }
        }
      ') || true

    if [[ -n "$DECIMAL_PHASES" ]]; then
      ALL_PHASES=$(echo -e "$ALL_PHASES\n$DECIMAL_PHASES")
    fi
  fi

  # Sort numerically (handles both integers and decimals)
  echo "$ALL_PHASES" | sort -t. -k1,1n -k2,2n | uniq
}

# Get phases still needing execution
# A phase is complete if:
#   - It has VERIFICATION.md (explicit completion marker), OR
#   - Has SUMMARY.md files (work was done and plans may have been cleaned up), OR
#   - All PLAN.md files have corresponding SUMMARY.md files
# Usage: get_remaining_phases 11 16
get_remaining_phases() {
  local START="$1"
  local END="$2"
  local PLANNING_DIR=".planning/phases"

  local ALL_PHASES
  ALL_PHASES=$(get_milestone_phases "$START" "$END")

  local REMAINING=""
  for PHASE in $ALL_PHASES; do
    local NORMALIZED
    NORMALIZED=$(normalize_phase "$PHASE")

    local PHASE_DIR
    PHASE_DIR=$(find_phase_dir "$PHASE" "$PLANNING_DIR")

    if [[ -z "$PHASE_DIR" ]]; then
      # Phase directory doesn't exist yet - use normalized format
      REMAINING="$REMAINING $NORMALIZED"
      continue
    fi

    # Check for explicit completion marker
    if [[ -f "$PHASE_DIR/${NORMALIZED}-VERIFICATION.md" ]] || [[ -f "$PHASE_DIR/VERIFICATION.md" ]]; then
      # Phase is verified complete
      continue
    fi

    # Count plans and summaries
    local PLAN_COUNT=0
    local SUMMARY_COUNT=0

    for plan in "$PHASE_DIR"/*-PLAN.md; do
      [[ -f "$plan" ]] && PLAN_COUNT=$((PLAN_COUNT + 1))
    done

    for summary in "$PHASE_DIR"/*-SUMMARY.md; do
      [[ -f "$summary" ]] && SUMMARY_COUNT=$((SUMMARY_COUNT + 1))
    done

    # Phase is complete if:
    # - No plans but has summaries (plans were cleaned up), OR
    # - All plans have corresponding summaries
    if [[ $PLAN_COUNT -eq 0 && $SUMMARY_COUNT -gt 0 ]]; then
      # Plans cleaned up, work was done
      continue
    elif [[ $PLAN_COUNT -gt 0 ]]; then
      # Check each plan has a summary
      local ALL_HAVE_SUMMARIES=true
      for plan in "$PHASE_DIR"/*-PLAN.md; do
        if [[ -f "$plan" ]]; then
          local summary="${plan%-PLAN.md}-SUMMARY.md"
          if [[ ! -f "$summary" ]]; then
            ALL_HAVE_SUMMARIES=false
            break
          fi
        fi
      done

      if [[ "$ALL_HAVE_SUMMARIES" == true ]]; then
        continue
      fi
    fi

    # Phase is incomplete - use normalized format
    REMAINING="$REMAINING $NORMALIZED"
  done

  echo "$REMAINING" | xargs
}

# Check if phase has CONTEXT.md (from discuss or auto-discuss)
has_context() {
  local PHASE="$1"
  local PHASE_DIR
  PHASE_DIR=$(find_phase_dir "$PHASE")

  if [[ -n "$PHASE_DIR" && -f "$PHASE_DIR/CONTEXT.md" ]]; then
    return 0
  fi
  return 1
}

# Check if phase has PLAN.md files
has_plans() {
  local PHASE="$1"
  local PHASE_DIR
  PHASE_DIR=$(find_phase_dir "$PHASE")

  if [[ -n "$PHASE_DIR" ]]; then
    # Use find instead of glob to avoid nullglob issues
    local PLAN_COUNT
    PLAN_COUNT=$(find "$PHASE_DIR" -maxdepth 1 -name "*-PLAN.md" -type f 2>/dev/null | wc -l | tr -d ' ')
    [[ "$PLAN_COUNT" -gt 0 ]] && return 0
  fi
  return 1
}

# Get milestone goal from ROADMAP.md
get_milestone_goal() {
  local START="$1"
  local END="$2"
  local ROADMAP=".planning/ROADMAP.md"

  # Find the Phase Details section for the first phase in range
  local GOAL
  GOAL=$(awk -v start="$START" '
    /^### Phase '"$START"':/ { found=1; next }
    found && /^\*\*Goal\*\*:/ {
      gsub(/^\*\*Goal\*\*: */, "")
      print
      exit
    }
    found && /^###/ { exit }
  ' "$ROADMAP")

  if [[ -z "$GOAL" ]]; then
    # Fallback: try to get from Overview section
    GOAL=$(awk '
      /^## Overview/ { found=1; next }
      found && /^##/ { exit }
      found && NF > 0 { print; exit }
    ' "$ROADMAP")
  fi

  echo "$GOAL"
}

# ─────────────────────────────────────────────────────────────────────────────
# Milestone Sprint State Management
# ─────────────────────────────────────────────────────────────────────────────

MILESTONE_SPRINT_FILE=".planning/MILESTONE-SPRINT.md"

# Initialize milestone sprint state file
init_milestone_sprint() {
  local MILESTONE_NAME="$1"
  local START="$2"
  local END="$3"
  local MODE="$4"
  local AUTO_COMPLETE="${5:-false}"

  local REMAINING
  REMAINING=$(get_remaining_phases "$START" "$END")

  local TIMESTAMP
  TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  cat > "$MILESTONE_SPRINT_FILE" << EOF
---
started: "$TIMESTAMP"
milestone: $(echo "$MILESTONE_NAME" | cut -d' ' -f1)
milestone_name: "$MILESTONE_NAME"
mode: $MODE
phase_range: "$START-$END"
current_phase: $(echo "$REMAINING" | awk '{print $1}')
phases_remaining: [$REMAINING]
status: running
auto_complete: $AUTO_COMPLETE
---

# Milestone Sprint: $MILESTONE_NAME

## Progress

| Phase | Status | Duration | Codex | Notes |
|-------|--------|----------|-------|-------|
EOF

  # Add rows for each phase
  local ALL_PHASES
  ALL_PHASES=$(get_milestone_phases "$START" "$END")

  for PHASE in $ALL_PHASES; do
    local STATUS="pending"
    local NORMALIZED_PHASE
    NORMALIZED_PHASE=$(normalize_phase "$PHASE")
    local PHASE_DIR
    PHASE_DIR=$(find_phase_dir "$PHASE")

    if [[ -n "$PHASE_DIR" && -f "$PHASE_DIR/SUMMARY.md" ]]; then
      STATUS="complete"
    fi

    echo "| $NORMALIZED_PHASE | $STATUS | - | - | - |" >> "$MILESTONE_SPRINT_FILE"
  done

  cat >> "$MILESTONE_SPRINT_FILE" << EOF

## Validation History

| Phase | Step | Result | Notes |
|-------|------|--------|-------|

## Auto-Discuss Log

| Phase | Rounds | Flagged | Resolution |
|-------|--------|---------|------------|

---
*Started: $TIMESTAMP*
EOF
}

# Get field from milestone sprint state
get_milestone_sprint_field() {
  local FIELD="$1"

  if [[ ! -f "$MILESTONE_SPRINT_FILE" ]]; then
    echo ""
    return
  fi

  awk -v field="$FIELD" '
    /^---$/ { if (in_front) exit; in_front=1; next }
    in_front && $0 ~ "^"field":" {
      sub(/^[^:]+: *"?/, "")
      sub(/"? *$/, "")
      print
    }
  ' "$MILESTONE_SPRINT_FILE"
}

# Update field in milestone sprint state
update_milestone_sprint_field() {
  local FIELD="$1"
  local VALUE="$2"

  if [[ ! -f "$MILESTONE_SPRINT_FILE" ]]; then
    return 1
  fi

  # Escape special characters for sed
  local ESCAPED_VALUE
  ESCAPED_VALUE=$(echo "$VALUE" | sed 's/[&/\]/\\&/g')

  # Check if value should be quoted
  if [[ "$VALUE" =~ ^[0-9]+$ ]] || [[ "$VALUE" == "true" ]] || [[ "$VALUE" == "false" ]] || [[ "$VALUE" =~ ^\[ ]]; then
    sed -i.bak "s/^$FIELD: .*/$FIELD: $ESCAPED_VALUE/" "$MILESTONE_SPRINT_FILE"
  else
    sed -i.bak "s/^$FIELD: .*/$FIELD: \"$ESCAPED_VALUE\"/" "$MILESTONE_SPRINT_FILE"
  fi
  rm -f "$MILESTONE_SPRINT_FILE.bak"
}

# Load milestone sprint state for resume
load_milestone_sprint_state() {
  if [[ ! -f "$MILESTONE_SPRINT_FILE" ]]; then
    echo "ERROR: No milestone sprint state found at $MILESTONE_SPRINT_FILE" >&2
    return 1
  fi

  local STATUS
  STATUS=$(get_milestone_sprint_field "status")

  if [[ "$STATUS" == "complete" ]]; then
    echo "ERROR: Milestone sprint already completed" >&2
    return 1
  fi

  echo "Resuming milestone sprint..."
  echo "  Milestone: $(get_milestone_sprint_field "milestone_name")"
  echo "  Current phase: $(get_milestone_sprint_field "current_phase")"
  echo "  Phases remaining: $(get_milestone_sprint_field "phases_remaining")"
}

# Log phase completion in milestone sprint
log_milestone_phase_complete() {
  local PHASE="$1"
  local DURATION="$2"
  local CODEX_RESULT="${3:-}"
  local NOTES="${4:-}"

  if [[ ! -f "$MILESTONE_SPRINT_FILE" ]]; then
    return
  fi

  # Update progress table
  sed -i.bak "s/| $PHASE | [^|]* | [^|]* | [^|]* | [^|]* |/| $PHASE | complete | $DURATION | $CODEX_RESULT | $NOTES |/" "$MILESTONE_SPRINT_FILE"
  rm -f "$MILESTONE_SPRINT_FILE.bak"
}

# Log auto-discuss results
log_auto_discuss() {
  local PHASE="$1"
  local ROUNDS="$2"
  local FLAGGED="$3"
  local RESOLUTION="$4"

  if [[ ! -f "$MILESTONE_SPRINT_FILE" ]]; then
    return
  fi

  # Append to auto-discuss log table
  sed -i.bak "/^## Auto-Discuss Log/,/^---/{
    /^| Phase | Rounds/a\\
| $PHASE | $ROUNDS | $FLAGGED | $RESOLUTION |
  }" "$MILESTONE_SPRINT_FILE" 2>/dev/null || true
  rm -f "$MILESTONE_SPRINT_FILE.bak"
}

# Halt milestone sprint
halt_milestone_sprint() {
  local REASON="$1"

  update_milestone_sprint_field "status" "halted"
  update_milestone_sprint_field "halt_reason" "$REASON"

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "MILESTONE SPRINT HALTED"
  echo ""
  echo "Reason: $REASON"
  echo ""
  echo "Resume with: /gsd:milestone-sprint --resume"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# Finalize milestone sprint
finalize_milestone_sprint() {
  update_milestone_sprint_field "status" "complete"

  local TIMESTAMP
  TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  echo "" >> "$MILESTONE_SPRINT_FILE"
  echo "*Completed: $TIMESTAMP*" >> "$MILESTONE_SPRINT_FILE"
}

# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight Checks
# ─────────────────────────────────────────────────────────────────────────────

check_no_active_milestone_sprint() {
  if [[ -f "$MILESTONE_SPRINT_FILE" ]]; then
    local STATUS
    STATUS=$(get_milestone_sprint_field "status")

    if [[ "$STATUS" == "running" || "$STATUS" == "halted" ]]; then
      local MILESTONE
      MILESTONE=$(get_milestone_sprint_field "milestone_name")
      echo "ERROR: Active milestone sprint exists for '$MILESTONE'" >&2
      echo "Use --resume to continue or delete $MILESTONE_SPRINT_FILE to start fresh" >&2
      return 1
    fi
  fi
  return 0
}

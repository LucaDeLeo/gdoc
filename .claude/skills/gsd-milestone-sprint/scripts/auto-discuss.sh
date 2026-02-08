#!/bin/bash
# auto-discuss.sh - Claude ↔ Codex dialogue for phase discussion
# Replaces human Q&A with AI conversation that makes decisions autonomously

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Discover skills directory relative to this script (supports both local and global installs)
SKILLS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CODEX_SCRIPT="$SKILLS_DIR/codex-oracle/scripts/ask_codex.sh"

# Source sprint-helpers for fix loop functions
if [[ -f "$SKILLS_DIR/gsd-sprint/scripts/sprint-helpers.sh" ]]; then
  source "$SKILLS_DIR/gsd-sprint/scripts/sprint-helpers.sh"
fi

# Source milestone-helpers for phase normalization functions
MILESTONE_HELPERS="$SCRIPT_DIR/milestone-helpers.sh"
if [[ -f "$MILESTONE_HELPERS" ]]; then
  source "$MILESTONE_HELPERS"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Auto-Discuss Phase
# ─────────────────────────────────────────────────────────────────────────────

# Main entry point for auto-discussion
# Usage: auto_discuss_phase PHASE MILESTONE_GOAL YOLO_MODE
auto_discuss_phase() {
  local PHASE="$1"
  local MILESTONE_GOAL="$2"
  local YOLO_MODE="${3:-false}"

  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "AUTO-DISCUSS: Phase $PHASE"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""

  local PLANNING_DIR=".planning"
  local ROADMAP="$PLANNING_DIR/ROADMAP.md"
  local PROJECT="$PLANNING_DIR/PROJECT.md"

  # Get phase info from roadmap
  local PHASE_INFO
  PHASE_INFO=$(get_phase_info "$PHASE" "$ROADMAP")

  echo "→ Claude analyzing phase..."

  # ─── ROUND 1: Claude proposes ───
  local CLAUDE_PROPOSAL
  CLAUDE_PROPOSAL=$(run_claude_propose "$PHASE" "$MILESTONE_GOAL" "$PHASE_INFO" "$ROADMAP" "$PROJECT")

  echo "  ✓ Initial proposal ready"
  echo ""

  # ─── ROUND 2: Codex challenges ───
  echo "→ Codex reviewing proposal..."

  local CODEX_REVIEW
  CODEX_REVIEW=$(run_codex_challenge "$PHASE" "$MILESTONE_GOAL" "$CLAUDE_PROPOSAL")

  local CODEX_STATUS=$?
  if [[ $CODEX_STATUS -ne 0 ]]; then
    echo "  ⚠ Codex review failed, using Claude's proposal as-is"
    CODEX_REVIEW="[READY] Unable to review, proceeding with proposal"
  fi

  echo "  ✓ Codex feedback received"
  echo ""

  # Check for early exit - if Codex agrees with no questions
  if echo "$CODEX_REVIEW" | grep -q "\[READY\]"; then
    if ! echo "$CODEX_REVIEW" | grep -qE "\[QUESTION\]|\[UNCERTAIN\]"; then
      echo "  ✓ Codex agrees - skipping incorporation round"
      echo ""

      # Write CONTEXT.md directly from proposal
      write_context_file "$PHASE" "$CLAUDE_PROPOSAL" "$CODEX_REVIEW" "2" ""

      # Run fix loop to validate context quality
      echo "→ Validating context..."
      if ! run_codex_fix_loop "context" "$PHASE"; then
        echo "  ⚠ Context fix loop did not fully resolve - continuing with best effort"
      fi

      return 0
    fi
  fi

  # ─── ROUND 3: Claude incorporates feedback ───
  echo "→ Claude incorporating feedback..."

  local FINAL_DECISIONS
  FINAL_DECISIONS=$(run_claude_incorporate "$PHASE" "$MILESTONE_GOAL" "$CLAUDE_PROPOSAL" "$CODEX_REVIEW")

  echo "  ✓ Final decisions ready"
  echo ""

  # Extract uncertainties
  local UNCERTAINTIES
  UNCERTAINTIES=$(echo "$FINAL_DECISIONS" | awk '
    /^## Flagged for Human Review/,/^##/ {
      if (/^## Flagged for Human Review/) next
      if (/^##/) exit
      if (NF > 0) print
    }
  ')

  # ─── Handle Uncertainties ───
  local RESOLUTION=""
  if [[ -n "$UNCERTAINTIES" ]]; then
    echo "⚠ Uncertainties flagged:"
    echo "$UNCERTAINTIES" | head -5
    echo ""

    if [[ "$YOLO_MODE" == "true" ]]; then
      echo "  → YOLO mode: Using defaults for flagged items"
      RESOLUTION="defaults"
    else
      echo "  → Interactive mode: Review flagged items?"
      # Add timeout and check for non-interactive mode (CI/CD)
      if [[ -t 0 ]]; then
        read -r -t 30 -p "  [review/defaults/halt] " response < /dev/tty || response="defaults"
      else
        response="defaults"
      fi

      case "$response" in
        review)
          RESOLUTION="reviewed"
          resolve_uncertainties "$UNCERTAINTIES"
          ;;
        halt)
          echo "Halting for manual review."
          return 2  # Checkpoint signal
          ;;
        *)
          RESOLUTION="defaults"
          echo "  → Using defaults"
          ;;
      esac
    fi
  else
    RESOLUTION="none"
  fi

  # ─── Write CONTEXT.md ───
  write_context_file "$PHASE" "$FINAL_DECISIONS" "$CODEX_REVIEW" "3" "$RESOLUTION"

  # ─── Run fix loop to validate context quality ───
  echo "→ Validating context..."
  if ! run_codex_fix_loop "context" "$PHASE"; then
    echo "  ⚠ Context fix loop did not fully resolve - continuing with best effort"
  fi

  local FLAGGED_COUNT
  FLAGGED_COUNT=$(echo "$UNCERTAINTIES" | grep -c "^>" 2>/dev/null || echo "0")

  echo "✓ Auto-discuss complete"
  echo "  Rounds: 3"
  echo "  Flagged: $FLAGGED_COUNT items"
  echo "  Resolution: $RESOLUTION"
  echo ""

  return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Claude Operations
# ─────────────────────────────────────────────────────────────────────────────

run_claude_propose() {
  local PHASE="$1"
  local MILESTONE_GOAL="$2"
  local PHASE_INFO="$3"
  local ROADMAP="$4"
  local PROJECT="$5"

  claude -p "
You are analyzing Phase $PHASE for auto-discussion before planning.

## Milestone Context
Goal: $MILESTONE_GOAL

## Phase Information
$PHASE_INFO

## Task
For each implementation decision in this phase:
1. State the decision point clearly
2. Propose a specific choice with rationale grounded in the milestone goal
3. Note your confidence (HIGH/MEDIUM/LOW)

Consider:
- Existing codebase patterns (check $PROJECT for tech stack)
- Mobile-first requirements if applicable
- Simplest solution that achieves the goal

## Output Format

### Implementation Decisions

**[Decision Area 1]**
- Decision: [specific choice]
- Rationale: [why this choice, grounded in milestone goal]
- Confidence: [HIGH/MEDIUM/LOW]

**[Decision Area 2]**
...

### Uncertainties

> [Any genuine uncertainties that need human input]
> - Option A: ...
> - Option B: ...

### Claude's Discretion

- [Minor details left to implementation time]
" --dangerously-skip-permissions --output-format text 2>/dev/null | tee /dev/stderr
}

run_claude_incorporate() {
  local PHASE="$1"
  local MILESTONE_GOAL="$2"
  local PROPOSAL="$3"
  local CODEX_REVIEW="$4"

  claude -p "
Incorporate Codex feedback into final implementation decisions for Phase $PHASE.

## Milestone Goal
$MILESTONE_GOAL

## Original Proposal
$PROPOSAL

## Codex Feedback
$CODEX_REVIEW

## Task
1. For items marked [AGREE]: Keep as-is
2. For items marked [QUESTION]: Consider the alternative, decide, explain
3. For items marked [UNCERTAIN]: Add to 'Flagged for Human Review'
4. For items marked [SUGGEST]: Incorporate if valuable

Output the final CONTEXT.md content in this format:

# Phase $PHASE - Context (Auto-Generated)

**Generated:** $(date -u +"%Y-%m-%dT%H:%M:%SZ")
**Method:** Claude ↔ Codex dialogue
**Status:** Ready for planning

## Milestone Anchor

**Milestone Goal:** $MILESTONE_GOAL

## Implementation Decisions

[Group decisions by area, include source: Claude proposed / Codex suggested / Both agreed]

## Flagged for Human Review

> [Genuine uncertainties needing human input, with options]

## Claude's Discretion

- [Minor details left to implementation]

---
*Auto-generated via milestone sprint*
" --dangerously-skip-permissions --output-format text 2>/dev/null | tee /dev/stderr
}

# ─────────────────────────────────────────────────────────────────────────────
# Codex Operations
# ─────────────────────────────────────────────────────────────────────────────

run_codex_challenge() {
  local PHASE="$1"
  local MILESTONE_GOAL="$2"
  local PROPOSAL="$3"

  if [[ ! -x "$CODEX_SCRIPT" ]]; then
    echo "[READY] Codex not available"
    return 0
  fi

  bash "$CODEX_SCRIPT" "
Review these proposed implementation decisions for Phase $PHASE.

Milestone goal: $MILESTONE_GOAL

<claude_proposal>
$PROPOSAL
</claude_proposal>

For each decision:
- [AGREE] if reasonable and well-justified
- [QUESTION] if you'd do it differently - explain the alternative
- [UNCERTAIN] if this genuinely needs human input
- [SUGGEST] if you have an additional recommendation

Also flag any gaps Claude missed.

Keep responses concise. Focus on substantive issues, not style.
" "gpt-5.2-codex" "xhigh" "300" "brief" 2>/dev/null | tee /dev/stderr || return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

get_phase_info() {
  local PHASE="$1"
  local ROADMAP="$2"

  # Extract phase section from ROADMAP.md
  awk -v phase="$PHASE" '
    /^### Phase '"$PHASE"':/ { found=1 }
    found { print }
    found && /^### Phase [0-9]/ && !/^### Phase '"$PHASE"':/ { exit }
  ' "$ROADMAP" 2>/dev/null | head -50
}

write_context_file() {
  local PHASE="$1"
  local CONTENT="$2"
  local CODEX_REVIEW="$3"
  local ROUNDS="$4"
  local RESOLUTION="$5"

  local PLANNING_DIR=".planning/phases"
  local PHASE_DIR

  # Normalize phase to zero-padded format (1 -> 01)
  local NORMALIZED_PHASE
  if type normalize_phase &>/dev/null; then
    NORMALIZED_PHASE=$(normalize_phase "$PHASE")
  elif [[ "$PHASE" =~ ^[0-9]+$ ]]; then
    NORMALIZED_PHASE=$(printf "%02d" "$PHASE")
  else
    NORMALIZED_PHASE="$PHASE"
  fi

  # Find existing phase directory using helper if available
  if type find_phase_dir &>/dev/null; then
    PHASE_DIR=$(find_phase_dir "$PHASE" "$PLANNING_DIR")
  else
    PHASE_DIR=$(find "$PLANNING_DIR" -maxdepth 1 -type d -name "${NORMALIZED_PHASE}-*" 2>/dev/null | head -1)
  fi

  if [[ -z "$PHASE_DIR" ]]; then
    # Need to get phase name from roadmap
    local PHASE_NAME
    PHASE_NAME=$(awk -v phase="$PHASE" '
      /^### Phase '"$PHASE"':/ {
        gsub(/^### Phase [0-9]+: /, "")
        gsub(/ .*/, "")
        print
        exit
      }
    ' ".planning/ROADMAP.md" 2>/dev/null) || true

    # Warn and use default if phase name not found
    if [[ -z "$PHASE_NAME" ]]; then
      echo "WARNING: Could not find phase $PHASE in ROADMAP.md, using default" >&2
      PHASE_NAME="phase-$NORMALIZED_PHASE"
    fi

    # Sanitize name
    PHASE_NAME=$(echo "$PHASE_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')
    # Use NORMALIZED phase number (zero-padded) for directory name
    PHASE_DIR="$PLANNING_DIR/$NORMALIZED_PHASE-$PHASE_NAME"
    if ! mkdir -p "$PHASE_DIR"; then
      echo "ERROR: Failed to create phase directory: $PHASE_DIR" >&2
      return 1
    fi
  fi

  # Write CONTEXT.md
  cat > "$PHASE_DIR/CONTEXT.md" << EOF
$CONTENT

---

## Auto-Discuss Metadata

- **Rounds:** $ROUNDS
- **Codex Available:** $(if [[ -x "$CODEX_SCRIPT" ]]; then echo "yes"; else echo "no"; fi)
- **Uncertainties Resolution:** $RESOLUTION
- **Timestamp:** $(date -u +"%Y-%m-%dT%H:%M:%SZ")

<details>
<summary>Codex Review (Round 2)</summary>

$CODEX_REVIEW

</details>
EOF

  echo "  → Wrote $PHASE_DIR/CONTEXT.md"
}

resolve_uncertainties() {
  local UNCERTAINTIES="$1"

  echo ""
  echo "Please resolve these uncertainties:"
  echo "$UNCERTAINTIES"
  echo ""
  echo "Enter your decisions (press Enter twice when done):"

  local DECISIONS=""
  local LINE=""
  while IFS= read -r LINE; do
    [[ -z "$LINE" ]] && break
    DECISIONS="$DECISIONS$LINE\n"
  done < /dev/tty

  if [[ -n "$DECISIONS" ]]; then
    echo "  → Recorded decisions"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Main (for direct invocation)
# ─────────────────────────────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  PHASE="${1:-}"
  MILESTONE_GOAL="${2:-}"
  YOLO_MODE="${3:-false}"

  if [[ -z "$PHASE" ]]; then
    echo "Usage: auto-discuss.sh PHASE MILESTONE_GOAL [YOLO_MODE]"
    exit 1
  fi

  auto_discuss_phase "$PHASE" "$MILESTONE_GOAL" "$YOLO_MODE"
fi

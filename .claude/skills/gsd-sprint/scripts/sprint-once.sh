#!/bin/bash
# sprint-once.sh - Single iteration for human-in-the-loop testing
# Run once, watch what happens, run again.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/sprint-helpers.sh"

# Get current state
PHASE=$(get_current_phase)
STATUS=$(get_phase_status "$PHASE")

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "SPRINT-ONCE: Phase $PHASE ($STATUS)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

case "$STATUS" in
  "needs_planning")
    echo "Action: Planning phase $PHASE"
    echo ""
    # Fix 10: Use run_claude_streaming with signal checking
    run_claude_streaming "@.planning/STATE.md @.planning/ROADMAP.md

Execute /gsd:plan-phase $PHASE

IMPORTANT: When planning is complete and PLAN.md files are created, output exactly:
[SPRINT:PLANNING_COMPLETE]

If any error occurs, output:
[SPRINT:ERROR] description [/ERROR]"
    RESULT=$?

    case $RESULT in
      0) echo -e "\n✓ Planning complete" ;;
      4) echo -e "\n⚠ No completion signal received" ;;
      *) echo -e "\n⚠ Planning failed (code $RESULT)" ;;
    esac
    ;;

  "ready_to_execute"|"in_progress")
    echo "Action: Executing phase $PHASE"
    echo ""
    # Fix 10: Use run_claude_streaming with signal checking
    run_claude_streaming "@.planning/STATE.md

Execute /gsd:execute-phase $PHASE

Signal outcomes using these exact markers:

SUCCESS (all plans complete):
[SPRINT:PHASE_COMPLETE]

CHECKPOINT (requires human):
[SPRINT:CHECKPOINT]
type: auth-gate|decision|human-verify
details for human
[/CHECKPOINT]

ERROR:
[SPRINT:ERROR] description [/ERROR]"
    RESULT=$?

    case $RESULT in
      0) echo -e "\n✓ Phase complete" ;;
      3) echo -e "\n⚠ Checkpoint hit - manual action required" ;;
      4) echo -e "\n⚠ No completion signal received" ;;
      *) echo -e "\n⚠ Execution failed (code $RESULT)" ;;
    esac
    ;;

  "phase_complete")
    echo "Phase $PHASE is complete!"
    echo ""
    echo "Next: Run again to start phase $((PHASE + 1))"
    echo "Or: /gsd:sprint $((PHASE + 1)) <end-phase>"
    ;;

  "not_found")
    echo "Phase $PHASE not found in .planning/phases/"
    echo ""
    echo "Available phases:"
    ls -d .planning/phases/*/ 2>/dev/null | sed 's|.planning/phases/||;s|/||' || echo "(none)"
    ;;

  *)
    echo "Unknown status: $STATUS"
    exit 1
    ;;
esac

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "SPRINT-ONCE complete. Run again to continue."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

#!/bin/bash
# health-check.sh - Verify GSD setup is working correctly
#
# Checks:
# - Claude CLI installed and authenticated
# - Codex CLI installed and authenticated
# - Available skills
# - MCP server configuration

set -uo pipefail

# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

VERBOSE=false
[[ "${1:-}" == "--verbose" || "${1:-}" == "-v" ]] && VERBOSE=true

# Colors (disable if not a terminal)
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'
  RED='\033[0;31m'
  YELLOW='\033[0;33m'
  BLUE='\033[0;34m'
  NC='\033[0m' # No Color
else
  GREEN=''
  RED=''
  YELLOW=''
  BLUE=''
  NC=''
fi

# Counters
PASSED=0
FAILED=0
WARNINGS=0

# ═══════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════

print_header() {
  echo ""
  echo -e "${BLUE}$1${NC}"
  echo "$(echo "$1" | sed 's/./-/g')"
}

print_ok() {
  echo -e "  ${GREEN}[OK]${NC} $1"
  ((PASSED++))
}

print_fail() {
  echo -e "  ${RED}[FAIL]${NC} $1"
  ((FAILED++))
}

print_warn() {
  echo -e "  ${YELLOW}[WARN]${NC} $1"
  ((WARNINGS++))
}

print_info() {
  echo -e "  ${BLUE}[INFO]${NC} $1"
}

verbose() {
  [[ "$VERBOSE" == true ]] && echo "       $1"
}

# ═══════════════════════════════════════════════════════════════
# Check Functions
# ═══════════════════════════════════════════════════════════════

check_tmux() {
  print_header "tmux (for long-running tasks)"

  # Check if installed
  if ! command -v tmux &>/dev/null; then
    print_fail "tmux not found in PATH"
    verbose "Install with: brew install tmux (macOS) or apt install tmux (Linux)"
    verbose "tmux is required for /gsd:sprint and /gsd:milestone-sprint"
    return 1
  fi

  # Get version
  local VERSION
  VERSION=$(tmux -V 2>/dev/null) || VERSION="unknown"
  print_ok "Installed: $VERSION"

  # Check if tmux server is responsive
  if tmux list-sessions &>/dev/null || [[ $? -eq 1 ]]; then
    # Exit code 1 means "no sessions" which is fine - server works
    print_ok "Server responsive"
  else
    print_warn "tmux server may have issues"
    verbose "Try: tmux kill-server && tmux"
  fi

  # List any active GSD sessions
  local GSD_SESSIONS
  GSD_SESSIONS=$(tmux list-sessions 2>/dev/null | grep -E "^gsd-" || true)

  if [[ -n "$GSD_SESSIONS" ]]; then
    echo ""
    echo "  Active GSD sessions:"
    while IFS= read -r session; do
      local SESSION_NAME
      SESSION_NAME=$(echo "$session" | cut -d: -f1)
      print_info "$SESSION_NAME"
      verbose "    Attach: tmux attach -t $SESSION_NAME"
    done <<< "$GSD_SESSIONS"
  fi
}

check_claude_cli() {
  print_header "Claude CLI"

  # Check if installed
  if ! command -v claude &>/dev/null; then
    print_fail "Claude CLI not found in PATH"
    verbose "Install with: npm install -g @anthropic-ai/claude-code"
    return 1
  fi

  # Get version
  local VERSION
  VERSION=$(claude --version 2>/dev/null | head -1) || VERSION="unknown"
  print_ok "Installed: $VERSION"

  # Check authentication by running a simple prompt
  local AUTH_TEST
  AUTH_TEST=$(timeout 30 claude -p "Reply with exactly: HEALTH_CHECK_OK" --max-turns 1 --output-format text 2>&1) || true

  if echo "$AUTH_TEST" | grep -q "HEALTH_CHECK_OK"; then
    print_ok "Authenticated and working"
    verbose "Test prompt executed successfully"
  elif echo "$AUTH_TEST" | grep -qi "auth\|login\|unauthorized\|api.?key"; then
    print_fail "Not authenticated"
    verbose "Run 'claude login' or set ANTHROPIC_API_KEY"
    verbose "Error: $AUTH_TEST"
  else
    print_warn "Could not verify authentication"
    verbose "Response: $AUTH_TEST"
  fi
}

check_codex_cli() {
  print_header "Codex CLI (OpenAI)"

  # Check if installed
  if ! command -v codex &>/dev/null; then
    print_warn "Codex CLI not found in PATH"
    verbose "Install with: npm install -g @openai/codex"
    verbose "Codex is optional - used for second opinions and validation"
    return 0
  fi

  # Get version
  local VERSION
  VERSION=$(codex --version 2>/dev/null | head -1) || VERSION="unknown"
  print_ok "Installed: $VERSION"

  # Check authentication by running a simple prompt
  # Codex uses account auth (codex auth login), not API key
  local AUTH_TEST
  AUTH_TEST=$(timeout 60 codex exec \
    --model codex-mini-latest \
    --sandbox read-only \
    -c approval_policy="never" \
    -- "Reply with exactly: HEALTH_CHECK_OK" 2>&1) || true

  if echo "$AUTH_TEST" | grep -q "HEALTH_CHECK_OK"; then
    print_ok "Authenticated and working"
    verbose "Test prompt executed successfully"
  elif echo "$AUTH_TEST" | grep -qi "auth\|login\|unauthorized\|not logged"; then
    print_fail "Not authenticated"
    verbose "Run 'codex auth login' to authenticate"
    verbose "Error: $(echo "$AUTH_TEST" | head -3)"
  else
    print_warn "Could not verify authentication"
    verbose "Response: $(echo "$AUTH_TEST" | head -3)"
  fi
}

check_skills() {
  print_header "GSD Skills"

  # Find skills directory (project-local or global)
  local SKILLS_DIR=""
  if [[ -d ".claude/skills" ]]; then
    SKILLS_DIR=".claude/skills"
    verbose "Using project-local skills: $SKILLS_DIR"
  elif [[ -d "$HOME/.claude/skills" ]]; then
    SKILLS_DIR="$HOME/.claude/skills"
    verbose "Using global skills: $SKILLS_DIR"
  else
    print_fail "No skills directory found"
    verbose "Expected .claude/skills or ~/.claude/skills"
    return 1
  fi

  # List skills
  local SKILL_COUNT=0
  local SKILLS_OK=0
  local SKILLS_MISSING=0

  for skill_dir in "$SKILLS_DIR"/*/; do
    [[ ! -d "$skill_dir" ]] && continue

    local skill_name=$(basename "$skill_dir")
    local skill_file="$skill_dir/SKILL.md"

    ((SKILL_COUNT++))

    if [[ -f "$skill_file" ]]; then
      # Extract skill name from frontmatter
      local display_name
      display_name=$(grep "^name:" "$skill_file" 2>/dev/null | sed 's/name:[[:space:]]*//' | head -1)
      [[ -z "$display_name" ]] && display_name="$skill_name"

      # Check for scripts
      local has_scripts=false
      [[ -d "$skill_dir/scripts" ]] && [[ -n "$(ls -A "$skill_dir/scripts" 2>/dev/null)" ]] && has_scripts=true

      if [[ "$has_scripts" == true ]]; then
        print_ok "$display_name"
        ((SKILLS_OK++))

        if [[ "$VERBOSE" == true ]]; then
          for script in "$skill_dir/scripts"/*.sh; do
            [[ -f "$script" ]] && verbose "  Script: $(basename "$script")"
          done
        fi
      else
        print_ok "$display_name (no scripts)"
        ((SKILLS_OK++))
      fi
    else
      print_warn "$skill_name - missing SKILL.md"
      ((SKILLS_MISSING++))
    fi
  done

  if [[ $SKILL_COUNT -eq 0 ]]; then
    print_warn "No skills found in $SKILLS_DIR"
  else
    echo ""
    print_info "Total: $SKILL_COUNT skills ($SKILLS_OK valid, $SKILLS_MISSING missing config)"
  fi
}

check_mcp_servers() {
  print_header "MCP Servers (Claude)"

  # Check for jq first
  if ! command -v jq &>/dev/null; then
    print_warn "jq not installed - cannot parse MCP config"
    verbose "Install jq for detailed MCP analysis: brew install jq"
    return 0
  fi

  local TOTAL_SERVERS=0

  # ─── Project-local MCP (.mcp.json) ───
  echo ""
  echo "  Project-local (.mcp.json):"

  if [[ -f ".mcp.json" ]]; then
    local SERVERS
    SERVERS=$(jq -r '.mcpServers // {} | keys[]' ".mcp.json" 2>/dev/null) || true

    if [[ -n "$SERVERS" ]]; then
      while IFS= read -r server; do
        [[ -z "$server" ]] && continue
        ((TOTAL_SERVERS++))

        local DISABLED
        DISABLED=$(jq -r ".mcpServers[\"$server\"].disabled // false" ".mcp.json" 2>/dev/null)

        local CMD
        CMD=$(jq -r ".mcpServers[\"$server\"].command // \"unknown\"" ".mcp.json" 2>/dev/null)

        local ARGS
        ARGS=$(jq -r ".mcpServers[\"$server\"].args // [] | join(\" \")" ".mcp.json" 2>/dev/null)

        if [[ "$DISABLED" == "true" ]]; then
          print_warn "$server (disabled)"
        else
          print_ok "$server"
        fi
        verbose "    $CMD $ARGS"
      done <<< "$SERVERS"
    else
      print_info "No servers in .mcp.json"
    fi
  else
    print_info "No .mcp.json found (create one for project-specific MCP servers)"
  fi

  # ─── Global MCP (~/.claude/settings.json) ───
  echo ""
  echo "  Global (~/.claude/settings.json):"

  local SETTINGS_FILE="$HOME/.claude/settings.json"
  if [[ -f "$SETTINGS_FILE" ]]; then
    local SERVERS
    SERVERS=$(jq -r '.mcpServers // {} | keys[]' "$SETTINGS_FILE" 2>/dev/null) || true

    if [[ -n "$SERVERS" ]]; then
      while IFS= read -r server; do
        [[ -z "$server" ]] && continue
        ((TOTAL_SERVERS++))

        local DISABLED
        DISABLED=$(jq -r ".mcpServers[\"$server\"].disabled // false" "$SETTINGS_FILE" 2>/dev/null)

        local CMD
        CMD=$(jq -r ".mcpServers[\"$server\"].command // \"unknown\"" "$SETTINGS_FILE" 2>/dev/null)

        if [[ "$DISABLED" == "true" ]]; then
          print_warn "$server (disabled)"
        else
          print_ok "$server"
        fi
        verbose "    Command: $CMD"
      done <<< "$SERVERS"
    else
      print_info "No servers in settings.json"
    fi

    # Also check enabledPlugins for MCP-providing plugins
    local PLUGINS
    PLUGINS=$(jq -r '.enabledPlugins // {} | to_entries[] | select(.value == true) | .key' "$SETTINGS_FILE" 2>/dev/null) || true

    if [[ -n "$PLUGINS" ]]; then
      echo ""
      echo "  Enabled Plugins (may provide MCP):"
      while IFS= read -r plugin; do
        [[ -z "$plugin" ]] && continue
        print_ok "$plugin"
      done <<< "$PLUGINS"
    fi
  else
    print_info "No ~/.claude/settings.json found"
  fi

  echo ""
  print_info "Total: $TOTAL_SERVERS MCP servers configured"
  verbose "Add servers: claude mcp add <name> -- <command>"
  verbose "Project-local: create .mcp.json in project root"
}

check_mcp_servers_codex() {
  print_header "MCP Servers (Codex)"

  # Codex MCP is experimental
  local CODEX_CONFIG="$HOME/.codex/config.toml"

  if [[ ! -f "$CODEX_CONFIG" ]]; then
    print_info "No Codex config found"
    return 0
  fi

  # Check if there's an [mcp] section in config.toml
  if grep -q '^\[mcp' "$CODEX_CONFIG" 2>/dev/null; then
    print_ok "MCP section found in config.toml"
    verbose "Codex MCP is experimental"

    # Try to list servers
    local MCP_LIST
    MCP_LIST=$(codex mcp list 2>&1) || true

    if [[ -n "$MCP_LIST" ]] && ! echo "$MCP_LIST" | grep -q "Error"; then
      echo "$MCP_LIST" | while IFS= read -r line; do
        [[ -n "$line" ]] && verbose "  $line"
      done
    fi
  else
    print_info "No MCP configured (experimental feature)"
    verbose "Add servers: codex mcp add <name> -- <command>"
  fi
}

check_project_structure() {
  print_header "Project Structure"

  # Check for .planning directory
  if [[ -d ".planning" ]]; then
    print_ok ".planning/ directory exists"

    # Check for key files
    [[ -f ".planning/PROJECT.md" ]] && verbose "  PROJECT.md found" || verbose "  PROJECT.md not found"
    [[ -f ".planning/ROADMAP.md" ]] && verbose "  ROADMAP.md found" || verbose "  ROADMAP.md not found"
    [[ -f ".planning/STATE.md" ]] && verbose "  STATE.md found" || verbose "  STATE.md not found"
  else
    print_info "No .planning/ directory (run /gsd:new-project to create)"
  fi

  # Check for GSD templates
  local TEMPLATES_DIR=""
  if [[ -d ".claude/get-shit-done/templates" ]]; then
    TEMPLATES_DIR=".claude/get-shit-done/templates"
  elif [[ -d "$HOME/.claude/get-shit-done/templates" ]]; then
    TEMPLATES_DIR="$HOME/.claude/get-shit-done/templates"
  fi

  if [[ -n "$TEMPLATES_DIR" ]]; then
    local TEMPLATE_COUNT
    TEMPLATE_COUNT=$(find "$TEMPLATES_DIR" -name "*.md" -o -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
    print_ok "GSD templates: $TEMPLATE_COUNT files"
  else
    print_warn "GSD templates not found"
  fi
}

# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

echo ""
echo "========================================"
echo "       GSD Health Check"
echo "========================================"

check_tmux
check_claude_cli
check_codex_cli
check_skills
check_mcp_servers
check_mcp_servers_codex
check_project_structure

# Summary
echo ""
echo "========================================"
echo "           Summary"
echo "========================================"
echo ""

if [[ $FAILED -eq 0 && $WARNINGS -eq 0 ]]; then
  echo -e "${GREEN}All checks passed!${NC}"
elif [[ $FAILED -eq 0 ]]; then
  echo -e "${YELLOW}Checks passed with $WARNINGS warning(s)${NC}"
else
  echo -e "${RED}$FAILED check(s) failed, $WARNINGS warning(s)${NC}"
fi

echo ""
echo "  Passed:   $PASSED"
echo "  Warnings: $WARNINGS"
echo "  Failed:   $FAILED"
echo ""

# Exit code
[[ $FAILED -gt 0 ]] && exit 1
exit 0

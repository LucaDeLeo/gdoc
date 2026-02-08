---
name: gsd:health-check
description: Verify GSD setup - checks Claude, Codex, skills, and MCP servers are working correctly.
argument-hint: "[--verbose]"
---

# GSD Health Check

Run diagnostics to verify your GSD setup is working correctly.

## What It Checks

1. **Claude CLI** - Installed, authenticated, can execute prompts
2. **Codex CLI** - Installed, authenticated, can execute prompts
3. **Skills** - Lists available GSD skills and verifies script paths
4. **MCP Servers** - Lists configured MCP servers and their status

## Usage

```bash
# Quick check (summary only)
/gsd:health-check

# Verbose output (full details)
/gsd:health-check --verbose
```

## Output

```
GSD Health Check
================

Claude CLI:     [OK] v1.2.3, authenticated as user@example.com
Codex CLI:      [OK] v0.1.0, authenticated
Skills:         [OK] 5 skills found
MCP Servers:    [OK] 3 servers configured

All checks passed!
```

## Execution

```bash
# Support both project-local (.claude/) and global (~/.claude/) installs
if [[ -f ".claude/skills/gsd-health-check/scripts/health-check.sh" ]]; then
  bash .claude/skills/gsd-health-check/scripts/health-check.sh $ARGUMENTS
else
  bash ~/.claude/skills/gsd-health-check/scripts/health-check.sh $ARGUMENTS
fi
```

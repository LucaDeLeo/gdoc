Phase 01 Codex feedback integration — summary of changes from the original proposal.

> **Canonical decision tables:** `.planning/phases/01/CONTEXT.md`
>
> This file is a **changelog summary only**. If this summary conflicts with the canonical decision tables, the canonical file wins. Do not treat this summary as an independent source of truth.

**Incorporated from Codex review (5 major + 4 minor):**

1. **Custom ArgumentParser for exit code 3** — argparse defaults to exit 2 on usage errors, which collides with auth error code 2. Subclass `ArgumentParser.error()` to emit exit code 3 instead.
2. **Stub subcommands use exit code 4** — `gdoc.md` defines four exit codes: `0=success, 1=API error, 2=auth error, 3=usage error`. Exit code 4 is unassigned by the spec and avoids collision with all defined codes. This is preferable to exit 1 (which `gdoc.md` defines as "API error") because it introduces no spec deviation. **Dev-only override policy:** exit code 4 is a development-only artifact not defined in `gdoc.md`. This is acceptable because: (a) `gdoc.md` describes the finished tool, not scaffolding — it neither defines nor forbids exit code 4, (b) the stderr message `ERR: <command> is not yet implemented` unambiguously identifies the condition, (c) exit 4 is removed when stubs are replaced with real implementations. **Enforcement:** a CI release gate (`scripts/check-no-stubs.sh` or equivalent) must verify no stub exit-code-4 paths remain before any tagged release. This guard is created in Phase 1 scaffolding.
3. **`--json`/`--verbose` mutual exclusivity** — enforced via argparse `add_mutually_exclusive_group`.
4. **Error format gaps filled** — `ERR: <message>` prefix on stderr for all errors; errors always plain text on stderr even in `--json` mode; top-level exception handler formalized in `main()`.
5. **Runtime dependencies locked to gdoc.md spec** — `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2` with minimum versions. Codex flagged this as a gap; now explicit in the scaffolding decision table.
6. `gdoc auth` validates `credentials.json` exists before starting flow
7. Guard for corrupt `token.json` and missing `refresh_token`
8. Support `id=` query parameter URLs
9. `--no-browser` as explicit headless toggle (not just auto-detection)

**Resolved (previously flagged, now settled):**

1. **Build backend → hatchling.** gdoc.md mandates minimal deps; hatchling is build-time only, PEP 517, lighter than setuptools. Python `>=3.10` for `match` statements and modern typing. Low-impact, easily swappable.
2. **Headless OAuth → `run_local_server(open_browser=False)` with documented limitations.** `run_console()` was **removed** in google-auth-oauthlib v1.0.0 (Feb 2023, "Remove deprecated OOB code #264"). Our minimum version (`>=1.2.1`) does not have it. Headless flow: start local server without browser, print auth URL for manual copy-paste. **Localhost redirect limitation:** this requires the OAuth redirect to reach the machine running `gdoc auth` (works for: local desktop, WSL, containers with host networking, SSH with port forwarding via `ssh -L 8080:localhost:8080`). For fully remote/headless environments without port forwarding, the documented workaround is: run `gdoc auth` on a local machine, then copy `~/.gdoc/token.json` to the remote host. Device flow is not used — it requires a different OAuth client type configuration in Google Cloud Console and Google has been restricting its availability for new projects.

**Rejected:**
- Stubbing all architecture files (api/drive.py, state.py, notify.py, etc.) in Phase 1 — no value in empty files

**Source-of-truth hierarchy:**
1. `gdoc.md` (root) — product spec, overrides everything
2. `.planning/research/*.md` — research outputs (STACK, ARCHITECTURE, PITFALLS, FEATURES, SUMMARY)
3. `.planning/PROJECT.md` — project definition derived from gdoc.md

Where research files informed a decision not covered by `gdoc.md` (e.g., build backend, Python version), this is noted in the Source column of the decision tables. `gdoc.md` always wins on conflicts.

**Dev-only override policy:** Development scaffolding may introduce behaviors not described in `gdoc.md` (e.g., exit code 4 for stubs) provided: (1) the behavior does not contradict any spec-defined behavior, (2) it is documented in the canonical CONTEXT.md with explicit rationale, and (3) a concrete enforcement mechanism (CI gate, test assertion) prevents the dev-only behavior from shipping. This policy does not permit deviating from spec-defined behaviors — only extending into undefined territory during development.

---

## Auto-Discuss Metadata

- **Rounds:** 3
- **Codex Available:** yes
- **Uncertainties Resolution:** 2/2 resolved (build backend → hatchling; headless OAuth → run_local_server, run_console removed)
- **Timestamp:** 2026-02-07T18:19:02Z

<details>
<summary>Codex Review (Round 2)</summary>

**Findings**
- High: `argparse` defaults to exit code 2 on usage errors, which conflicts with the required exit code 3 and collides with auth error code 2. You'll need a custom `ArgumentParser.error()` or a `SystemExit` remap to enforce 3. (`gdoc.md`)
- Medium: "Not yet implemented" stubs returning exit code 3 will look like usage errors; recommend exit code 1 to avoid misleading automation.
- Medium: Several decisions cite `STACK.md`, `PROJECT.md`, `ARCHITECTURE.md`, and `PITFALLS.md`, but only `gdoc.md` exists in this repo. Please confirm those sources or align decisions strictly to `gdoc.md`.

1. [UNCERTAIN] Build backend (`hatchling`) and Python `>=3.10`: `gdoc.md` doesn't specify packaging or Python version. If you want minimal build deps, consider `setuptools`; otherwise hatchling is fine.
2. [AGREE] `argparse` with `add_subparsers` and a central `cli.py` aligns with `gdoc.md`'s architecture; ensure `--json` and `--verbose` are mutually exclusive.
3. [SUGGEST] Minimal Phase‑1 files are fine, but consider stubbing `gdoc/api/drive.py`, `gdoc/api/docs.py`, `gdoc/api/comments.py`, `gdoc/state.py`, `gdoc/notify.py`, and `gdoc/annotate.py` to match the `gdoc.md` architecture and reduce later churn.
4. [AGREE] `run_local_server` primary + token at `~/.gdoc/token.json` and scope tracking matches `gdoc.md`; permissions hardening is a plus.
5. [QUESTION] Headless detection via `DISPLAY`/`webbrowser.get()` is brittle. I'd prefer explicit `--no-browser` as the primary toggle plus `run_local_server(open_browser=...)` and clear instructions for manual URL opening; consider device flow only if required.
6. [AGREE] Scopes `drive` + `documents` match `gdoc.md` and are needed for full functionality.
7. [AGREE] Refresh logic and invalid_grant recovery are good; also guard for missing/invalid `refresh_token` and a corrupt `token.json`.
8. [AGREE] Regex extraction and bare IDs align with `gdoc.md`; consider also supporting `id=` query URLs (common Drive links).
9. [QUESTION] Exit codes: `argparse` won't emit 3 without customization. Implement a custom parser to enforce 3 and avoid collision with auth exit code 2. (`gdoc.md`)
10. [AGREE] `format.py` with pure functions and `terse/json/verbose` matches `gdoc.md`; enforce mutual exclusivity and ensure JSON is the only stdout when `--json`.
11. [AGREE] Stdout/stderr separation is correct for pipe‑safety; keep all errors on stderr.
12. [AGREE] `gdoc auth` subcommand is the right UX; ensure it validates `~/.gdoc/credentials.json` exists with a clear error.
13. [AGREE] `~/.gdoc/` and `state/` align with `gdoc.md`.
14. [SUGGEST] Keep `pytest`/`ruff` in dev extras only to preserve minimal runtime deps; if strict minimalism is desired, consider stdlib `unittest`.
15. [QUESTION] Registering stub subcommands is good for help text, but exit code 3 for "not implemented" is misleading; use exit code 1 with a clear message.

**Gaps**
- No explicit decision on error message format (e.g., `ERR: ...`) despite examples in `gdoc.md`; define a consistent error formatter now.
- No decision on how errors should look in `--json` mode (structured JSON vs plain stderr). This affects "consistent error reporting."
- No explicit plan for top‑level exception handling to enforce consistent exit codes and error formatting across all commands.
- `pyproject.toml` dependency list isn't specified; `gdoc.md` requires the three Google libraries.

</details>

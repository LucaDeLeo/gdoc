"""Model Context Protocol server exposing gdoc subcommands as MCP tools.

`gdoc mcp` speaks MCP over stdio so clients that launch a local stdio
server (Claude Desktop, the Codex CLI, and others) can drive gdoc
directly, instead of gdoc only being reachable from a coding agent with
shell access.

Design notes:

- **No new dependencies.** MCP's stdio transport is newline-delimited
  JSON-RPC 2.0, which is short enough to implement here. Adding an SDK
  would pull a web stack into a CLI whose dependency list is deliberately
  four entries long.
- **Schemas are derived from argparse**, not hand-written, so a new flag on
  `gdoc edit` becomes an MCP tool parameter with no work here. Only the
  command allowlist and the read/write classification below are manual.
- **Commands run in-process** via the same dispatch the CLI uses, with
  stdout captured. Nothing shells out.
- **Validation errors here are deliberately not `GdocError`s.** Everything
  raised in this module surfaces as a JSON-RPC error or an `isError` tool
  result; nothing reaches a process exit path, so the CLI's
  exit-code-carrying exception convention does not apply.
"""

import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from typing import Any

from gdoc import __version__

# MCP revisions this server has been checked against. An unknown version
# from the client falls back to the newest one we know.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

TOOL_PREFIX = "gdoc_"

# Subcommands exposed as tools, and whether each one only reads.
# Anything absent is deliberately not exposed: `auth` needs an interactive
# browser, `update` mutates the install, `config` is machine-wide, and
# `pull`/`push`/`export`/`insert-image`/`replace-image` work on local file
# paths a chat client cannot see.
EXPOSED_COMMANDS: dict[str, bool] = {
    # read-only
    "ls": True,
    "find": True,
    "cat": True,
    "info": True,
    "tabs": True,
    "toc": True,
    "revisions": True,
    "comments": True,
    "comment-info": True,
    "suggestions": True,
    "suggestion-info": True,
    "diff": True,
    "images": True,
    "structure": True,
    "drives": True,
    # writes
    "edit": False,
    "suggest": False,
    "nest": False,
    "unnest": False,
    "insert": False,
    "write": False,
    "cells": False,
    "add-tab": False,
    "comment": False,
    "reply": False,
    "resolve": False,
    "reopen": False,
    "delete-comment": False,
    "accept-suggestion": False,
    "reject-suggestion": False,
    "delete-suggestion": False,
    "edit-comment": False,
    "edit-suggestion-reply": False,
    "delete-reply": False,
    "delete-suggestion-reply": False,
    "new": False,
    "cp": False,
    "mkdir": False,
    "mv": False,
    "rename": False,
    "share": False,
}

# Commands whose content argument is a local markdown file. A chat client
# has no filesystem to write one to, so over MCP the file parameter is
# replaced by inline `text`, which the server materialises to a temp file
# for the duration of the call. The CLI itself is unchanged.
_TEXT_TO_FILE: dict[str, str] = {
    "write": "file",
    "insert": "file",
    "new": "file_path",
}

_TEXT_DESCRIPTION = "Markdown content, supplied inline."

# Parameters that name paths on the server's filesystem. A chat client
# cannot see that filesystem, so they are useless to legitimate callers —
# and they would let a prompt-injected model read host files into a doc
# (`edit --new-file ~/.ssh/id_rsa`) or write files onto the host
# (`images --download`). Stripped from the schemas and rejected at call
# time.
_LOCAL_PATH_PARAMS: dict[str, frozenset[str]] = {
    "edit": frozenset({"old_file", "new_file"}),
    "suggest": frozenset({"old_file", "new_file"}),
    "write": frozenset({"file"}),
    "insert": frozenset({"file"}),
    "new": frozenset({"file_path"}),
    "cells": frozenset({"file"}),
    "diff": frozenset({"file", "out"}),
    "images": frozenset({"download"}),
}

# Choice values that would write to the server's filesystem: `diff
# --format html` renders to --out (default gdoc-diff.html in the cwd).
_LOCAL_PATH_CHOICES: dict[str, dict[str, frozenset[str]]] = {
    "diff": {"format": frozenset({"html"})},
}

# Parameters where the CLI treats a literal `-` as "read from stdin".
# Over MCP stdin is the JSON-RPC stream, shielded to an empty string for
# the call's duration, so `-` would silently resolve to "" — turning a
# replacement into a deletion — instead of reading anything.
_STDIN_SENTINEL_PARAMS: dict[str, frozenset[str]] = {
    "edit": frozenset({"old_text", "new_text"}),
    "suggest": frozenset({"old_text", "new_text"}),
}

# Commands that use diff-style exit codes: 1 means "differences found",
# not failure. Real errors still print an ERR: line to stderr.
_DIFF_EXIT_COMMANDS = frozenset({"diff"})

# MCP-only schema tightenings: parameters the CLI can satisfy another way
# (an alternative flag, an interactive prompt) that MCP cannot, so a
# schema-valid call would otherwise be guaranteed to fail at runtime.
_EXTRA_REQUIRED: dict[str, tuple[str, ...]] = {
    # -v/--value is the only data source left once --file/--stdin are hidden
    "cells": ("value",),
    # confirm_destructive() cannot prompt over MCP (stdin is detached)
    "delete-comment": ("force",),
    # --old-file/--new-file are hidden over MCP and suggest has no cell
    # mode, so the text pair is the only way to supply the replacement
    "suggest": ("old_text", "new_text"),
    "delete-suggestion": ("force",),
    "delete-reply": ("force",),
    "delete-suggestion-reply": ("force",),
}

# Commands whose `force` must be true over MCP (see _EXTRA_REQUIRED).
_FORCE_REQUIRED = frozenset(
    cmd for cmd, params in _EXTRA_REQUIRED.items() if "force" in params
)

# Cross-parameter requirements JSON Schema could only express with a
# root-level oneOf/anyOf, which several MCP clients reject or ignore.
# Stated in the tool description instead; the CLI repeats the same
# constraint in its error message at call time.
_DESCRIPTION_NOTES: dict[str, str] = {
    "diff": "Exactly one of `rev` or `since` is required over MCP.",
    "share": "Exactly one of `email`, `domain`, or `anyone` is required.",
    "edit": (
        "Text replacement needs `old_text` and `new_text`; `cell` mode "
        "addresses a table cell instead."
    ),
    "suggest": (
        "Needs `old_text` and `new_text`. The replacement lands as a "
        "suggested edit for review, not a direct change; inline markdown "
        "only."
    ),
    "comment": "`assign` requires `quote` (Docs API preview, no Drive fallback).",
    "reply": (
        "`suggestion` and `reassign` are mutually exclusive; `reassign` "
        "needs a thread that already has an assignee (Docs API preview)."
    ),
}

# `new` imports markdown images: extract_images() resolves non-http(s)
# references against the materialised temp file's directory and uploads
# what it finds, so inline text could still read server files that the
# hidden path parameters no longer can.
_IMAGE_REF = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)")

# Parser-level plumbing that must not become a tool parameter.
_SKIP_DESTS = frozenset({
    "help",
    "func",
    "command",
    "allow_commands",
    "verbose",
    "plain",
    # `cells --stdin` reads the server's stdin — the JSON-RPC stream
    "stdin",
})


def _tool_name(command: str) -> str:
    return TOOL_PREFIX + command.replace("-", "_")


def _command_name(tool: str) -> str:
    if not tool.startswith(TOOL_PREFIX):
        raise KeyError(tool)
    return tool[len(TOOL_PREFIX):].replace("_", "-")


def _help_text(action: argparse.Action) -> str:
    """Render an action's help, tolerating argparse's %(default)s syntax."""
    help_str = action.help or ""
    if "%" not in help_str:
        return help_str
    try:
        return help_str % {"default": action.default, "prog": "gdoc"}
    except (KeyError, TypeError, ValueError):
        return help_str


def _property_for(action: argparse.Action) -> dict[str, Any]:
    """Map one argparse action onto a JSON Schema property."""
    prop: dict[str, Any] = {}
    description = _help_text(action)

    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        prop["type"] = "boolean"
    elif action.choices:
        prop["type"] = "string"
        prop["enum"] = [str(c) for c in action.choices]
    elif action.nargs in ("+", "*") or isinstance(action, argparse._AppendAction):
        prop["type"] = "array"
        prop["items"] = {"type": "integer" if action.type is int else "string"}
    elif action.type is int:
        prop["type"] = "integer"
    elif action.type is float:
        prop["type"] = "number"
    else:
        prop["type"] = "string"

    if description:
        prop["description"] = description
    return prop


def _schema_for(command: str, parser: argparse.ArgumentParser) -> dict[str, Any]:
    """Derive a JSON Schema for a subparser's arguments."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    hidden = _LOCAL_PATH_PARAMS.get(command, frozenset())
    hidden_choices = _LOCAL_PATH_CHOICES.get(command, {})
    extra_required = _EXTRA_REQUIRED.get(command, ())

    for action in parser._actions:
        if action.dest in _SKIP_DESTS or action.dest in hidden:
            continue
        if isinstance(action, (argparse._HelpAction, argparse._VersionAction)):
            continue
        if isinstance(action, argparse._SubParsersAction):
            continue

        prop = _property_for(action)
        removed = hidden_choices.get(action.dest)
        if removed and "enum" in prop:
            prop["enum"] = [c for c in prop["enum"] if c not in removed]
        if action.dest in extra_required and prop.get("type") == "array":
            prop["minItems"] = 1
        if action.dest == "force" and command in _FORCE_REQUIRED:
            # `required` alone would admit `force: false`, which always
            # fails at runtime; say in the schema that only true is valid.
            prop["const"] = True
        properties[action.dest] = prop

        is_positional = not action.option_strings
        if is_positional and action.nargs not in ("?", "*"):
            required.append(action.dest)
        elif action.required or action.dest in extra_required:
            required.append(action.dest)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _file_arg_required(parser: argparse.ArgumentParser, file_arg: str) -> bool:
    """Whether the file argument that `text` replaces is mandatory."""
    action = next((a for a in parser._actions if a.dest == file_arg), None)
    return action is not None and action.required


def _description_for(command: str, parser: argparse.ArgumentParser) -> str:
    """Prefer the subparser's long description, falling back to its help."""
    text = (parser.description or "").strip()
    if not text:
        text = (getattr(parser, "_gdoc_help", "") or "").strip()
    note = _DESCRIPTION_NOTES.get(command)
    if note:
        text = f"{text}\n\n{note}" if text else note
    if not EXPOSED_COMMANDS[command]:
        warning = "Writes to Google Docs/Drive."
        text = f"{text}\n\n{warning}" if text else warning
    return text


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def build_tools(
    *, read_only: bool = False, allow: set[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Build the MCP tool list by introspecting the gdoc CLI parser.

    Returns a mapping of tool name -> tool definition, in the order the
    commands are listed in EXPOSED_COMMANDS.
    """
    from gdoc.cli import build_parser

    parser = build_parser()
    subparsers = _subparsers(parser)
    help_by_command = _help_by_command(parser)

    tools: dict[str, dict[str, Any]] = {}
    env_allow = os.environ.get("GDOC_ALLOW_COMMANDS", "")
    env_set = None
    if env_allow:
        # run_argv enforces this env allowlist on every in-process call;
        # mirror it here so tools/list never advertises a tool that can
        # only fail.
        env_set = {c.strip().lower() for c in env_allow.split(",") if c.strip()}

    for command, is_read_only in EXPOSED_COMMANDS.items():
        if command not in subparsers:
            continue  # command retired upstream; skip rather than crash
        if read_only and not is_read_only:
            continue
        if allow is not None and command not in allow:
            continue
        if env_set is not None and command not in env_set:
            continue

        sub = subparsers[command]
        sub._gdoc_help = help_by_command.get(command, "")
        schema = _schema_for(command, sub)

        file_arg = _TEXT_TO_FILE.get(command)
        if file_arg:
            schema["properties"]["text"] = {
                "type": "string",
                "description": _TEXT_DESCRIPTION,
            }
            if _file_arg_required(sub, file_arg):
                schema.setdefault("required", []).append("text")

        tools[_tool_name(command)] = {
            "name": _tool_name(command),
            "description": _description_for(command, sub),
            "inputSchema": schema,
        }
    return tools


def _help_by_command(parser: argparse.ArgumentParser) -> dict[str, str]:
    """Recover each subcommand's one-line help from the subparsers action."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return {
                choice.dest: (choice.help or "")
                for choice in action._choices_actions
            }
    return {}


def _option_token(flag: str, value: Any) -> str:
    """Render an option and its value as one token, so a value starting
    with `-` cannot be re-parsed as a flag."""
    if flag.startswith("--"):
        return f"{flag}={value}"
    return f"{flag}{value}"  # short options take attached values


def _argv_for(
    command: str, arguments: dict[str, Any], parser: argparse.ArgumentParser
) -> list[str]:
    """Turn a tool-call argument dict back into a gdoc argv list.

    Option values are rendered as `--flag=value` and positionals follow a
    `--` separator, so user text that begins with `-` cannot be re-parsed
    as a flag.
    """
    actions = {
        a.dest: a
        for a in parser._actions
        if a.dest not in _SKIP_DESTS
        and not isinstance(a, (argparse._HelpAction, argparse._VersionAction))
    }

    unknown = set(arguments) - set(actions)
    if unknown:
        raise ValueError(f"unknown argument(s): {', '.join(sorted(unknown))}")

    positionals: list[str] = []
    options: list[str] = []
    skipped_positional: str | None = None

    for dest, action in actions.items():
        provided = dest in arguments and arguments[dest] is not None

        if not action.option_strings:
            # Positionals are matched by position, so a gap cannot be
            # expressed: a later value would silently shift into the
            # earlier slot (for `edit`, turning a replacement into a
            # deletion).
            if not provided:
                if skipped_positional is None:
                    skipped_positional = dest
                continue
            if skipped_positional is not None:
                raise ValueError(
                    f"`{dest}` requires `{skipped_positional}` to be set too"
                )
            value = arguments[dest]
            if isinstance(value, list):
                positionals.extend(str(v) for v in value)
            else:
                positionals.append(str(value))
            continue

        if not provided:
            continue
        value = arguments[dest]

        flag = max(action.option_strings, key=len)
        if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            # Strict: a truthy string like "false" must not enable an
            # opt-in flag — several of them guard destructive behavior.
            if not isinstance(value, bool):
                raise ValueError(f"`{dest}` must be a boolean")
            if value:
                options.append(flag)
        elif isinstance(action, argparse._AppendAction) and isinstance(value, list):
            for item in value:
                options.append(_option_token(flag, item))
        elif isinstance(value, list):
            # nargs="+"/"*" options have no per-value `=` form
            options.append(flag)
            options.extend(str(v) for v in value)
        else:
            options.append(_option_token(flag, value))

    argv = [command, *options]
    if positionals:
        argv.append("--")
        argv.extend(positionals)
    return argv


def call_command(
    command: str, arguments: dict[str, Any]
) -> tuple[str, str, int]:
    """Run a gdoc subcommand in-process, capturing its output.

    Returns (stdout, stderr, exit_code).
    """
    from gdoc.cli import build_parser, run_argv

    parser = build_parser()
    subparser = _subparsers(parser).get(command)
    if subparser is None:
        raise ValueError(f"unknown command: {command}")

    _reject_local_paths(command, arguments)
    _reject_stdin_sentinels(command, arguments)

    # Schema `required` cannot force a boolean to be true, so guard here:
    # with stdin detached, confirm_destructive() can never prompt.
    if command in _FORCE_REQUIRED and not arguments.get("force"):
        raise ValueError(
            "`force: true` is required: deletion cannot prompt for "
            "confirmation over MCP"
        )

    if command == "new" and arguments.get("text"):
        for target in _IMAGE_REF.findall(arguments["text"]):
            if not target.startswith(("http://", "https://")):
                raise ValueError(
                    f"local image reference in text: {target!r} — over MCP, "
                    "images must be http(s) URLs"
                )

    file_arg = _TEXT_TO_FILE.get(command)
    if (
        file_arg
        and arguments.get("text") is None
        and _file_arg_required(subparser, file_arg)
    ):
        raise ValueError("`text` is required: the markdown content, inline")

    from gdoc.util import account_context, resolve_account

    # Scope the account like a fresh CLI invocation: an explicit account
    # pins this call only, and an unpinned call resolves the configured
    # default once at call entry (service caches key on the resolved
    # account, so a default changed mid-serve is picked up on the next
    # call). Resolving once, not per service access, keeps a single call
    # from straddling a `gdoc auth --set-default` made while it runs.
    with (
        account_context(arguments.get("account") or resolve_account()),
        _materialised_text(command, arguments) as prepared,
    ):
        argv = _argv_for(command, prepared, subparser)

        out, err = io.StringIO(), io.StringIO()
        # A tool call must never read the server's stdin — that is the
        # JSON-RPC protocol stream, and commands that read it (`edit`
        # with "-", `cells --stdin`) would swallow queued messages and
        # then hang until the client hangs up.
        real_stdin, sys.stdin = sys.stdin, io.StringIO("")
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = run_argv(argv, check_updates=False)
                except SystemExit as e:  # argparse usage errors exit, not raise
                    code = e.code if isinstance(e.code, int) else 1
        finally:
            sys.stdin = real_stdin
    return out.getvalue(), err.getvalue(), code


def _reject_local_paths(command: str, arguments: dict[str, Any]) -> None:
    """Refuse parameters that name files on the server's machine."""
    used = {
        p for p in _LOCAL_PATH_PARAMS.get(command, frozenset())
        if arguments.get(p) is not None
    }
    if used:
        hint = " — use `text` for inline content" if command in _TEXT_TO_FILE else ""
        raise ValueError(
            f"{', '.join(sorted(used))}: local file paths are not available "
            f"over MCP{hint}"
        )
    for dest, removed in _LOCAL_PATH_CHOICES.get(command, {}).items():
        value = arguments.get(dest)
        if isinstance(value, str) and value in removed:
            raise ValueError(
                f"{dest}={value} writes a local file and is not available "
                "over MCP"
            )


def _reject_stdin_sentinels(command: str, arguments: dict[str, Any]) -> None:
    """Refuse the CLI's `-` (stdin) convention, which MCP cannot honour."""
    for param in _STDIN_SENTINEL_PARAMS.get(command, frozenset()):
        if arguments.get(param) == "-":
            raise ValueError(
                f'`{param}: "-"` (read from stdin) is not available over '
                "MCP; pass the text itself"
            )


@contextlib.contextmanager
def _materialised_text(command: str, arguments: dict[str, Any]):
    """Swap an inline `text` argument for a temp file the CLI can read."""
    file_arg = _TEXT_TO_FILE.get(command)
    if file_arg is None:
        yield arguments
        return
    if arguments.get("text") is None:
        yield {k: v for k, v in arguments.items() if k != "text"}
        return

    prepared = {k: v for k, v in arguments.items() if k != "text"}
    # delete=False so the CLI can reopen the path by name — Windows locks
    # a NamedTemporaryFile that is still open.
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".md", encoding="utf-8", delete=False
    )
    try:
        with handle:
            handle.write(arguments["text"])
        prepared[file_arg] = handle.name
        yield prepared
    finally:
        with contextlib.suppress(OSError):
            os.unlink(handle.name)


# stderr lines that are noise on every call rather than news.
_NOTE_NOISE = frozenset({
    "--- no changes ---",
    "---",
    "",
    # auth.py prints this hint when no default account is configured;
    # relaying it invites the model to pass account="default", which is
    # not a real account name.
    "account: default (use --account to switch)",
})


def _clean_notes(stderr: str) -> str:
    """Drop pre-flight banners that say nothing happened.

    `pre_flight()` writes a change summary to stderr before most commands.
    Real changes are worth relaying to the model; "no changes" is noise on
    every single tool call.
    """
    lines = [
        line for line in stderr.splitlines()
        if line.strip() not in _NOTE_NOISE
    ]
    return "\n".join(lines).strip()


def _err_lines(stderr: str) -> list[str]:
    """The ERR: lines — the CLI's actual error messages — from stderr."""
    return [
        line for line in stderr.splitlines() if line.startswith("ERR:")
    ]


class _InvalidParamsError(Exception):
    """Maps to JSON-RPC error -32602 (invalid params)."""


class MCPServer:
    """Minimal MCP server over newline-delimited JSON-RPC on stdio."""

    def __init__(
        self,
        *,
        read_only: bool = False,
        allow: set[str] | None = None,
        account: str | None = None,
    ) -> None:
        self.tools = build_tools(read_only=read_only, allow=allow)
        self.account = account
        self.protocol_version = DEFAULT_PROTOCOL_VERSION

    # -- request handlers ------------------------------------------------

    def handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        if requested in SUPPORTED_PROTOCOL_VERSIONS:
            self.protocol_version = requested
        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "gdoc", "version": __version__},
            "instructions": (
                "gdoc reads and edits Google Docs and Drive files. Pass a "
                "document URL or bare ID as `doc`. Output is terse by design; "
                "set `json` for machine-readable output."
            ),
        }

    def handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": list(self.tools.values())}

    def handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        arguments = params.get("arguments") or {}

        if name not in self.tools:
            # an unknown tool is a protocol error (-32602), not a tool result
            raise _InvalidParamsError(f"no such tool: {name}")

        # `is None` rather than `not in`: a client that serializes unset
        # optionals as null must not bypass the server-wide account.
        if self.account and arguments.get("account") is None:
            arguments = {**arguments, "account": self.account}

        command = _command_name(name)
        try:
            stdout, stderr, code = call_command(command, arguments)
        except Exception as e:  # surface as a tool error, never kill the server
            return self._text_result(f"ERR: {e}", is_error=True)

        ok = code == 0 or (
            command in _DIFF_EXIT_COMMANDS and code == 1 and not _err_lines(stderr)
        )
        if not ok:
            errs = _err_lines(stderr)
            body = (
                "\n".join(errs)
                or _clean_notes(stderr)
                or stdout.strip()
                or f"exit code {code}"
            )
            return self._text_result(body, is_error=True)

        # Notes travel as a second content item so machine-readable stdout
        # (e.g. `json: true`) stays parseable on its own.
        content = [{"type": "text", "text": stdout.strip() or "OK"}]
        notes = _clean_notes(stderr)
        if notes:
            content.append({"type": "text", "text": f"--- notes ---\n{notes}"})
        return {"content": content, "isError": False}

    @staticmethod
    def _text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        }

    # -- dispatch --------------------------------------------------------

    def dispatch(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        params = message.get("params") or {}
        msg_id = message.get("id")
        is_notification = "id" not in message

        handlers = {
            "initialize": self.handle_initialize,
            "tools/list": self.handle_tools_list,
            "tools/call": self.handle_tools_call,
            "ping": lambda _params: {},
        }

        if method in ("notifications/initialized", "notifications/cancelled"):
            return None

        handler = handlers.get(method)
        if handler is None:
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }

        try:
            result = handler(params)
        except _InvalidParamsError as e:
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": str(e)},
            }
        except Exception as e:
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(e)},
            }

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    # -- transport -------------------------------------------------------

    def serve(self, stdin=None, stdout=None) -> int:
        stdin = stdin or sys.stdin
        protocol_out = stdout or sys.stdout

        # Anything that slips a print() past the per-call capture must not
        # corrupt the JSON-RPC stream.
        real_stdout, sys.stdout = sys.stdout, sys.stderr
        try:
            for line in stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._write(
                        protocol_out,
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "parse error"},
                        },
                    )
                    continue

                if isinstance(message, list):
                    # JSON-RPC batch (protocol revisions before 2025-06-18):
                    # one request array gets one response array.
                    responses = [
                        self.dispatch(item) if isinstance(item, dict)
                        else self._invalid_request()
                        for item in message
                    ]
                    responses = [r for r in responses if r is not None]
                    if not message:
                        self._write(protocol_out, self._invalid_request())
                    elif responses:
                        self._write(protocol_out, responses)
                    continue

                if not isinstance(message, dict):
                    self._write(protocol_out, self._invalid_request())
                    continue

                response = self.dispatch(message)
                if response is not None:
                    self._write(protocol_out, response)
        except (BrokenPipeError, KeyboardInterrupt):
            pass
        finally:
            sys.stdout = real_stdout
        return 0

    @staticmethod
    def _invalid_request() -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "invalid request"},
        }

    @staticmethod
    def _write(stream, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        stream.write(json.dumps(payload) + "\n")
        stream.flush()

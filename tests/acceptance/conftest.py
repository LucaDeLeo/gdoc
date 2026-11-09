"""Offline task harness: real CLI/MCP dispatch, isolated state, no sockets."""

import contextlib
import io
import json
import socket
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gdoc import cli, mcp, state
from gdoc.api import docs, drive
from gdoc.mdparse import utf16_len

RECORDS = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    if report.when == "call":
        record = next((r for r in RECORDS if r["test"] == item.nodeid), None)
        if record is None:
            record = {"test": item.nodeid, "interface": "unit", "commands": []}
            RECORDS.append(record)
        record["outcome"] = report.outcome
        record["failure"] = str(report.longrepr) if report.failed else None
        record["test_elapsed_seconds"] = report.duration


def pytest_sessionfinish(session, exitstatus):
    destination = session.config.getoption("--acceptance-results", default=None)
    if destination:
        Path(destination).write_text(
            json.dumps(
                {
                    "source_sha": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], text=True
                    ).strip(),
                    "timing_kind": "offline_scripted",
                    "pytest_exit_status": exitstatus,
                    "records": RECORDS,
                },
                indent=2,
            )
            + "\n"
        )


def paragraph(text, start=1, style=None, bullet=None):
    end = start + utf16_len(text)
    p = {
        "elements": [
            {"startIndex": start, "endIndex": end, "textRun": {"content": text}}
        ],
        "paragraphStyle": {"namedStyleType": style or "NORMAL_TEXT"},
    }
    if bullet:
        p["bullet"] = bullet
    return {"startIndex": start, "endIndex": end, "paragraph": p}


def tab(tab_id, title, content):
    return {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {"body": {"content": content}},
    }


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Acceptance tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("gdoc.util.get_default_account", lambda: None)
    for name in ("GDOC_ALLOW_COMMANDS", "GDOC_ACCOUNT"):
        monkeypatch.delenv(name, raising=False)


class Scenario:
    """Captures API requests; it deliberately does not emulate native semantics."""

    def __init__(self, monkeypatch, tmp_path, interface, nodeid):
        self.interface, self.tmp_path = interface, tmp_path
        self.document = {
            "documentId": "synthetic",
            "revisionId": "r1",
            "tabs": [tab("draft", "Draft", [paragraph("Original sentence.\n")])],
        }
        self.version = 1
        self.export = "Original sentence.\n"
        self.service = MagicMock()

        def snapshot(**kwargs):
            fields = self.service.documents.return_value.get.call_args.kwargs.get(
                "fields"
            )
            if fields == "documentId,title,revisionId":
                return {
                    k: v for k, v in self.document.items() if k in fields.split(",")
                }
            return deepcopy(self.document)

        self.service.documents.return_value.get.return_value.execute.side_effect = (
            snapshot
        )
        batch_execute = (
            self.service.documents.return_value.batchUpdate.return_value.execute
        )
        batch_execute.return_value = {"writeControl": {"requiredRevisionId": "r2"}}
        monkeypatch.setattr(docs, "get_docs_service", lambda: self.service)
        monkeypatch.setattr(
            drive,
            "get_file_version",
            lambda *a, **kw: {
                "version": self.version,
                "mimeType": "application/vnd.google-apps.document",
            },
        )
        monkeypatch.setattr(
            drive,
            "get_file_info",
            lambda *a, **kw: {"name": "Synthetic harbor", "version": self.version},
        )
        monkeypatch.setattr(drive, "export_doc", lambda *a, **kw: self.export)
        monkeypatch.setattr("gdoc.api.comments.list_comments", lambda *a, **kw: [])
        self.boundaries = {}
        from gdoc.api import comments

        for module, name in (
            (drive, "get_file_version"),
            (drive, "get_file_info"),
            (drive, "export_doc"),
            (comments, "list_comments"),
        ):
            boundary = MagicMock(side_effect=getattr(module, name))
            monkeypatch.setattr(module, name, boundary)
            self.boundaries[name] = boundary
        self.record = {
            "test": nodeid,
            "interface": interface,
            "commands": [],
            "evidence": "request_assertions",
            "retries": 0,
        }
        RECORDS.append(self.record)

    @property
    def batches(self):
        return [
            call.kwargs["body"]
            for call in self.service.documents.return_value.batchUpdate.call_args_list
        ]

    def call(self, command, **arguments):
        arguments = {"doc": "synthetic", **arguments}
        start = time.perf_counter()
        if self.interface == "mcp":
            response = mcp.MCPServer().dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "gdoc_" + command.replace("-", "_"),
                        "arguments": arguments,
                    },
                }
            )
            result = response.get("result", {})
            blocks = result.get("content", [])
            output = blocks[0]["text"] if blocks else ""
            error = (
                json.dumps(response["error"])
                if "error" in response
                else "\n".join(c["text"] for c in blocks[1:])
            )
            code = int(bool("error" in response or result.get("isError")))
            sequence = {
                "tool": "gdoc_" + command.replace("-", "_"),
                "arguments": arguments,
            }
        else:
            prepared = dict(arguments)
            if command in ("write", "insert"):
                path = self.tmp_path / "rewrite.md"
                path.write_text(prepared.pop("text"))
                prepared["file"] = str(path)
            # Build CLI argv independently, not through MCP's argv adapter.
            positionals = {
                "cat": ("doc",),
                "structure": ("doc",),
                "info": ("doc",),
                "write": ("doc", "file"),
                "insert": ("doc", "file"),
                "export": ("doc",),
                "pull": ("doc", "file"),
                "edit": ("doc", "old_text", "new_text"),
                "comment": ("doc", "text"),
                "images": ("doc",),
                "toc": ("doc",),
            }[command]
            argv = [command] + [str(prepared.pop(k)) for k in positionals]
            for key, value in prepared.items():
                flag = "--" + key.replace("_", "-")
                if value is True:
                    argv.append(flag)
                elif value is not False and value is not None:
                    argv.extend([flag, str(value)])
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = cli.run_argv(argv, check_updates=False)
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
            output, error = out.getvalue(), err.getvalue()
            sequence = {"argv": argv}
            if command in ("write", "insert"):
                sequence["input_markdown"] = arguments["text"]
        self.record["commands"].append(
            {
                **sequence,
                "exit_code": code,
                "elapsed_seconds": time.perf_counter() - start,
                "output_bytes": len(output.encode()),
                "stderr_bytes": len(error.encode()),
                "output": output,
                "stderr": error,
            }
        )
        batch_execute = (
            self.service.documents.return_value.batchUpdate.return_value.execute
        )
        self.record["docs_api_attempts"] = (
            self.service.documents.return_value.get.return_value.execute.call_count
            + batch_execute.call_count
        )
        self.record["mocked_boundary_calls"] = {
            name: mock.call_count for name, mock in self.boundaries.items()
        }
        self.record["command_count"] = len(self.record["commands"])
        self.record["refusals_or_errors"] = sum(
            c["exit_code"] != 0 for c in self.record["commands"]
        )
        return code, output, error

    def ok(self, command, **arguments):
        code, output, error = self.call(command, **arguments)
        assert code == 0, output + error
        return output


@pytest.fixture(params=["cli", "mcp"])
def scenario(request, monkeypatch, tmp_path):
    return Scenario(monkeypatch, tmp_path, request.param, request.node.nodeid)

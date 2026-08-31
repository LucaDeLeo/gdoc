"""Tests for the MCP server (`gdoc mcp`)."""

import io
import json
import os

import pytest

from gdoc import mcp


@pytest.fixture(autouse=True)
def _clean_gdoc_env(monkeypatch):
    """Ambient GDOC_* settings and the machine's configured default
    account must not change tool construction or account tracking."""
    monkeypatch.delenv("GDOC_ALLOW_COMMANDS", raising=False)
    monkeypatch.delenv("GDOC_ACCOUNT", raising=False)
    monkeypatch.setattr("gdoc.util.get_default_account", lambda: None)


# -- tool construction ---------------------------------------------------


def test_build_tools_exposes_prefixed_names():
    tools = mcp.build_tools()
    assert "gdoc_cat" in tools
    assert "gdoc_comment_info" in tools
    # Not exposed: interactive, machine-wide, or local-path commands.
    assert "gdoc_auth" not in tools
    assert "gdoc_update" not in tools
    assert "gdoc_pull" not in tools


def test_read_only_drops_write_commands():
    tools = mcp.build_tools(read_only=True)
    assert "gdoc_cat" in tools
    assert "gdoc_edit" not in tools
    assert "gdoc_share" not in tools


def test_allow_list_restricts_surface():
    tools = mcp.build_tools(allow={"cat", "ls"})
    assert set(tools) == {"gdoc_cat", "gdoc_ls"}


def test_schema_derived_from_argparse():
    schema = mcp.build_tools(allow={"edit"})["gdoc_edit"]["inputSchema"]
    props = schema["properties"]

    assert schema["required"] == ["doc"]
    assert props["doc"]["type"] == "string"
    assert props["old_text"]["type"] == "string"
    assert props["all"]["type"] == "boolean"  # store_true
    assert props["table"]["type"] == "integer"  # type=int
    # Output plumbing is not a tool parameter.
    assert "verbose" not in props
    assert "func" not in props


def test_choices_become_enum():
    props = mcp.build_tools(allow={"insert"})["gdoc_insert"]["inputSchema"][
        "properties"
    ]
    assert props["position"]["enum"] == ["start", "end"]


def test_write_tools_accept_inline_text():
    schema = mcp.build_tools(allow={"write"})["gdoc_write"]["inputSchema"]
    assert "text" in schema["properties"]
    # The local `file` path is replaced by `text`, which inherits its
    # requiredness (write's file positional was mandatory).
    assert "file" not in schema["properties"]
    assert schema["required"] == ["doc", "text"]


def test_new_accepts_optional_inline_text():
    schema = mcp.build_tools(allow={"new"})["gdoc_new"]["inputSchema"]
    # `new --file` was optional, so inline text is too.
    assert "text" in schema["properties"]
    assert "file_path" not in schema["properties"]
    assert "text" not in schema.get("required", [])


def test_cells_is_classified_as_write():
    assert "gdoc_cells" not in mcp.build_tools(read_only=True)
    tool = mcp.build_tools(allow={"cells"})["gdoc_cells"]
    assert "Writes to Google Docs/Drive." in tool["description"]


def test_image_commands_are_not_exposed():
    tools = mcp.build_tools()
    # Both require a local image path a chat client cannot provide.
    assert "gdoc_insert_image" not in tools
    assert "gdoc_replace_image" not in tools


def test_local_path_params_are_stripped_from_schemas():
    tools = mcp.build_tools()
    props = lambda name: tools[name]["inputSchema"]["properties"]  # noqa: E731
    assert "old_file" not in props("gdoc_edit")
    assert "new_file" not in props("gdoc_edit")
    assert "file" not in props("gdoc_insert")
    assert "file" not in props("gdoc_cells")
    assert "stdin" not in props("gdoc_cells")
    assert "file" not in props("gdoc_diff")
    assert "out" not in props("gdoc_diff")
    assert "download" not in props("gdoc_images")


def test_html_diff_format_is_not_offered():
    props = mcp.build_tools(allow={"diff"})["gdoc_diff"]["inputSchema"][
        "properties"
    ]
    # html renders to a local file the client cannot see.
    assert "html" not in props["format"]["enum"]
    assert "color" in props["format"]["enum"]


def test_insert_tab_is_optional_via_url():
    schema = mcp.build_tools(allow={"insert"})["gdoc_insert"]["inputSchema"]
    # `insert --tab` is no longer parser-required: a `doc` URL carrying
    # `?tab=` satisfies it (cmd_insert errors at runtime when neither is
    # given).
    assert "tab" not in schema.get("required", [])
    assert "doc" in schema["required"]


def test_parser_required_flag_is_marked_required():
    # No exposed command carries required=True on an option anymore, so pin
    # the action.required derivation branch with a synthetic parser.
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("doc")
    parser.add_argument("--tab", required=True)
    schema = mcp._schema_for("insert", parser)
    assert "tab" in schema["required"]


def test_cells_requires_a_value():
    schema = mcp.build_tools(allow={"cells"})["gdoc_cells"]["inputSchema"]
    # With --file/--stdin hidden, -v/--value is the only data source; a
    # call without it would always fail at runtime.
    assert "value" in schema["required"]
    assert schema["properties"]["value"]["minItems"] == 1


def test_suggest_requires_the_text_pair():
    schema = mcp.build_tools(allow={"suggest"})["gdoc_suggest"]["inputSchema"]
    # With --old-file/--new-file hidden and no cell mode, the text pair is
    # the only data source; a call without it would always fail at runtime.
    assert "old_text" in schema["required"]
    assert "new_text" in schema["required"]


def test_delete_comment_requires_force():
    schema = mcp.build_tools(allow={"delete-comment"})[
        "gdoc_delete_comment"
    ]["inputSchema"]
    # With stdin detached there is no confirmation prompt to answer.
    assert "force" in schema["required"]


def test_env_allowlist_filters_tools(monkeypatch):
    monkeypatch.setenv("GDOC_ALLOW_COMMANDS", "mcp,cat,ls")
    tools = mcp.build_tools()
    # run_argv enforces the env allowlist per call; tools/list must agree.
    assert set(tools) == {"gdoc_cat", "gdoc_ls"}


def test_write_command_description_flags_mutation():
    tool = mcp.build_tools(allow={"share"})["gdoc_share"]
    assert "Writes to Google Docs/Drive." in tool["description"]


# -- argv construction ---------------------------------------------------


def _subparser(command):
    from gdoc.cli import build_parser

    return mcp._subparsers(build_parser())[command]


def test_argv_orders_options_before_positionals():
    argv = mcp._argv_for(
        "edit",
        {"doc": "DOC1", "old_text": "a", "new_text": "b", "all": True},
        _subparser("edit"),
    )
    assert argv[0] == "edit"
    assert "--all" in argv
    # Positionals keep parser declaration order and follow every option.
    assert argv[-3:] == ["DOC1", "a", "b"]
    assert argv.index("--all") < argv.index("DOC1")


def test_argv_passes_option_values():
    argv = mcp._argv_for(
        "cat", {"doc": "DOC1", "tab": "Notes"}, _subparser("cat"),
    )
    # Attached form, so a value starting with "-" cannot become a flag.
    assert "--tab=Notes" in argv


def test_argv_shields_dash_prefixed_text():
    """User text like "--all" must survive the round trip as data."""
    from gdoc.cli import build_parser

    argv = mcp._argv_for(
        "edit",
        {"doc": "DOC1", "old_text": "--all", "new_text": "-x"},
        _subparser("edit"),
    )
    args = build_parser().parse_args(argv)
    assert args.old_text == "--all"
    assert args.new_text == "-x"
    assert args.all is False


def test_argv_rejects_skipped_middle_positional():
    """A gap in positionals must error, not silently shift values."""
    with pytest.raises(ValueError, match="requires `old_text`"):
        mcp._argv_for(
            "edit", {"doc": "D", "new_text": "b"}, _subparser("edit"),
        )


def test_boolean_flags_require_real_booleans():
    """A truthy string like "false" must not enable an opt-in flag —
    some of them (write --force-collapse-tabs) guard destructive paths."""
    with pytest.raises(ValueError, match="must be a boolean"):
        mcp._argv_for(
            "write",
            {"doc": "D", "file": "f", "force_collapse_tabs": "false"},
            _subparser("write"),
        )
    argv = mcp._argv_for(
        "write",
        {"doc": "D", "file": "f", "force_collapse_tabs": True},
        _subparser("write"),
    )
    assert "--force-collapse-tabs" in argv


def test_argv_omits_false_booleans():
    argv = mcp._argv_for("edit", {"doc": "D", "all": False}, _subparser("edit"))
    assert "--all" not in argv


def test_argv_rejects_unknown_arguments():
    with pytest.raises(ValueError, match="unknown argument"):
        mcp._argv_for("cat", {"doc": "D", "nope": 1}, _subparser("cat"))


def test_inline_text_is_written_to_a_temp_file():
    with mcp._materialised_text("write", {"doc": "D", "text": "# Hi"}) as prepared:
        assert "text" not in prepared
        path = prepared["file"]
        with open(path, encoding="utf-8") as handle:
            assert handle.read() == "# Hi"
    assert not os.path.exists(path)  # cleaned up after the call


def test_local_file_params_are_rejected():
    with pytest.raises(ValueError, match="local file paths"):
        mcp.call_command("write", {"doc": "D", "text": "x", "file": "f"})
    with pytest.raises(ValueError, match="local file paths"):
        mcp.call_command(
            "edit", {"doc": "D", "old_text": "a", "new_file": "/etc/passwd"},
        )


def test_html_diff_choice_is_rejected():
    with pytest.raises(ValueError, match="writes a local file"):
        mcp.call_command("diff", {"doc": "D", "rev": "prev", "format": "html"})


def test_write_requires_inline_text():
    with pytest.raises(ValueError, match="`text` is required"):
        mcp.call_command("write", {"doc": "D"})


def test_new_rejects_local_image_references(mocker):
    """`new` imports images relative to the materialised temp file, so a
    local path in inline text could still read server files."""
    with pytest.raises(ValueError, match="local image reference"):
        mcp.call_command(
            "new", {"title": "T", "text": "hi ![x](/tmp/private.png)"},
        )
    with pytest.raises(ValueError, match="local image reference"):
        mcp.call_command(
            "new", {"title": "T", "text": "![x](../secret.png)"},
        )
    run = mocker.patch("gdoc.cli.run_argv", return_value=0)
    mcp.call_command(
        "new", {"title": "T", "text": "![x](https://example.com/i.png)"},
    )
    assert run.called


def test_alternative_requirements_are_stated_in_descriptions():
    tools = mcp.build_tools()
    assert "Exactly one of `rev` or `since`" in tools["gdoc_diff"]["description"]
    assert (
        "Exactly one of `email`, `domain`, or `anyone`"
        in tools["gdoc_share"]["description"]
    )


def test_delete_comment_without_true_force_is_rejected(mocker):
    # Schema `required` cannot force a boolean to be true.
    with pytest.raises(ValueError, match="`force: true` is required"):
        mcp.call_command("delete-comment", {"doc": "D", "comment_id": "c1"})
    with pytest.raises(ValueError, match="`force: true` is required"):
        mcp.call_command(
            "delete-comment", {"doc": "D", "comment_id": "c1", "force": False},
        )
    run = mocker.patch("gdoc.cli.run_argv", return_value=0)
    mcp.call_command(
        "delete-comment", {"doc": "D", "comment_id": "c1", "force": True},
    )
    assert run.called


# -- command execution ---------------------------------------------------


def test_call_command_captures_stdout(mocker):
    def fake_run(argv, check_updates=True):
        print("hello from gdoc")
        return 0

    mocker.patch("gdoc.cli.run_argv", side_effect=fake_run)
    stdout, stderr, code = mcp.call_command("cat", {"doc": "DOC1"})
    assert stdout.strip() == "hello from gdoc"
    assert code == 0


def test_call_command_reports_exit_code(mocker):
    def fake_run(argv, check_updates=True):
        print("ERR: nope", file=__import__("sys").stderr)
        return 3

    mocker.patch("gdoc.cli.run_argv", side_effect=fake_run)
    _, stderr, code = mcp.call_command("cat", {"doc": "DOC1"})
    assert code == 3
    assert "nope" in stderr


@pytest.fixture()
def _service_probe(mocker):
    """Run each tool call through a fake command that grabs the Drive
    service, with build/credentials mocked to record the account used."""
    from gdoc import api

    api.clear_service_caches()
    mocker.patch(
        "gdoc.auth.get_credentials", side_effect=lambda account: f"creds:{account}"
    )
    built = {}

    def fake_build(api_name, version, credentials):
        service = object()
        built[service] = credentials
        return service

    mocker.patch("gdoc.api.build", side_effect=fake_build)

    services = []

    def fake_run_argv(argv, check_updates=True):
        services.append(api.get_drive_service())
        return 0

    mocker.patch("gdoc.cli.run_argv", side_effect=fake_run_argv)
    yield services, built
    api.clear_service_caches()


def test_account_does_not_leak_between_calls(mocker, _service_probe):
    """A named account is scoped to its own tool call."""
    from gdoc import util

    services, built = _service_probe

    mcp.call_command("cat", {"doc": "D", "account": "work"})
    assert built[services[0]] == "creds:work"
    # Restored after the call, not left set until the next one.
    assert util.get_active_account() is None

    mcp.call_command("cat", {"doc": "D"})
    assert built[services[1]] == "creds:None"


def test_account_is_restored_when_a_call_raises(mocker):
    """A failing tool call must not leave its account pinned."""
    from gdoc import util

    mocker.patch("gdoc.cli.run_argv", side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        mcp.call_command("cat", {"doc": "D", "account": "work"})
    assert util.get_active_account() is None


def test_repeat_calls_on_one_account_keep_cached_services(_service_probe):
    """Repeat calls on one account reuse their cached service objects."""
    services, _ = _service_probe

    mcp.call_command("cat", {"doc": "D", "account": "work"})
    mcp.call_command("cat", {"doc": "D", "account": "work"})
    assert services[0] is services[1]


def test_default_account_change_reaches_next_unpinned_call(mocker, _service_probe):
    """`gdoc auth --set-default` in another terminal must not leave a
    running server on the old default's cached credentials."""
    services, built = _service_probe
    default = mocker.patch("gdoc.util.get_default_account", return_value="a")

    mcp.call_command("cat", {"doc": "D"})
    mcp.call_command("cat", {"doc": "D"})
    assert services[0] is services[1]  # default unchanged: cache kept
    assert built[services[0]] == "creds:a"

    default.return_value = "b"
    mcp.call_command("cat", {"doc": "D"})
    assert services[2] is not services[1]  # default changed: new service
    assert built[services[2]] == "creds:b"


@pytest.mark.parametrize("command", ["edit", "suggest"])
@pytest.mark.parametrize("param", ["old_text", "new_text"])
def test_stdin_sentinel_dash_is_rejected(mocker, command, param):
    """The CLI's `-` (stdin) convention cannot work over MCP: stdin is
    shielded to an empty string, so `-` would silently resolve to "" and
    turn a replacement into a deletion of the anchor."""
    run = mocker.patch("gdoc.cli.run_argv", return_value=0)
    arguments = {"doc": "D", "old_text": "x", "new_text": "y", param: "-"}
    with pytest.raises(ValueError, match="stdin.*not available over MCP"):
        mcp.call_command(command, arguments)
    run.assert_not_called()


def test_stdin_is_shielded_from_tool_calls(mocker):
    """A command that reads stdin must not consume the protocol stream."""
    import sys

    seen = {}

    def fake_run(argv, check_updates=True):
        seen["stdin"] = sys.stdin.read()
        return 0

    mocker.patch("gdoc.cli.run_argv", side_effect=fake_run)
    real_stdin = sys.stdin
    protocol_stream = io.StringIO('{"jsonrpc": "2.0", "id": 9}\n')
    sys.stdin = protocol_stream
    try:
        mcp.call_command("cat", {"doc": "D"})
    finally:
        sys.stdin = real_stdin
    assert seen["stdin"] == ""
    # The protocol stream was left untouched for the transport loop.
    assert protocol_stream.tell() == 0


def test_clean_notes_drops_no_change_banner():
    assert mcp._clean_notes("---\n--- no changes ---\n") == ""
    assert "doc edited" in mcp._clean_notes(
        "--- since last interaction ---\n ✎ doc edited by A\n---\n"
    )


def test_clean_notes_drops_account_hint():
    # Relaying this hint teaches the model to pass account="default",
    # which is not a real account name.
    assert mcp._clean_notes(
        "account: default (use --account to switch)\n"
    ) == ""


# -- protocol ------------------------------------------------------------


def test_initialize_echoes_known_protocol_version():
    server = mcp.MCPServer()
    result = server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "gdoc"
    assert "tools" in result["capabilities"]


def test_initialize_falls_back_for_unknown_version():
    server = mcp.MCPServer()
    result = server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "1999-01-01"},
    })["result"]
    assert result["protocolVersion"] == mcp.DEFAULT_PROTOCOL_VERSION


def test_notifications_get_no_response():
    server = mcp.MCPServer()
    assert server.dispatch({
        "jsonrpc": "2.0", "method": "notifications/initialized",
    }) is None


def test_unknown_method_returns_method_not_found():
    server = mcp.MCPServer()
    response = server.dispatch({"jsonrpc": "2.0", "id": 7, "method": "nope"})
    assert response["error"]["code"] == -32601


def test_tools_call_unknown_tool_is_invalid_params():
    server = mcp.MCPServer()
    response = server.dispatch({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "gdoc_nope", "arguments": {}},
    })
    # The MCP spec treats an unknown tool as a protocol error.
    assert response["error"]["code"] == -32602
    assert "no such tool" in response["error"]["message"]


def test_tools_call_returns_stdout(mocker):
    mocker.patch(
        "gdoc.mcp.call_command", return_value=("doc body", "", 0),
    )
    server = mcp.MCPServer()
    result = server.dispatch({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "gdoc_cat", "arguments": {"doc": "D"}},
    })["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"] == "doc body"


def test_tools_call_surfaces_failure(mocker):
    mocker.patch(
        "gdoc.mcp.call_command", return_value=("", "ERR: Document not found", 1),
    )
    server = mcp.MCPServer()
    result = server.dispatch({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "gdoc_cat", "arguments": {"doc": "D"}},
    })["result"]
    assert result["isError"] is True
    assert "Document not found" in result["content"][0]["text"]


def test_error_body_is_the_err_line_not_the_banner(mocker):
    mocker.patch(
        "gdoc.mcp.call_command",
        return_value=("", "--- no changes ---\nERR: Document not found", 1),
    )
    server = mcp.MCPServer()
    result = server.dispatch({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "gdoc_cat", "arguments": {"doc": "D"}},
    })["result"]
    assert result["content"][0]["text"] == "ERR: Document not found"


def test_diff_exit_one_means_differences_not_failure(mocker):
    mocker.patch(
        "gdoc.mcp.call_command",
        return_value=("- old\n+ new", "--- no changes ---\n", 1),
    )
    server = mcp.MCPServer()
    result = server.dispatch({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "gdoc_diff", "arguments": {"doc": "D", "rev": "prev"}},
    })["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"] == "- old\n+ new"


def test_diff_exit_one_with_err_line_is_still_a_failure(mocker):
    mocker.patch(
        "gdoc.mcp.call_command", return_value=("", "ERR: boom", 1),
    )
    server = mcp.MCPServer()
    result = server.dispatch({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "gdoc_diff", "arguments": {"doc": "D", "rev": "prev"}},
    })["result"]
    assert result["isError"] is True


def test_notes_travel_as_a_separate_content_item(mocker):
    """Machine-readable stdout must stay parseable on its own."""
    mocker.patch(
        "gdoc.mcp.call_command",
        return_value=(
            '{"title": "Doc"}',
            "--- since last interaction ---\n ✎ doc edited by A\n---\n",
            0,
        ),
    )
    server = mcp.MCPServer()
    result = server.dispatch({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "gdoc_info", "arguments": {"doc": "D", "json": True}},
    })["result"]
    assert json.loads(result["content"][0]["text"]) == {"title": "Doc"}
    assert result["content"][1]["text"].startswith("--- notes ---")


def test_account_default_is_applied(mocker):
    call = mocker.patch("gdoc.mcp.call_command", return_value=("ok", "", 0))
    server = mcp.MCPServer(account="work")
    server.dispatch({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "gdoc_cat", "arguments": {"doc": "D"}},
    })
    assert call.call_args[0][1]["account"] == "work"


def test_explicit_account_wins_over_default(mocker):
    call = mocker.patch("gdoc.mcp.call_command", return_value=("ok", "", 0))
    server = mcp.MCPServer(account="work")
    server.dispatch({
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "gdoc_cat", "arguments": {"doc": "D", "account": "home"}},
    })
    assert call.call_args[0][1]["account"] == "home"


def test_null_account_does_not_bypass_the_default(mocker):
    """Clients that serialize unset optionals as null keep the pin."""
    call = mocker.patch("gdoc.mcp.call_command", return_value=("ok", "", 0))
    server = mcp.MCPServer(account="work")
    server.dispatch({
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "gdoc_cat", "arguments": {"doc": "D", "account": None}},
    })
    assert call.call_args[0][1]["account"] == "work"


# -- transport -----------------------------------------------------------


def test_serve_round_trip(mocker):
    mocker.patch("gdoc.mcp.call_command", return_value=("body", "", 0))
    stdin = io.StringIO(
        json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        }) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        }) + "\n"
    )
    stdout = io.StringIO()

    assert mcp.MCPServer().serve(stdin=stdin, stdout=stdout) == 0

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    # The notification must not produce a response.
    assert [r["id"] for r in responses] == [1, 2]
    assert len(responses[1]["result"]["tools"]) > 10


def test_serve_reports_malformed_json():
    stdout = io.StringIO()
    mcp.MCPServer().serve(stdin=io.StringIO("not json\n"), stdout=stdout)
    assert json.loads(stdout.getvalue())["error"]["code"] == -32700


def test_serve_answers_batches_with_an_array():
    stdin = io.StringIO(json.dumps([
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ]) + "\n")
    stdout = io.StringIO()
    mcp.MCPServer().serve(stdin=stdin, stdout=stdout)
    response = json.loads(stdout.getvalue())
    assert isinstance(response, list)
    assert [r["id"] for r in response] == [1, 2]


def test_serve_rejects_non_object_frames():
    stdout = io.StringIO()
    mcp.MCPServer().serve(stdin=io.StringIO("42\n"), stdout=stdout)
    assert json.loads(stdout.getvalue())["error"]["code"] == -32600


def test_serve_protects_the_protocol_stream_from_stray_prints(mocker):
    """A print() outside the per-call capture must not corrupt stdout."""
    import sys

    def leaky(*args, **kwargs):
        print("stray output", file=sys.stdout)
        return ("body", "", 0)

    mocker.patch("gdoc.mcp.call_command", side_effect=leaky)
    stdin = io.StringIO(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "gdoc_cat", "arguments": {"doc": "D"}},
    }) + "\n")
    stdout = io.StringIO()

    mcp.MCPServer().serve(stdin=stdin, stdout=stdout)
    # Every line on the protocol stream is still valid JSON-RPC.
    for line in stdout.getvalue().splitlines():
        assert json.loads(line)["jsonrpc"] == "2.0"

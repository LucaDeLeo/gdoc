"""Independent review round 5 of 4df611b (R5-xx): one normalization step per
bypass class, checked through CLI and MCP on the native model."""

import io
import json
from types import SimpleNamespace

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc

HEADER = "---\ngdoc: synthetic\ntitle: T\ngdoc-version: 1\n---\n"


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _read(route):
    return parse_frontmatter(route.ok("cat"))[1]


def _written(route, markdown):
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    route.ok("cat")
    return doc


def _refused(route, command, needle, **arguments):
    before, batches = _read(route), len(route.service.batches)
    code, output, error = route.call(command, **arguments)
    assert code != 0 and needle in output + error, output + error
    assert len(route.service.batches) == batches and _read(route) == before


@pytest.mark.parametrize("markdown", [
    # R5-01: a later fence line outside the item or quote is no closer
    "1. a\n\n   ```\n   x\n2. b\n\n## H\n\nprose\n\n```py\ncode\n```\n",
    "1. a\n\n   ```\n   x\n\n```\n",
    "1. a\n\n   ```\n   x\n       ```\n2. b\n",
    "> ```\n> x\n\n> ```\n",
    "- a\n\n  > ```\n  > x\n- b\n\n  ```\n",
])
def test_r5_01_a_fence_closes_only_inside_its_container(route, markdown):
    _written(route, "Alpha.\n")
    _refused(route, "write", "code fence gdoc doesn't read", text=markdown)


@pytest.mark.parametrize("markdown", [
    "1. a\n\n   ```\n   x\n\n   y\n   ```\n2. b\n",
    "> ```\n> x\n>\n> y\n> ```\nafter\n",
])
def test_r5_01_a_fence_closed_in_its_container_is_written(route, markdown):
    _written(route, markdown)


@pytest.mark.parametrize("markdown", [
    "1) ```\n   x\n   ```\n2. b\n\n## H\n\nprose\n",  # R5-06
    "-   > ```\nafter\n",  # R5-07
    "+ ```\nx\n",
    "*  1) ```\nx\n",
    "- >  > ~~~\nx\n",
])
def test_r5_06_07_every_marker_spelling_is_refused(route, markdown):
    _written(route, "Alpha.\n")
    _refused(route, "write", "code fence gdoc doesn't read", text=markdown)


@pytest.mark.parametrize("prefix", [
    "\n", "\r\n", " ", "\t", "﻿ ", "﻿﻿", "​", " \n",
    "<!-- x -->\n", "<!-- x -->", "<!-- a\nb -->", "\u200e", "\u00ad\n",
    "\u034f", "\ufe0f", "\U000e0100", "<!-- x -->\u034f", "\u3164",
])
def test_r5_02_a_prefixed_pulled_header_is_refused(route, prefix):
    _written(route, "Alpha.\n")
    _refused(route, "write", "may be a pulled file",
             text=prefix + HEADER + "Changed.\n")


@pytest.mark.parametrize("opener", [
    "---\u200b", "---\u034f", "---\u00a0", "---<!-- n -->", "-\n---", "*\n---",
    "-<!-- n -->---",
])
def test_r5_02_an_opener_with_anything_around_it_is_refused(route, opener):
    _written(route, "Alpha.\n")
    _refused(route, "write", "may be a pulled file",
             text=opener + HEADER[3:] + "Changed.\n")


@pytest.mark.parametrize("header", [
    "---\nGDOC: synthetic\ntitle: T\n---\n",
    "---\n\tGdoc-Version: 1\ntitle: T\n---\n",
    "---\n{gdoc: synthetic, title: T}\n---\n",
])
def test_r5_02_key_spellings_are_refused(route, header):
    _written(route, "Alpha.\n")
    _refused(route, "write", "may be a pulled file", text=header + "Changed.\n")


@pytest.mark.parametrize("markdown,expected", [
    # R5-03: ordinary front matter that only mentions gdoc
    ("---\nname: gdoc-fidelity-test\ndescription: tests gdoc\n---\nBody.\n", "Body.\n"),
    ("---\ntags:\n  - gdoc\n---\nBody.\n", "Body.\n"),
    ('---\ntitle: "Using gdoc"\n---\nBody.\n', "Body.\n"),
])
def test_r5_03_ordinary_front_matter_is_not_a_pulled_header(route, markdown, expected):
    _written(route, "Alpha.\n")
    route.ok("write", text=markdown)
    assert _read(route) == expected


def test_r5_04_a_body_opening_with_a_rule_is_body(route):
    _written(route, "Alpha.\n")
    route.ok("write", text="---\n\nWe use gdoc daily.\n\n---\nMore.\n")
    assert "We use gdoc daily." in _read(route)


def test_r5_05_a_header_after_a_blank_line_is_refused(route):
    _written(route, "Alpha.\n")
    _refused(route, "write", "may be a pulled file",
             text="---\n\ngdoc: synthetic\ntitle: T\n---\nChanged.\n")


def test_r5_03_the_sync_hook_skips_ordinary_front_matter(monkeypatch, tmp_path, capsys):
    from gdoc import cli

    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "Alpha.")))
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: gdoc-fidelity-test\ndescription: gdoc\n---\nBody.\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_input": {"file_path": str(skill)}})))
    assert cli.cmd_sync_hook(SimpleNamespace()) == 0
    assert not capsys.readouterr().err
    pulled = tmp_path / "d.md"
    pulled.write_text("\n" + HEADER + "Changed.\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_input": {"file_path": str(pulled)}})))
    assert cli.cmd_sync_hook(SimpleNamespace()) == 2
    assert "may be a pulled file" in capsys.readouterr().err
    assert cli.run_argv(["push", str(pulled)], check_updates=False) == 3
    assert not route.service.batches


@pytest.mark.parametrize("base,inserted", [
    ("- a\n- b\n", "   \n- c\n"),
    ("- a\n- b\n", " \t\n- c\n"),
])
def test_r5_08_a_whitespace_line_is_blank(route, base, inserted):
    _written(route, base)
    _refused(route, "insert", "list's structure", text=inserted, tab="t.0",
             position="end")


@pytest.mark.parametrize("inserted", [
    "- c\n\n```\n2. x\n```\n",
    "Para\n\n2) not a marker\n",
])
def test_r5_09_numbers_that_start_no_list_are_not_refused(route, inserted):
    _written(route, "1. a\n\npara\n")
    route.ok("insert", text=inserted, tab="t.0", position="end")

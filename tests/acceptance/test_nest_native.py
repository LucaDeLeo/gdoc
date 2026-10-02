"""`gdoc nest` / `gdoc unnest` on lists written by `write`, applied to the
offline native model (CLI and MCP)."""

import pytest

from gdoc.frontmatter import parse_frontmatter
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc, styles


@pytest.fixture(params=["cli", "mcp"])
def route(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _read(route):
    return parse_frontmatter(route.ok("cat"))[1]


def _nest(route, command, text, **flags):
    if route.interface == "mcp":
        return route.ok(command, text=text, **flags)
    from gdoc import cli

    argv = [command, "synthetic", text]
    for key, value in flags.items():
        argv += ["--" + key, str(value)]
    assert cli.run_argv(argv, check_updates=False) == 0


def _written(route, markdown):
    doc = route.load(NativeDoc())
    route.ok("cat")
    route.ok("write", text=markdown)
    route.ok("cat")
    return doc


@pytest.mark.parametrize("base,command,args,expected", [
    ("1. a\n2. b\n3. c\n", "nest", {"text": "b"}, "1. a\n  1. b\n2. c\n"),
    ("- a\n  - b\n- c\n", "unnest", {"text": "b"}, "- a\n- b\n- c\n"),
    ("1. a\n2. b\n3. c\n4. d\n", "nest", {"text": "b", "to": "c"},
     "1. a\n  1. b\n  2. c\n2. d\n"),
])
def test_nest_on_a_written_list(route, base, command, args, expected):
    doc = _written(route, base)
    lists = {bullet[0] for _, _, bullet in styles(doc) if bullet}
    _nest(route, command, **args)
    assert _read(route) == expected
    # The items stay in their native list.
    assert {bullet[0] for _, _, bullet in styles(doc) if bullet} == lists
    # And a rewrite of what cat shows is stable.
    route.ok("write", text=expected)
    assert _read(route) == expected

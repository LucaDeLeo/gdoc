"""Independent review round 6 of f97ebdd (R6-xx): a pulled header is found by
its gdoc key anywhere in the file, and fences in list items and quotes are
accepted only as `cat` prints them. Checked through CLI and MCP `write`,
`push` and the sync hook on the native model."""

import contextlib
import io
import json
import random
import sys

import pytest

from gdoc import cli, mcp
from gdoc.frontmatter import provenance_header_problem
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc, styles


def _run(*argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.run_argv(list(argv), check_updates=False)
    finally:
        sys.stdin = old
    return code, out.getvalue() + err.getvalue()


def _mcp_write(text):
    reply = mcp.MCPServer().dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "gdoc_write",
                   "arguments": {"doc": "synthetic", "text": text}}})
    result = reply.get("result", {})
    output = "\n".join(block["text"] for block in result.get("content", []))
    return (1 if result.get("isError") or "error" in reply else 0), output


def _attempt(entry, path):
    if entry == "write":
        return _run("write", "synthetic", str(path))
    if entry == "push":
        return _run("push", str(path))
    if entry == "hook":
        return _run("_sync-hook", stdin=json.dumps(
            {"hook_event_name": "PostToolUse",
             "tool_input": {"file_path": str(path)}}))
    return _mcp_write(path.read_text(encoding="utf-8"))


def _texts(route):
    return [text for text, *_ in styles(route.service.doc)]


@pytest.fixture
def route(monkeypatch, tmp_path):
    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "Alpha."), ("p", "Beta.")))
    return route


def _stale(route, path, wrap):
    """Pull, edit the body, wrap the file, then a collaborator edits and the
    agent reads the doc again."""
    assert _run("pull", "synthetic", str(path))[0] == 0
    pulled = path.read_text(encoding="utf-8").replace("Alpha.", "Alpha edited.")
    path.write_text(wrap(pulled), encoding="utf-8", newline="")
    route.service.doc = NativeDoc(("p", "Alpha."), ("p", "Beta."),
                                  ("p", "COLLAB."))
    route.service.revision += 1
    assert _run("cat", "synthetic")[0] == 0


def _quoted(text):
    return "".join("> " + line for line in text.splitlines(keepends=True))


# R6-01: visible text or a wrapper before the header.
WRAPPERS = {
    "fence_markdown": lambda t: "```markdown\n" + t + "```\n",
    "fence_yaml": lambda t: "```yaml\n" + t,
    "chatter": lambda t: "Here is the updated file:\n\n" + t,
    "title": lambda t: "My notes\n" + t,
    "html_tag": lambda t: "<div>\n" + t + "</div>\n",
    "open_comment": lambda t: "<!-- draft\n" + t,
    "empty_block": lambda t: "---\n---\n" + t,
    "quoted": _quoted,
    "listed": lambda t: "- " + t.replace("\n", "\n  "),
    "indented": lambda t: "".join("    " + line
                                  for line in t.splitlines(keepends=True)),
}
ENTRIES = ["write", "mcp", "push", "hook"]


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("wrapper", list(WRAPPERS))
def test_r6_01_a_wrapped_pulled_header_is_refused(route, tmp_path, wrapper, entry):
    path = tmp_path / "d.md"
    _stale(route, path, WRAPPERS[wrapper])
    sent = len(route.service.batches)
    code, output = _attempt(entry, path)
    assert code != 0 and "may be a pulled file" in output, output
    assert len(route.service.batches) == sent
    assert "COLLAB." in _texts(route)


INVISIBLE = ["​", "‎", "﻿", "͏", "️", "­",
             "⁠", "\U000e0100", "‮"]
FILLER = ["\n", "\r\n", " ", "\t", " ", "　", "<!-- x -->",
          "<!-- a\nb -->", "<!--\n-->\n"]


def _random_wrap(seed):
    rng = random.Random(seed)
    pieces = [rng.choice(INVISIBLE + FILLER)
              for _ in range(rng.randint(1, 4))]
    wrapper = rng.choice([None, *WRAPPERS])

    def wrap(text):
        if wrapper:
            text = WRAPPERS[wrapper](text)
        if rng.random() < 0.3:  # an invisible character inside a key
            text = text.replace("gdoc", "g" + rng.choice(INVISIBLE) + "doc", 1)
        return "".join(pieces) + text
    return wrap


@pytest.mark.parametrize("seed", range(40))
def test_r6_01_random_prefixes_around_a_stale_header_are_refused(
        route, tmp_path, seed):
    """Property: invisible characters, blank lines, whitespace, comments and
    the R6-01 wrappers around a stale pulled header never let any entry point
    overwrite a collaborator's edit."""
    for entry in ENTRIES:
        path = tmp_path / f"{entry}.md"
        _stale(route, path, _random_wrap(seed))
        sent = len(route.service.batches)
        code, output = _attempt(entry, path)
        assert code != 0, (entry, path.read_text(encoding="utf-8"), output)
        assert len(route.service.batches) == sent
        assert "COLLAB." in _texts(route)


@pytest.mark.parametrize("text", [
    "---\ngdoc: d\ntitle: T\n---\nBody.\n",
    "﻿---\r\ngdoc: d\r\ntitle: T\r\n---\r\nBody.\r\n",
    "---\ngdoc: d\n---\n---\ngdoc-revision: old\n---\n",  # body text after it
])
def test_a_header_as_pull_writes_it_is_read(text):
    assert provenance_header_problem(text) is None


@pytest.mark.parametrize("text", [
    "---\nGdoc: d\n---\nBody.\n",
    "---\ngdoc: d\nGDOC-REVISION: r\n---\nBody.\n",
    "---\ngdoc: d\n\ntitle: T\n---\nBody.\n",
    "---\ngdoc: d\n# note\n---\nBody.\n",
    "--- \ngdoc: d\n---\nBody.\n",
])
def test_a_header_spelled_otherwise_is_refused(text):
    assert provenance_header_problem(text)


@pytest.mark.parametrize("text", [
    "---\ntitle: Notes\ntags: gdoc\n---\nBody.\n",
    "---\n---\n---\nStatus: draft\n---\nBody.\n",
    "![chart](gdoc-image:kix.abc)\n",
    "<!-- gdoc:TITLE --> Heading\n",
    "We use gdoc: it works.\n",
    "Install gdoc-cli: run it.\n",
])
def test_text_without_a_gdoc_key_line_is_ordinary(text):
    assert provenance_header_problem(text) is None


def test_a_gdoc_line_in_ordinary_text_has_working_routes(route, tmp_path):
    """The accepted over-refusal: a tab whose text holds a line like
    `gdoc-revision: x`. `cat`'s output is refused as a write without a
    header; writing from a fresh pull, or escaping the colon, works."""
    route.load(NativeDoc(("p", "Notes."), ("p", "gdoc-revision: x")))
    shown = route.ok("cat")
    code, output, error = route.call("write", text=shown + "More.\n")
    assert code != 0 and "may be a pulled file" in output + error
    assert not route.service.batches

    pulled = tmp_path / "d.md"
    assert _run("pull", "synthetic", str(pulled))[0] == 0
    pulled.write_text(pulled.read_text() + "More.\n")
    assert _run("write", "synthetic", str(pulled))[0] == 0
    assert _texts(route) == ["Notes.", "gdoc-revision: x", "More."]

    route.ok("cat")
    route.ok("write", text=shown.replace("gdoc-revision:", "gdoc-revision\\:")
             + "Again.\n")
    assert _texts(route) == ["Notes.", "gdoc-revision: x", "Again."]


@pytest.fixture(params=["cli", "mcp"])
def either(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _shown(route):
    from gdoc.frontmatter import parse_frontmatter

    return parse_frontmatter(route.ok("cat"))[1]


# Code in list items and quotes, as `cat` prints it: the container's prefix
# on every line, then the fence or the code.
CAT_SPELLINGS = [
    "- a\n\n  ```\n  x\n  ```\n",
    "1. a\n\n   ```\n   x\n\n   y\n   ```\n2. b\n",
    "- a\n  - b\n\n    ```\n    x\n    ```\n",
    "- a\n  - b\n    - c\n\n      ```\n      x\n      ```\n",
    "> ```\n> x\n>\n> y\n> ```\nafter\n",
    "> > ```\n> > x\n> > ```\n",
    "> - a\n>\n>   ```\n>   x\n>   ```\n",
    "- a\n\n  > ```\n  > x\n  > ```\n",
    "- a\n\n  ````\n  ```\n  x\n  ```\n  ````\n",
]


@pytest.mark.parametrize("markdown", CAT_SPELLINGS)
def test_code_in_a_container_round_trips_as_cat_prints_it(either, markdown):
    either.load(NativeDoc())
    either.ok("cat")
    either.ok("write", text=markdown)
    shown = _shown(either)
    sent = len(either.service.batches)
    either.ok("write", text=shown)
    assert len(either.service.batches) == sent
    edited = shown.replace("x\n", "x2\n", 1)
    either.ok("write", text=edited)
    assert _shown(either) == edited


def test_an_info_string_and_editor_trimmed_blank_lines_are_accepted(either):
    either.load(NativeDoc())
    either.ok("cat")
    either.ok("write", text="1. a\n\n   ```bash\n   x\n\n   y\n   ```\n"
                            "> ```\n> x\n>\n> y\n> ```\n")
    assert _shown(either) == ("1. a\n\n   ```\n   x\n   \n   y\n   ```\n"
                              "> ```\n> x\n> \n> y\n> ```\n")


@pytest.mark.parametrize("markdown", [
    "  ```\nx\n  ```\n",                       # indented top-level fence
    "- ```\n  x\n  ```\n",                     # fence on a marker line
    "1) ```\nx\n```\n",                        # `1)` marker line
    "- a\n\n   ```\n   x\n   ```\n".replace("   ```", "    ```", 1),
    ">```\n>x\n>```\n",                        # quote without its space
    ">  ```\n>  x\n>  ```\n",                  # extra indent in a quote
    "- a\n\n  ```\n  x\n   ```\n",             # closer indented differently
    "- a\n\n  ```\n  x\n  ````\n",             # longer closer
    "- a\n\n  ```\n  x\n",                     # never closed
    "1. a\n\n   ```\n   x\n2. b\n\n```\n",     # closer outside the item
    "- a\n\n  ```\nx\n  ```\n",                # code line outside the item
    "> ```\n> x\n\n> ```\n",                   # blank line ends the quote
    "- a\n\n  ~~~\n  x\n  ```\n  ~~~~\n",      # closer-like line in the code
])
def test_any_other_fence_spelling_is_refused(either, markdown):
    either.load(NativeDoc(("p", "Alpha.")))
    either.ok("cat")
    code, output, error = either.call("write", text=markdown)
    assert code != 0 and "code fence gdoc doesn't read" in output + error
    assert "`   ```bash`" in output + error  # the accepted spelling
    assert not either.service.batches

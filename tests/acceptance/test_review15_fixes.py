"""Independent review round 7 of 283e376 (R7-xx): the sync hook skips files
that were never pulled, the header refusal names the line that stops the
header being read, and `edit --cell` keeps a cell's native bullets."""

import json
import re
from pathlib import Path

import pytest

from tests.acceptance.test_review14_fixes import _attempt, _run, _shown, _stale, _texts
from tests.acceptance.test_round5_workflows import NativeRoute
from tests.native_model import NativeDoc

README = Path(__file__).resolve().parents[2] / "README.md"


@pytest.fixture
def route(monkeypatch, tmp_path):
    route = NativeRoute("cli", monkeypatch, tmp_path)
    route.load(NativeDoc(("p", "Alpha."), ("p", "Beta.")))
    return route


def _hook(path):
    return _run("_sync-hook", stdin=json.dumps(
        {"hook_event_name": "PostToolUse",
         "tool_input": {"file_path": str(path)}}))


@pytest.mark.parametrize("text", [
    README.read_text(encoding="utf-8"),
    '```json\n{\n  "mcpServers": {\n    "gdoc": {\n      "command": "gdoc"\n'
    "    }\n  }\n}\n```\n",
    "A pulled file starts like this:\n\n```yaml\n---\ngdoc: ID\n"
    "gdoc-revision: R\n---\n```\n",
])
def test_r7_01_the_sync_hook_skips_files_that_were_never_pulled(
        route, tmp_path, text):
    path = tmp_path / "notes.md"
    path.write_text(text, encoding="utf-8")
    assert _hook(path) == (0, "")
    assert not route.service.batches


@pytest.fixture(params=["cli", "mcp"])
def either(request, monkeypatch, tmp_path):
    return NativeRoute(request.param, monkeypatch, tmp_path)


def _with_line(text, line):
    """Add *line* to a pulled file's header, before its closing `---`."""
    head, _, body = text.partition("\n---\n")
    return head + "\n" + line + "\n---\n" + body


# R7-02: a stale pulled file whose header can't be read, and the line the
# refusal must name.
UNREAD = {
    "fence": (lambda t: "```markdown\n" + t + "```\n",
              "text comes before its gdoc header line, line 3"),
    "chatter": (lambda t: "Sure, here it is:\n\n" + t,
                "text comes before its gdoc header line, line 4"),
    "tags": (lambda t: _with_line(t, "tags:\n  - draft"),
             "line 9 ('  - draft')"),
    "comment": (lambda t: _with_line(t, "# synced"), "line 8 ('# synced')"),
    "blank": (lambda t: _with_line(t, ""), "line 8 ('')"),
    "closer_space": (lambda t: t.replace("\n---\n", "\n--- \n", 1),
                     "line 8 ('--- ')"),
    "opener_space": (lambda t: t.replace("---\n", "--- \n", 1),
                     "line 1 ('--- ')"),
}


def _follow(text, error):
    """The file after the fix the refusal names."""
    lines = text.split("\n")
    if match := re.search(r"or correct line (\d+) to `---`", error):
        lines[int(match[1]) - 1] = "---"
    elif match := re.search(r"or correct or remove line (\d+)", error):
        del lines[int(match[1]) - 1]
    else:
        assert "delete the text above the header's opening `---`" in error
        first = next(n for n, line in enumerate(lines) if line.startswith("gdoc:"))
        del lines[:first - 1]
    return "\n".join(lines)


@pytest.mark.parametrize("entry", ["write", "mcp", "push"])
@pytest.mark.parametrize("name", list(UNREAD))
def test_r7_02_the_refusal_names_the_line_and_its_advice_keeps_the_check(
        route, tmp_path, name, entry):
    wrap, where = UNREAD[name]
    path = tmp_path / "d.md"
    _stale(route, path, wrap)
    code, output = _attempt(entry, path)
    assert code != 0 and where in output, output
    assert "escape" not in output and "--force" not in output
    for _ in range(3):  # A fix may show the next line that stops the header.
        path.write_text(_follow(path.read_text(encoding="utf-8"), output),
                        encoding="utf-8")
        code, output = _attempt(entry, path)
        if "may be a pulled file" not in output:
            break
    assert code != 0 and "stale" in output, output
    assert not route.service.batches
    assert "COLLAB." in _texts(route)


def test_r7_02_force_doesnt_write_a_file_with_a_pulled_key(route, tmp_path):
    path = tmp_path / "d.md"
    _stale(route, path, UNREAD["fence"][0])
    code, output = _run("write", "synthetic", str(path), "--force")
    assert code == 3 and "may be a pulled file" in output
    assert not route.service.batches


@pytest.mark.parametrize("markdown", [
    "# gdoc: a CLI for Google Docs\n\nIt reads docs.\n",
    "Notes.\n\ngdoc: is a CLI for Google Docs.\n",
    "```\ngdoc: d\n```\n",
])
def test_r7_02_a_doc_about_gdoc_writes_with_force(either, markdown):
    """No `gdoc-revision` or `gdoc-version` key: the file may never have been
    pulled, so `--force` writes it as it is."""
    either.load(NativeDoc(("p", "Alpha.")))
    code, output, error = either.call("write", text=markdown)
    assert code != 0 and "`write --force` (MCP `force: true`)" in output + error
    assert "delete the text" not in output + error
    assert not either.service.batches
    either.ok("cat")
    either.ok("write", text=markdown, force=True)
    assert _shown(either) == markdown

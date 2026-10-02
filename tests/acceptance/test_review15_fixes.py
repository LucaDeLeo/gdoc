"""Independent review round 7 of 283e376 (R7-xx): the sync hook skips files
that were never pulled, the header refusal names the line that stops the
header being read, and `edit --cell` keeps a cell's native bullets."""

import json
from pathlib import Path

import pytest

from tests.acceptance.test_review14_fixes import _run
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

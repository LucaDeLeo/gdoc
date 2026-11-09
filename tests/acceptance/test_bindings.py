"""The inventory binds stable IDs to real assertions, not just test files."""

import ast
import json
from pathlib import Path


def test_inventory_has_exact_families_and_deferred_items():
    bindings = json.loads(Path(__file__).with_name("finding-bindings.json").read_text())
    assert len({b["id"] for b in bindings}) == len(bindings) == 54
    assert {b["id"] for b in bindings if b["id"].startswith("D")} == {
        f"D{i:02d}" for i in range(1, 17)
    }
    assert len([b for b in bindings if not b["id"].startswith("D")]) == 38
    for binding in bindings:
        if not binding["assertions"]:
            assert binding["id"] in {"F8", "D03", "D04"}
            assert binding["gap"]
        for node in binding["assertions"]:
            filename, *names = node.split("::")
            tree = ast.parse(Path(filename).read_text())
            body = tree.body
            for name in names:
                candidates = [n for n in body if getattr(n, "name", None) == name]
                assert len(candidates) == 1, node
                body = candidates[0].body
            assert isinstance(candidates[0], ast.FunctionDef), node
            assert candidates[0].name.startswith("test_"), node

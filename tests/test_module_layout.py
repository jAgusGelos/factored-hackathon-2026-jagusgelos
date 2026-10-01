"""The split of the conversation code (`case_turn` <- `credit` <- `explanation`
<- `state_machine`, and `case_turn` <- `statement` <- `state_machine`) stays
acyclic, and the explanation step can only get its policy verdict from the
caller.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys

import pytest

from app import explanation
from tests.support import REPO_ROOT

LAYERS = ("app.case_turn", "app.credit", "app.explanation", "app.statement", "app.state_machine", "app.main")


@pytest.mark.parametrize("order", [LAYERS, tuple(reversed(LAYERS))], ids=["bottom_up", "top_down"])
def test_the_conversation_modules_import_in_any_order(order):
    code = "; ".join(f"import {module}" for module in order)
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def _imported_modules(module_file: str) -> set[str]:
    tree = ast.parse((REPO_ROOT / module_file).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


@pytest.mark.parametrize(
    "module_file", ["app/case_turn.py", "app/credit.py", "app/explanation.py", "app/statement.py"],
)
def test_the_step_modules_never_import_the_state_machine(module_file):
    assert "app.state_machine" not in _imported_modules(module_file)


def test_the_case_turn_module_imports_no_step_module():
    assert not {"app.credit", "app.explanation", "app.statement"} & _imported_modules("app/case_turn.py")


def test_the_policy_verdict_is_a_required_keyword_argument():
    parameter = inspect.signature(explanation.handle_explanation).parameters["policy_verdict"]
    assert parameter.kind == inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty

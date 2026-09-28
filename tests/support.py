"""Test-only re-exports of `support.py` plus the pytest skip marker.

`support.py` (repo root) has no pytest dependency, since `eval/run_eval.py`
(a standalone script) imports from it too — this module adds the
pytest-specific bits on top, for `tests/` only.
"""

from __future__ import annotations

import pytest

from support import (
    REAL_DEMO_USERS_PATH,
    REAL_FIXTURE_PATH,
    REPO_ROOT,
    mock_anthropic_client,
    persona_complaint,
    persona_session,
)

__all__ = [
    "REAL_DEMO_USERS_PATH",
    "REAL_FIXTURE_PATH",
    "REPO_ROOT",
    "mock_anthropic_client",
    "persona_complaint",
    "persona_session",
    "requires_real_fixture",
]

requires_real_fixture = pytest.mark.skipif(
    not (REAL_FIXTURE_PATH.exists() and REAL_DEMO_USERS_PATH.exists()),
    reason="Requires the ETL fixture (run `python etl/extract.py && python etl/build_fixture.py` first)",
)

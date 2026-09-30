"""Test-only re-exports of `support.py` plus the pytest skip marker.

`support.py` (repo root) has no pytest dependency, since `eval/run_eval.py`
(a standalone script) imports from it too — this module adds the
pytest-specific bits on top, for `tests/` only.
"""

from __future__ import annotations

import pytest

from support import (
    AUTO_RESOLVE_CHARGE,
    CONVINCING_ASSESSMENT,
    DEMO_USERNAME,
    DUPLICATE_CHARGES,
    EXPLANATION,
    FRAUD_SCORE_CHARGE,
    OVER_LIMIT_CHARGE,
    REAL_DEMO_USERS_PATH,
    REAL_FIXTURE_PATH,
    REPO_ROOT,
    charge_extraction,
    charge_report,
    demo_session,
    mock_anthropic_client,
    session_for,
)

__all__ = [
    "REAL_DEMO_USERS_PATH",
    "REAL_FIXTURE_PATH",
    "REPO_ROOT",
    "AUTO_RESOLVE_CHARGE",
    "CONVINCING_ASSESSMENT",
    "EXPLANATION",
    "DEMO_USERNAME",
    "DUPLICATE_CHARGES",
    "FRAUD_SCORE_CHARGE",
    "OVER_LIMIT_CHARGE",
    "charge_extraction",
    "charge_report",
    "demo_session",
    "mock_anthropic_client",
    "session_for",
    "requires_real_fixture",
]

requires_real_fixture = pytest.mark.skipif(
    not (REAL_FIXTURE_PATH.exists() and REAL_DEMO_USERS_PATH.exists()),
    reason="Requires the ETL fixture (run `python etl/extract.py && python etl/build_fixture.py` first)",
)

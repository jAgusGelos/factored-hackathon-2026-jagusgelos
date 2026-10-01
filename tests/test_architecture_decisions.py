"""The versioned decisions record, docs/architecture-decisions.md
(system-baseline-adrs AD-7): every feature's decisions are there, traceable to
their source, and the core product's AD-1..AD-13 carry a status.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from support import REPO_ROOT
from tests.support import EM_DASH

DECISIONS_PATH = REPO_ROOT / "docs" / "architecture-decisions.md"
FEATURES = (
    "dispute-agent", "usability-s1-flujo", "usability-s2-tono-escalamiento", "usability-s3-handoff-idiomas",
    "statement-before-handoff", "ad13-quality-refactor", "demand-analysis", "system-baseline-adrs",
)
CORE_FEATURE = "dispute-agent"
CORE_DECISION_COUNT = 13
AD_HEADING = re.compile(r"^### (AD-\d+):", re.MULTILINE)

# The planning record is gitignored and only exists on the author's machine.
# A worktree can point at it with DECISIONS_WORKSPACE_ROOT; without it, the
# plan cross-check is skipped (a clean clone has no .workspace/).
WORKSPACE_ROOT = Path(os.environ.get("DECISIONS_WORKSPACE_ROOT", REPO_ROOT))


def _sections() -> dict[str, str]:
    """Feature name -> its section body, from the `## <feature>` headings."""
    parts = re.split(r"^## ", DECISIONS_PATH.read_text(), flags=re.MULTILINE)[1:]
    return {part.split("\n", 1)[0].strip(): part for part in parts}


def _entries(section: str) -> dict[str, str]:
    """`AD-n` -> its entry text, for one section."""
    parts = re.split(r"^### ", section, flags=re.MULTILINE)[1:]
    ids = [part.split(":", 1)[0] for part in parts]
    assert len(ids) == len(set(ids)), f"duplicate AD heading in {ids}"
    return dict(zip(ids, parts, strict=True))


def _local_plan_decisions(workspace_root: Path) -> dict[str, list[str]]:
    plans = {
        feature: workspace_root / ".workspace" / "features" / feature / "plan.md" for feature in FEATURES
    }
    return {feature: AD_HEADING.findall(plan.read_text()) for feature, plan in plans.items() if plan.exists()}


def test_every_feature_has_a_section():
    assert set(FEATURES) <= set(_sections())


def test_the_core_product_has_ad1_to_ad13_each_with_a_status():
    entries = _entries(_sections()[CORE_FEATURE])
    assert list(entries) == [f"AD-{n}" for n in range(1, CORE_DECISION_COUNT + 1)]
    assert all("**Status:**" in entry for entry in entries.values())


def test_ad13_is_marked_as_recorded_after_the_fact():
    ad13 = _entries(_sections()[CORE_FEATURE])["AD-13"]
    assert "recorded after the fact" in ad13 and "21794ae" in ad13


def test_the_deployment_decision_says_it_is_not_deployed_yet():
    assert "not deployed yet" in _entries(_sections()[CORE_FEATURE])["AD-7"]


def test_every_entry_has_a_source_line():
    for feature in FEATURES:
        for ad, entry in _entries(_sections()[feature]).items():
            assert "**Source:**" in entry, f"{feature} {ad}"


def test_the_record_has_no_em_dash():
    assert EM_DASH not in DECISIONS_PATH.read_text()


def test_every_local_plan_decision_has_an_entry():
    plans = _local_plan_decisions(WORKSPACE_ROOT)
    if not plans:
        pytest.skip(f"no local planning record under {WORKSPACE_ROOT / '.workspace'}")
    sections = _sections()
    for feature, decisions in plans.items():
        assert set(decisions) <= set(_entries(sections[feature])), feature


def test_the_plan_cross_check_skips_without_a_planning_record(tmp_path, monkeypatch):
    monkeypatch.setattr(f"{__name__}.WORKSPACE_ROOT", tmp_path)
    with pytest.raises(pytest.skip.Exception):
        test_every_local_plan_decision_has_an_entry()

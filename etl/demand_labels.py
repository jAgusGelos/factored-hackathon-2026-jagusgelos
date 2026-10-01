"""The demand report's `kind` labels and lookups over the report dict, shared
by the builder (`etl/analyze_demand.py`) and the renderer
(`etl/demand_report_render.py`): the builder imports the renderer, so the
renderer cannot import the builder.
"""

from __future__ import annotations

from enum import StrEnum


class Kind(StrEnum):
    MEASURED = "measured"
    ASSUMED = "assumed"
    SIMULATED = "simulated"
    PROJECTION = "projection"
    DESIGN_ARGUMENT = "design-argument"


KINDS = frozenset(Kind)

KIND_MEANINGS = {
    Kind.MEASURED: "measured on the dataset, here or in the cited source",
    Kind.ASSUMED: "an input with no data behind it",
    Kind.SIMULATED: "from the offline eval",
    Kind.PROJECTION: "arithmetic over the others",
    Kind.DESIGN_ARGUMENT: "reasoning, not a number",
}


def reason_row(call_center_block: dict, reason: str) -> dict:
    for row in call_center_block["reasons"]:
        if row["contact_reason"] == reason:
            return row
    raise ValueError(f"contact reason {reason!r} not found in the call-center block")


def match_noun(count: int) -> str:
    return "match" if count == 1 else "matches"

"""The `kind` labels of the demand report, shared by the builder
(`etl/analyze_demand.py`) and the renderer (`etl/demand_report_render.py`).
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
    Kind.MEASURED: "computed from the warehouse",
    Kind.ASSUMED: "an input with no data behind it",
    Kind.SIMULATED: "from the offline eval",
    Kind.PROJECTION: "arithmetic over the others",
    Kind.DESIGN_ARGUMENT: "reasoning, not a number",
}

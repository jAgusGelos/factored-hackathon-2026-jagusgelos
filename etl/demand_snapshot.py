"""The committed eval cost snapshot (AD-6 in the demand-analysis plan): the
allowlist of fields copied from the gitignored `data/eval_report.json`, and
the validation every read and every refresh goes through.
"""

from __future__ import annotations

import json
import math
from enum import StrEnum
from pathlib import Path

EVAL_REPORT_SOURCE = "data/eval_report.json"
REFRESH_COMMAND = "python -m etl.analyze_demand --refresh-eval-snapshot"
REFRESH_HINT = f" Regenerate it with `{REFRESH_COMMAND}`."
# Allowed gap between a stored rate and count / of_attempted (4-dp rounding).
RATE_ROUNDING_TOLERANCE = 5e-5


class FieldType(StrEnum):
    TEXT = "text"
    COUNT = "count"
    RATE = "rate"
    NUMBER = "number"
    NUMBER_OR_NONE = "number-or-none"
    # eval/run_eval.py writes a sentence instead of a number when nothing resolved.
    NUMBER_OR_TEXT = "number-or-text"


# The only keys and sub-fields the committed snapshot may hold, so no case id,
# customer data or prompt can reach it. The eval's free-text notes are left
# out: the report never quotes them.
SNAPSHOT_SCHEMA: dict[str, FieldType | dict[str, FieldType]] = {
    "source": FieldType.TEXT,
    "disclosure": FieldType.TEXT,
    "sample_size": FieldType.COUNT,
    "safe_automated_resolution_rate": {
        "count": FieldType.COUNT, "of_attempted": FieldType.COUNT, "rate": FieldType.RATE,
    },
    "estimated_cost_usd": {
        "per_attempted_case_mean": FieldType.NUMBER_OR_NONE,
        "per_successful_resolution": FieldType.NUMBER_OR_TEXT,
        "pricing_source": FieldType.TEXT,
        "method": FieldType.TEXT,
    },
    "latency_seconds": {"p50": FieldType.NUMBER, "p95": FieldType.NUMBER},
    "real_data_match_rate_finding": {
        "sample_size": FieldType.COUNT,
        "real_matches_found": FieldType.COUNT,
        "source": FieldType.TEXT,
    },
}
OPTIONAL_SNAPSHOT_KEY = "real_data_match_rate_finding"


def is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_valid(value, field_type: FieldType) -> bool:
    if field_type is FieldType.TEXT:
        return isinstance(value, str)
    if field_type is FieldType.COUNT:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if field_type is FieldType.RATE:
        return is_number(value) and 0 <= value <= 1
    if field_type is FieldType.NUMBER:
        return is_number(value)
    if field_type is FieldType.NUMBER_OR_NONE:
        return value is None or is_number(value)
    return isinstance(value, str) or is_number(value)


def _invalid_sub_fields(key: str, value, spec: dict[str, FieldType]) -> list[str]:
    if not isinstance(value, dict):
        return [key]
    unknown = [f"{key}.{field}" for field in value if field not in spec]
    return unknown + [
        f"{key}.{field}" for field, field_type in spec.items()
        if field not in value or not _is_valid(value[field], field_type)
    ]


def _invalid_fields(snapshot: dict) -> list[str]:
    """Dotted names of the allowlisted fields that are missing or unusable;
    only the real-data finding may be absent as a whole.
    """
    invalid = [key for key in snapshot if key not in SNAPSHOT_SCHEMA]
    for key, spec in SNAPSHOT_SCHEMA.items():
        value = snapshot.get(key)
        if value is None:
            if key != OPTIONAL_SNAPSHOT_KEY:
                invalid.append(key)
        elif isinstance(spec, dict):
            invalid += _invalid_sub_fields(key, value, spec)
        elif not _is_valid(value, spec):
            invalid.append(key)
    return invalid


def _inconsistencies(snapshot: dict) -> list[str]:
    """Counts that exceed their denominators, or a rate that disagrees with them."""
    problems = []
    resolution = snapshot["safe_automated_resolution_rate"]
    count, attempted = resolution["count"], resolution["of_attempted"]
    if not count <= attempted <= snapshot["sample_size"]:
        problems.append("count <= of_attempted <= sample_size")
    elif attempted and abs(resolution["rate"] - count / attempted) > RATE_ROUNDING_TOLERANCE:
        problems.append("rate == count / of_attempted")
    finding = snapshot.get(OPTIONAL_SNAPSHOT_KEY)
    if finding is not None and finding["real_matches_found"] > finding["sample_size"]:
        problems.append("real_matches_found <= sample_size")
    return problems


def _validated(snapshot, description: str, hint: str = "") -> dict:
    if not isinstance(snapshot, dict):
        raise ValueError(f"{description} is not a JSON object.{hint}")
    invalid = _invalid_fields(snapshot)
    if invalid:
        raise ValueError(f"{description} lacks, adds or has unusable {invalid}.{hint}")
    problems = _inconsistencies(snapshot)
    if problems:
        raise ValueError(f"{description} breaks {problems}.{hint}")
    return snapshot


def _read_json(path: Path, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} at {path} is not valid JSON: {exc}") from exc


def read_snapshot(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Eval cost snapshot not found at {path}. Run `python -m eval.run_eval` and then "
            f"`{REFRESH_COMMAND}`."
        )
    snapshot = _read_json(path, "Eval cost snapshot")
    return _validated(snapshot, f"Eval cost snapshot at {path}", REFRESH_HINT)


def _allowlisted(value, spec: FieldType | dict[str, FieldType]):
    if not isinstance(spec, dict) or not isinstance(value, dict):
        return value
    return {field: value[field] for field in spec if field in value}


def build_snapshot(eval_report_path: Path) -> dict:
    """The allowlisted, validated snapshot of an eval report; nothing is written."""
    if not eval_report_path.exists():
        raise FileNotFoundError(
            f"Eval report not found at {eval_report_path}. Run `python -m eval.run_eval` first."
        )
    report = _read_json(eval_report_path, "Eval report")
    description = f"Eval report at {eval_report_path}"
    if not isinstance(report, dict):
        raise ValueError(f"{description} is not a JSON object.")
    escalation = report.get("escalation_quality")
    finding = escalation.get(OPTIONAL_SNAPSHOT_KEY) if isinstance(escalation, dict) else None
    source = {**report, OPTIONAL_SNAPSHOT_KEY: finding}
    snapshot: dict = {"source": EVAL_REPORT_SOURCE}
    for key, spec in SNAPSHOT_SCHEMA.items():
        if key != "source" and source.get(key) is not None:
            snapshot[key] = _allowlisted(source[key], spec)
    return _validated(snapshot, description)

"""Local, source-bound preparation for different report writing formats."""

import hashlib
import json
import math
from copy import deepcopy
from decimal import Decimal

REPORT_FORMATS = ("general", "epb", "cv")


def _format_name(report_format):
    if not isinstance(report_format, str) or report_format not in REPORT_FORMATS:
        raise ValueError("report format must be general, epb, or cv")
    return report_format


def preparation_guide(report_format):
    """Return format-specific instructions that do not authorize new facts."""
    guides = {
        "general": "Use only verified Saga facts for numerical claims. Saved text is source material for review.",
        "epb": ("Prepare an action, result, and impact draft for each selected record. "
                "Identify task titles and outcomes as saved text. Mark impact missing unless "
                "the author supplies supported wording. Use only Saga fact references for numbers."),
        "cv": ("Group selected accomplishments by saved role and category. Write concise "
               "review bullets from saved outcomes and task titles. Do not invent employers, "
               "employment dates, impact, or numerical claims. Use Saga fact references for numbers."),
    }
    return guides[_format_name(report_format)]


def _display_number(value):
    display = format(Decimal(str(value)), ",f")
    return display.rstrip("0").rstrip(".") if "." in display else display


def _saved_field(row, field):
    value = row.get(field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"record {row['id']} has invalid {field}")
    return {"text": value if value and value.strip() else None,
            "source_field": field, "status": "saved" if value and value.strip() else "missing"}


def build_preparation(evidence, report_format="general", selected_ids=None):
    """Build a JSON-ready draft ledger from explicitly selected verified records.

    Aggregates in evidence retain the full filtered scope. This ledger never
    computes selected-record totals or treats digits in saved prose as facts.
    """
    report_format = _format_name(report_format)
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise ValueError("unsupported evidence schema")
    if not isinstance(evidence.get("verification"), dict) or evidence["verification"].get("passed") is not True:
        raise ValueError("report evidence is not verified")
    report_id = evidence.get("report_id")
    records = evidence.get("records")
    if not isinstance(report_id, str) or not isinstance(records, list):
        raise ValueError("report records or ID are missing")  # noqa: TRY004 - report API uses ValueError
    if selected_ids is None:
        selected_ids = []
    if not isinstance(selected_ids, list):
        raise ValueError("selected IDs must be a list")  # noqa: TRY004 - report API uses ValueError
    if any(type(record_id) is not int for record_id in selected_ids):
        raise ValueError("selected IDs must be integers")
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("selected IDs must be unique")
    if report_format == "general" and selected_ids:
        raise ValueError("general report does not select records")
    if report_format != "general" and not selected_ids:
        raise ValueError("EPB and CV reports require selected records")
    source = {}
    for row in records:
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] in source:
            raise ValueError("report has invalid or duplicate record IDs")
        source[row["id"]] = row
    if any(record_id not in source for record_id in selected_ids):
        raise ValueError("selected ID is outside this report")

    selected_records = []
    facts = {}
    for record_id in selected_ids:
        row = source[record_id]
        measure, quantity = row.get("measure"), row.get("quantity")
        if (measure is None) != (quantity is None):
            raise ValueError(f"record {record_id} has an incomplete measurement")
        measurement = None
        if quantity is not None:
            if not isinstance(measure, str) or not measure.strip():
                raise ValueError(f"record {record_id} has an invalid measure")
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity):
                raise ValueError(f"record {record_id} has an invalid quantity")
            fact_id = f"record-{record_id}-quantity"
            display = _display_number(quantity)
            measurement = {"value": quantity, "unit": measure, "display": display,
                           "source_record_id": record_id, "fact_id": fact_id}
            facts[fact_id] = {"label": f"Record {record_id} quantity", "value": quantity,
                              "display": f"{display} {measure}", "measure": measure,
                              "dimension": "selected_record", "group": record_id,
                              "source_record_ids": [record_id]}
        selected_records.append({
            "source_record": deepcopy(row),
            "measurement": measurement,
            "action": _saved_field(row, "task_title"),
            "result": _saved_field(row, "outcome"),
            "impact": {"text": None, "source_field": None, "status": "missing"},
        })
    identity = json.dumps([report_id, report_format, selected_ids], ensure_ascii=False, separators=(",", ":"))
    preparation_id = "preparation-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return {"report_format": report_format, "report_id": report_id,
            "preparation_id": preparation_id,
            "selected_ids": list(selected_ids), "selected_records": selected_records,
            "facts": facts}


def validate_preparation(preparation, evidence):
    """Reject stale or modified preparation, including injected numeric fields."""
    if not isinstance(preparation, dict) or set(preparation) != {
            "report_format", "report_id", "preparation_id", "selected_ids", "selected_records", "facts"}:
        raise ValueError("preparation has invalid fields")
    if preparation.get("report_id") != evidence.get("report_id"):
        raise ValueError("preparation report ID does not match evidence")
    expected = build_preparation(evidence, preparation["report_format"], preparation["selected_ids"])
    if preparation != expected:
        raise ValueError("preparation does not match report evidence")
    return expected

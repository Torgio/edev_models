"""Immutable-by-value inputs of a study, captured from the solver's in-memory inputs.

No queries here: rereading mutable catalog rows after solving would falsify provenance.
No email, user ID, credentials or local source-file paths are exposed in the snapshot.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import math


def _plain(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, Decimal)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "item"):  # numpy scalars from pandas
        return _plain(value.item())
    raise TypeError(f"Unsupported snapshot value: {type(value).__name__}")


def _select(row, fields):
    return None if row is None else {key: row.get(key) for key in fields.split()}


def capture_inputs(*, case, battery, effective_battery, site, consumption, generation,
                   consumption_shape, generation_shape, date_from, date_to,
                   cycle_cost, scenarios, generation_full_load_hours):
    """Capture v1 in canonical SI units; retain actual normalized profiles too.

    Null installation means not included in this execution, not missing historical data.
    The JSON round trip detaches every nested value from mutable solver dictionaries.
    """
    battery_copy = _select(battery, "battery_id code name")
    battery_copy.update(
        power_mw=effective_battery["potencia_mw"],
        duration_h=effective_battery["duracion_h"],
        capacity_mwh=effective_battery["potencia_mw"] * effective_battery["duracion_h"],
        capex_eur_mwh=effective_battery["capex_eur_mwh"],
        efficiency_rt=effective_battery["eficiencia_rt"],
    )
    snapshot = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc),
        "case": _select(case, "case_id code name mode date_from date_to"),
        "period": {"date_from": date_from, "date_to": date_to, "calendar": "nominal_24h"},
        "battery": battery_copy,
        "consumption": _select(consumption, "consump_id code name annual_mwh growth_pct "
                               "tariff_markup_eur_mwh export_price_pct contracted_power_mw"),
        "generation": _select(generation, "gen_id code name technology capacity_mwp "
                              "degradation_pct export_limit_mw"),
        "profiles": {"columns": ["month", "day_type", "hour", "value_pu"],
                     "consumption": consumption_shape, "generation": generation_shape},
        "optimizer": {"battery": effective_battery, "site": site,
                      "window_days": case["window_days"], "cycle_cost_eur_mwh": cycle_cost,
                      "generation_full_load_hours": generation_full_load_hours if generation is not None else None,
                      "scenarios_requested": scenarios},
        "economics": {"discount_rate": case["discount_rate"], "opex_pct": case["opex_pct"]},
    }
    return json.loads(json.dumps(_plain(snapshot), allow_nan=False))


def public_inputs(snapshot):
    """Small response for the dashboard; historical rows remain explicitly unavailable."""
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        return None
    return {key: snapshot.get(key) for key in (
        "schema_version", "captured_at", "case", "period", "battery", "consumption",
        "generation", "economics")}

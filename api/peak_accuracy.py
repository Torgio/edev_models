"""Descriptive peak-hour accuracy on complete production days, without DB writes."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from math import isfinite
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")


def midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, TZ).astimezone(timezone.utc)


def evaluation_window(end_date: date | None, days: int, today: date | None = None):
    today = today or datetime.now(TZ).date()
    last_closed = today - timedelta(days=1)
    end = min(end_date or last_closed, last_closed)
    return end - timedelta(days=days - 1), end


def hour_slots(day: date):
    cursor = midnight(day)
    end = midnight(day + timedelta(days=1))
    result = []
    while cursor < end:
        result.append(cursor)
        cursor += timedelta(hours=1)
    return result


def peak_accuracy(rows, start: date, end: date, model: str):
    samples = {}
    duplicates = set()
    for timestamp, predicted, actual in rows:
        if timestamp.tzinfo is None:
            continue
        timestamp = timestamp.astimezone(timezone.utc)
        if timestamp in samples:
            duplicates.add(timestamp)
        samples[timestamp] = (predicted, actual)

    def valid(value):
        if isinstance(value, Decimal):
            return value.is_finite()
        return isinstance(value, (int, float)) and isfinite(value)

    details = []
    current = start
    while current <= end:
        slots = hour_slots(current)
        complete = all(
            timestamp not in duplicates and timestamp in samples
            and all(valid(value) for value in samples[timestamp])
            for timestamp in slots
        )
        if complete:
            # Deterministic prediction choice, made independently of the real peak.
            predicted_peak = max(slots, key=lambda timestamp: samples[timestamp][0])
            max_actual = max(samples[timestamp][1] for timestamp in slots)
            actual_peaks = [timestamp for timestamp in slots if samples[timestamp][1] == max_actual]
            gap = min(abs((predicted_peak - actual_peak).total_seconds()) for actual_peak in actual_peaks)
            details.append({
                "date": current, "expected_hours": len(slots), "evaluated": True,
                "hit": gap <= 3600, "distance_hours": gap / 3600,
                "predicted_peak": predicted_peak.astimezone(TZ),
                "actual_peaks": [timestamp.astimezone(TZ) for timestamp in actual_peaks],
            })
        else:
            details.append({"date": current, "expected_hours": len(slots),
                            "evaluated": False, "hit": None, "reason": "incomplete_data"})
        current += timedelta(days=1)
    evaluated = sum(item["evaluated"] for item in details)
    hits = sum(item["hit"] is True for item in details)
    return {
        "model": model, "source": "production", "start_date": start, "end_date": end,
        "window_days": len(details), "evaluated_days": evaluated, "hits": hits,
        "excluded_days": len(details) - evaluated, "tolerance_hours": 1,
        "timezone": "Europe/Madrid", "days": details,
        "definition": "First predicted maximum within one elapsed hour of any real maximum, on complete closed days.",
    }

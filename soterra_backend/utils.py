from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta


SEVERITY_ORDER = {
    "Low": 1,
    "Medium": 2,
    "High": 3,
    "Critical": 4,
}


def create_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return normalized.strip("-") or "unknown"


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def parse_report_date(value: str | None, fallback: str | None = None) -> str:
    if not value:
        return fallback or date.today().isoformat()

    cleaned = value.strip().replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue

    return fallback or date.today().isoformat()


def highest_severity(values: list[str]) -> str:
    if not values:
        return "Low"
    return max(values, key=lambda item: SEVERITY_ORDER.get(item, 0))


def summarize_status(severity_values: list[str]) -> str:
    highest = highest_severity(severity_values)
    if highest in {"Critical", "High"}:
        return "Reviewing"
    return "Completed"


def plus_days(iso_date: str, day_count: int) -> str:
    base = datetime.fromisoformat(iso_date).date()
    return (base + timedelta(days=day_count)).isoformat()


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default

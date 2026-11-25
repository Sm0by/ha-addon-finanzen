"""Small helpers: number parsing, dates, due dates of fixed costs."""

from __future__ import annotations

import calendar
import datetime as dt
import re

_NUM_RE = re.compile(r"[-−]?\s*\d[\d.,'   ]*")


def parse_number(value) -> float | None:
    """Parse a number from text in German or English notation.

    "1.234,56 €" -> 1234.56, "62,10" -> 62.1, "1,234.5" -> 1234.5,
    "0.77" -> 0.77, "3.246" -> 3246.0 (German thousands separator).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    match = _NUM_RE.search(text)
    if not match:
        return None
    raw = match.group(0)
    negative = raw.lstrip().startswith(("-", "−"))
    digits = re.sub(r"[^\d.,]", "", raw)
    digits = digits.rstrip(".,")
    if not digits:
        return None
    if "," in digits and "." in digits:
        if digits.rfind(",") > digits.rfind("."):
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif "," in digits:
        if digits.count(",") == 1:
            digits = digits.replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif "." in digits:
        if re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", digits):
            digits = digits.replace(".", "")
    try:
        number = float(digits)
    except ValueError:
        return None
    return -number if negative else number


def isin_valid(isin: str) -> bool:
    """Check format and check digit (Luhn over the letters converted to numbers)."""
    isin = (isin or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin):
        return False
    digits = "".join(str(int(ch, 36)) for ch in isin)
    total = 0
    for index, ch in enumerate(reversed(digits)):
        n = int(ch)
        if index % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def month_key(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_start(key: str) -> dt.date:
    year, month = key.split("-")
    return dt.date(int(year), int(month), 1)


def add_months(key: str, months: int) -> str:
    start = month_start(key)
    index = start.year * 12 + start.month - 1 + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def clamp_day(year: int, month: int, day: int) -> dt.date:
    return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))


_DUE_RE = re.compile(r"^\s*(\d{1,2})\.\s*(?:[–\-]\s*(\d{1,2})\.)?\s*(?:(\d{1,2})\.?)?\s*$")


def parse_due(text: str | None) -> tuple[int, int | None, int | None] | None:
    """Parse a due text of a fixed cost.

    "21.07." -> (21, None, 7)   yearly on 21 July
    "08."    -> (8, None, None) monthly on the 8th
    "27.–30." -> (27, 30, None) monthly between the 27th and the 30th
    """
    if not text:
        return None
    match = _DUE_RE.match(text)
    if not match:
        return None
    day_from = int(match.group(1))
    day_to = int(match.group(2)) if match.group(2) else None
    month = int(match.group(3)) if match.group(3) else None
    if not 1 <= day_from <= 31 or (day_to and not 1 <= day_to <= 31) or (month and not 1 <= month <= 12):
        return None
    return day_from, day_to, month


def format_due(day_from: int, day_to: int | None = None, month: int | None = None) -> str:
    text = f"{day_from:02d}."
    if day_to:
        text += f"–{day_to:02d}."
    if month:
        text += f"{month:02d}."
    return text


_NOTE_DUE_RE = re.compile(
    r"(?:immer\s+am\s*)?(\d{1,2})\s*(?:-\s*(\d{1,2}))?\s*\.\s*(XX|\d{1,2})?\.?",
    re.IGNORECASE,
)


def due_from_note(note: str | None) -> tuple[str, str]:
    """Split a free-text note from the sheet into (due, remaining note).

    "Immer am 21.07." -> ("21.07.", "")
    "27-30.XX."       -> ("27.–30.", "")
    "76,83 alle 2 Jahre" -> ("", "76,83 alle 2 Jahre")
    """
    if not note:
        return "", ""
    if isinstance(note, (dt.date, dt.datetime)):
        # A note like "21.01." becomes a date in the Excel export: yearly on that day
        return format_due(note.day, None, note.month), ""
    text = str(note).strip()
    match = _NOTE_DUE_RE.search(text)
    if not match or ("," in text[: match.start()] and match.start() > 0 and text[match.start() - 1].isdigit()):
        return "", text
    day_from = int(match.group(1))
    day_to = int(match.group(2)) if match.group(2) else None
    month_raw = match.group(3)
    month = int(month_raw) if month_raw and month_raw.isdigit() else None
    if not 1 <= day_from <= 31 or (month and not 1 <= month <= 12):
        return "", text
    rest = (text[: match.start()] + text[match.end():]).strip(" .,-")
    return format_due(day_from, day_to, month), rest


PERIODS = {"monatlich": 1, "vierteljaehrlich": 3, "halbjaehrlich": 6, "jaehrlich": 12}


def period_months(interval: str) -> int:
    return PERIODS.get(interval, 1)


def due_in_month(due: str | None, interval: str, key: str) -> bool:
    """Whether a fixed cost is paid in the month `key` (YYYY-MM)."""
    period = period_months(interval)
    if period == 1:
        return True
    parsed = parse_due(due)
    if not parsed or not parsed[2]:
        return False
    return (month_start(key).month - parsed[2]) % period == 0


def next_due_date(due: str | None, interval: str, today: dt.date) -> dt.date | None:
    parsed = parse_due(due)
    if not parsed:
        return None
    day_from = parsed[0]
    period = period_months(interval)
    if period > 1 and not parsed[2]:
        return None
    key = month_key(today)
    for offset in range(0, 25):
        candidate_key = add_months(key, offset)
        if not due_in_month(due, interval, candidate_key):
            continue
        start = month_start(candidate_key)
        candidate = clamp_day(start.year, start.month, day_from)
        if candidate >= today:
            return candidate
    return None


def due_day(due: str | None) -> int | None:
    parsed = parse_due(due)
    return parsed[0] if parsed else None


def de_date(value: dt.date | str | None) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        value = dt.date.fromisoformat(value[:10])
    return value.strftime("%d.%m.%Y")


def to_date(value) -> dt.date | None:
    """Turn a cell value (datetime, date, 'dd.mm.yyyy', 'yyyy-mm-dd') into a date."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%m.%Y", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None

import datetime as dt

import pytest

from finanzen.util import (
    due_from_note,
    due_in_month,
    next_due_date,
    parse_due,
    parse_number,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("62,10", 62.1),
        ("1.234,56 €", 1234.56),
        ("1,234.5", 1234.5),
        ("0.77", 0.77),
        ("3.246", 3246.0),
        ("3.246,02 EUR", 3246.02),
        ("−5,70 %", -5.7),
        ("EUR 104.18", 104.18),
        ("kein Kurs", None),
        (12, 12.0),
        (None, None),
    ],
)
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_parse_due():
    assert parse_due("21.07.") == (21, None, 7)
    assert parse_due("08.") == (8, None, None)
    assert parse_due("27.–30.") == (27, 30, None)
    assert parse_due("") is None
    assert parse_due("irgendwann") is None


@pytest.mark.parametrize(
    ("note", "due", "rest"),
    [
        ("Immer am 21.07.", "21.07.", ""),
        ("27-30.XX.", "27.–30.", ""),
        ("08.XX", "08.", ""),
        ("Immer am 02.XX.", "02.", ""),
        ("Immer am 22.05", "22.05.", ""),
        ("30-31.XX", "30.–31.", ""),
        ("76,83 alle 2 Jahre", "", "76,83 alle 2 Jahre"),
        ("5€ alle 5 Jahre, nächste 2030", "", "5€ alle 5 Jahre, nächste 2030"),
        (None, "", ""),
    ],
)
def test_due_from_note(note, due, rest):
    assert due_from_note(note) == (due, rest)


def test_due_in_month():
    assert due_in_month("08.", "monatlich", "2026-11")
    assert due_in_month("19.11.", "jaehrlich", "2026-11")
    assert not due_in_month("19.11.", "jaehrlich", "2026-12")
    assert due_in_month("15.02.", "vierteljaehrlich", "2026-11")
    assert not due_in_month("", "jaehrlich", "2026-11")


def test_next_due_date():
    today = dt.date(2026, 11, 24)
    assert next_due_date("27.–30.", "monatlich", today) == dt.date(2026, 11, 27)
    assert next_due_date("08.", "monatlich", today) == dt.date(2026, 12, 8)
    assert next_due_date("21.07.", "jaehrlich", today) == dt.date(2027, 7, 21)
    assert next_due_date("31.", "monatlich", dt.date(2027, 2, 1)) == dt.date(2027, 2, 28)
    assert next_due_date("", "monatlich", today) is None

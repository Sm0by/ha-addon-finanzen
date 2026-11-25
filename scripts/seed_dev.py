"""Fill a local data folder with the made-up sample sheet, for trying the UI without Home Assistant.

Usage: python scripts/seed_dev.py .devdata
"""

from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "finanzen" / "app"))
sys.path.insert(0, str(ROOT / "tests"))

from finanzen import calc  # noqa: E402
from finanzen.db import Database  # noqa: E402
from finanzen.importer import import_workbook  # noqa: E402
from sample_sheet import build  # noqa: E402


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".devdata")
    target.mkdir(parents=True, exist_ok=True)
    db_file = target / "finanzen.db"
    if db_file.exists():
        db_file.unlink()
    db = Database(db_file)
    values, formulas = build()
    today = dt.date.today()
    print(import_workbook(db, values, today, formula_data=formulas)["summary"])
    # Made-up daily history for the chart
    for offset in range(240, 0, -1):
        day = today - dt.timedelta(days=offset)
        wave = math.sin(offset / 17) * 120 + math.cos(offset / 5) * 40
        gold = 1900 + wave * 0.8 + (240 - offset) * 0.4
        stocks = 1210 + wave * 0.5
        db.execute(
            "INSERT OR REPLACE INTO daily (date, total, investments, stocks_etfs, stocks, etfs, crypto, gold) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (day.isoformat(), gold + stocks + 1250, gold + stocks, stocks, stocks * 0.55, stocks * 0.45, gold),
        )
    calc.ensure_month(db, calc.month_key(today)) if hasattr(calc, "month_key") else None
    db.set_setting("salary_net", 2000)
    db.close()
    print(f"Testdaten in {db_file}")


if __name__ == "__main__":
    main()

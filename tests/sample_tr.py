"""A made-up Trade Republic transaction export for the tests. No real data."""

from __future__ import annotations

import csv
import io

HEADER = [
    "datetime", "date", "account_type", "category", "type", "asset_class", "name", "symbol", "shares", "price",
    "amount", "fee", "tax", "currency", "original_amount", "original_currency", "fx_rate", "description",
    "transaction_id", "counterparty_name", "counterparty_iban", "payment_reference", "mcc_code",
]

ALPHA = "XF0000000011"
BETA = "XF0000000029"
GAMMA = "XF0000000037"
DELTA = "XF0000000045"

# (datetime, category, type, asset_class, name, isin, shares, price, amount, fee, tax, id, extra)
ROWS = [
    ("2024-01-05T09:00:00Z", "CASH", "STOCKPERK", "STOCK", "Gamma Streaming", GAMMA, "", "", "9.00", "", "", "t01"),
    ("2024-01-05T09:05:00Z", "TRADING", "BUY", "STOCK", "Gamma Streaming", GAMMA, "0.03", "300.00", "-9.00", "", "", "t02"),
    ("2024-01-10T10:00:00Z", "TRADING", "BUY", "STOCK", "Alpha Motoren", ALPHA, "10", "10.00", "-100.00", "-1.00", "", "t03"),
    ("2024-01-15T10:00:00Z", "TRADING", "BUY", "STOCK", "Delta Post", DELTA, "10", "30.00", "-300.00", "-1.00", "", "t04"),
    ("2024-01-20T12:00:00Z", "CASH", "CARD_TRANSACTION", "", "", "", "", "", "-23.40", "", "", "t05"),
    ("2024-02-01T08:00:00Z", "CASH", "BENEFITS_SAVEBACK", "FUND", "Beta Welt (Acc)", BETA, "", "", "4.00", "", "", "t06"),
    ("2024-02-02T08:00:00Z", "TRADING", "BUY", "FUND", "Beta Welt (Acc)", BETA, "2", "25.00", "-50.00", "", "", "t07"),
    ("2024-02-02T08:00:01Z", "TRADING", "BUY", "FUND", "Beta Welt (Acc)", BETA, "0.16", "25.00", "-4.00", "", "", "t08"),
    ("2024-02-12T10:00:00Z", "TRADING", "BUY", "STOCK", "Alpha Motoren", ALPHA, "10", "12.00", "-120.00", "-1.00", "", "t09"),
    ("2024-03-01T07:00:00Z", "CASH", "TRANSFER_INBOUND", "", "", "", "", "", "500.00", "", "", "t10"),
    ("2024-03-15T10:00:00Z", "TRADING", "SELL", "STOCK", "Alpha Motoren", ALPHA, "-5", "14.00", "70.00", "-1.00", "-2.00", "t11"),
    ("2024-04-20T10:00:00Z", "CASH", "DIVIDEND", "STOCK", "Alpha Motoren", ALPHA, "15", "", "5.00", "", "-1.32", "t12"),
    ("2024-05-10T10:00:00Z", "TRADING", "SELL", "STOCK", "Delta Post", DELTA, "-10", "35.00", "350.00", "-1.00", "", "t13"),
    ("2024-05-20T10:00:00Z", "CASH", "DIVIDEND", "STOCK", "Alpha Motoren", ALPHA, "15", "", "2.00", "", "", "t14"),
    ("2024-06-01T00:00:00Z", "CORPORATE_ACTION", "SPLIT", "STOCK", "Gamma Streaming", GAMMA, "0.27", "", "", "", "", "t15"),
    ("2024-06-20T18:00:00Z", "CASH", "CARD_TRANSACTION", "", "", "", "", "", "-12.00", "", "", "t16"),
]


def tr_csv(rows=ROWS, delimiter: str = ",") -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter)
    writer.writerow(HEADER)
    for at, category, kind, asset, name, isin, shares, price, amount, fee, tax, tx in rows:
        card = kind == "CARD_TRANSACTION"
        writer.writerow([
            at, at[:10], "DEFAULT", category, kind, asset, name, isin, shares, price, amount, fee, tax, "EUR", "", "",
            "", f"{kind} {isin}".strip(), tx, "Laden um die Ecke" if card else "",
            "XX00 0000 0000 0000 0000 00" if card else "", "", "5411" if card else "",
        ])
    return out.getvalue().encode("utf-8")

"""Draw icon.png (128x128) and logo.png (250x100) without extra libraries.

Usage: python scripts/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "finanzen"
BG = (36, 64, 94)
FG = (214, 235, 255)
GOLD = (229, 183, 64)


def png(path: Path, width: int, height: int, pixel) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel(x, y))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    path.write_bytes(data)


def rounded(x: int, y: int, w: int, h: int, r: int) -> bool:
    cx = min(max(x, r), w - 1 - r)
    cy = min(max(y, r), h - 1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def bars(x: int, y: int, ox: int, oy: int, scale: float) -> tuple | None:
    # three bars and a gold coin, in a 100x100 design space
    u, v = (x - ox) / scale, (y - oy) / scale
    for left, top in ((18, 52), (40, 34), (62, 18)):
        if left <= u <= left + 14 and top <= v <= 82:
            return FG
    if 16 <= u <= 84 and 86 <= v <= 90:
        return FG
    if (u - 80) ** 2 + (v - 70) ** 2 <= 11 ** 2:
        return GOLD
    return None


def icon(x: int, y: int):
    if not rounded(x, y, 128, 128, 26):
        return (0, 0, 0, 0)
    color = bars(x, y, 14, 12, 1.0)
    return (*(color or BG), 255)


def logo(x: int, y: int):
    if not rounded(x, y, 250, 100, 20):
        return (0, 0, 0, 0)
    color = bars(x, y, 75, 2, 0.95)
    return (*(color or BG), 255)


if __name__ == "__main__":
    png(OUT / "icon.png", 128, 128, icon)
    png(OUT / "logo.png", 250, 100, logo)
    print("icon.png und logo.png geschrieben")

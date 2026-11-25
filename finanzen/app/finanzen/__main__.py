"""Start the app: `python -m finanzen` (in the container) or `python -m finanzen --dev` (locally)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from pathlib import Path

from aiohttp import web

from . import __version__
from .core import FinanzenApp
from .web import create_web_app

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


def load_options(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


async def run(args) -> None:
    data_dir = Path(args.data)
    options = load_options(data_dir / "options.json")
    logging.basicConfig(
        level=LEVELS.get(str(options.get("log_level", "info")), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    core = FinanzenApp(data_dir, options, dev=args.dev)
    await core.start()
    runner = web.AppRunner(create_web_app(core))
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()
    logging.getLogger(__name__).info("Finanzen %s läuft auf Port %s", __version__, args.port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows
            pass
    try:
        await stop.wait()
    finally:
        await runner.cleanup()
        await core.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="finanzen")
    parser.add_argument("--data", default="/data", help="Datenordner (Standard: /data)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--dev", action="store_true", help="Lokal ohne Home Assistant und ohne Ingress-Prüfung")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

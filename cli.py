from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import formatting
from .config import Config, config_path, load_dotenv_if_present
from .exceptions import ConfigError, friendly_rpc_message
from .fees import build_service

BANNER = "\U0001f6e1\ufe0f  GasWatch  live Ethereum gas tracker"


def add_common(target: argparse.ArgumentParser, suppress: bool) -> None:
    def default(value):
        return argparse.SUPPRESS if suppress else value

    target.add_argument("--verbose", "-v", action="count", default=default(0), help="more logging on stderr")
    target.add_argument("--endpoint", "-e", action="append", default=default([]),
                        help="override the RPC endpoint list, repeatable")
    target.add_argument("--timeout", type=float, default=default(None), help="per request timeout in seconds")
    target.add_argument("--retries", type=int, default=default(None), help="retries per request")
    target.add_argument("--units", choices=["gwei", "kwei", "mwei", "wei"], default=default(None))
    target.add_argument("--markdown", action="store_true", default=default(False),
                        help="print raw markdown instead of a colour terminal report")
    target.add_argument("--price", action="store_true", default=default(False),
                        help="add ETH/USD cost estimates")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gaswatch", description="Live Ethereum base fee and priority fee tracker.")
    add_common(parser, suppress=False)
    sub = parser.add_subparsers(dest="command")
    names = {
        "gui": "launch the desktop app (default)",
        "once": "print one report and exit",
        "watch": "refresh the terminal report on a timer",
        "ping": "check every RPC endpoint and report latency",
        "endpoints": "show the configured RPC endpoints",
        "thresholds": "show the gas level cutoffs",
        "config": "print the resolved configuration as JSON",
        "selftest": "verify the RPC reader and the GUI, then exit",
    }
    for name, description in names.items():
        child = sub.add_parser(name, help=description)
        add_common(child, suppress=True)
        if name == "selftest":
            child.add_argument("--out", default=None, help="write the report to this file")
    return parser


def setup_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s %(message)s", stream=sys.stderr)


def resolve_config(args: argparse.Namespace) -> Config:
    load_dotenv_if_present()
    config = Config.load()
    if getattr(args, "endpoint", None):
        config.rpc_endpoints = list(args.endpoint)
    if getattr(args, "timeout", None) is not None:
        config.rpc_timeout = args.timeout
    if getattr(args, "retries", None) is not None:
        config.rpc_retries = args.retries
    if getattr(args, "units", None) is not None:
        config.units = args.units
    if getattr(args, "price", False):
        config.price_enabled = True
        config.show_usd = True
    config.validate()
    return config


def cmd_gui(config: Config) -> int:
    from .gui import run_gui

    return run_gui(config)


def cmd_once(config: Config, markdown: bool) -> int:
    service = build_service(config)
    try:
        snapshot = service.fetch()
    except Exception as exc:
        if markdown:
            print(formatting.render_error(exc, config))
        else:
            print(formatting.render_terminal_error(exc, config), file=sys.stderr)
        return 1
    if markdown:
        print(formatting.render_markdown(snapshot, config))
    else:
        print(formatting.render_terminal(snapshot, config))
    return 0


def cmd_watch(config: Config, markdown: bool) -> int:
    import time

    service = build_service(config)
    interval = max(3, config.gui_refresh_interval)
    try:
        while True:
            sys.stdout.write("\033[2J\033[H" if not markdown else "")
            try:
                snapshot = service.fetch()
                text = formatting.render_markdown(snapshot, config) if markdown else formatting.render_terminal(snapshot, config)
                print(text, flush=True)
            except Exception as exc:
                text = formatting.render_error(exc, config) if markdown else formatting.render_terminal_error(exc, config)
                print(text, file=sys.stderr, flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_ping(config: Config) -> int:
    import time

    from .rpc import RPCPool

    print(BANNER)
    print()
    rows: list[tuple[bool, str, float, str]] = []
    alive = 0
    for url in config.rpc_endpoints:
        single = config.copy()
        single.rpc_endpoints = [url]
        single.rpc_retries = 0
        started = time.monotonic()
        try:
            with RPCPool(single).connect() as w3:
                number = w3.eth.block_number
            rows.append((True, url, (time.monotonic() - started) * 1000, f"block {number:,}"))
            alive += 1
        except Exception as exc:
            rows.append((False, url, (time.monotonic() - started) * 1000, friendly_rpc_message(exc)[:70]))
    for ok, url, latency, detail in rows:
        mark = "\U0001f7e2" if ok else "\U0001f534"
        print(f"{mark} {url:<44} {latency:8.1f}ms   {detail}")
    print(f"\n{alive}/{len(rows)} endpoints reachable")
    return 0 if alive else 1


def cmd_endpoints(config: Config) -> int:
    service = build_service(config)
    print(BANNER)
    print(formatting.to_ansi(formatting.endpoint_blocks(service.endpoints_report())))
    return 0


def cmd_selftest(config: Config, out: str | None) -> int:
    import tkinter as tk

    from .gui import GasWatchApp, build_service

    lines: list[str] = []
    failures = 0
    snapshot = None

    try:
        snapshot = build_service(config).fetch()
        lines.append(f"rpc_fetch: ok block {snapshot.block_number} base {snapshot.base_fee_gwei} Gwei "
                     f"tip {snapshot.tip_gwei} Gwei via {snapshot.endpoint}")
        lines.append(f"markdown_bytes: {len(formatting.render_markdown(snapshot, config))}")
    except Exception as exc:
        failures += 1
        lines.append(f"rpc_fetch: failed {friendly_rpc_message(exc)}")

    try:
        root = tk.Tk()
        root.withdraw()
        app = GasWatchApp(root, config)
        if snapshot is not None:
            app._apply_snapshot(snapshot, 0.42)
        root.update_idletasks()
        lines.append(f"status_pill: {app.status_pill.cget('text')}")
        lines.append(f"cards: {[card.value_label.cget('text') for card in app.cards]}")
        lines.append(f"detail_lines: {len(app.detail_text.get('1.0', 'end').splitlines())}")
        lines.append(f"preview_lines: {len(app.preview_text.get('1.0', 'end').splitlines())}")
        root.destroy()
        lines.append("gui: ok")
    except Exception as exc:
        failures += 1
        lines.append(f"gui: failed {type(exc).__name__}: {exc}")

    report = "\n".join(lines)
    if out:
        Path(out).write_text(report + "\n", encoding="utf-8")
    else:
        print(report)
    return 1 if failures else 0


def cmd_thresholds(config: Config) -> int:
    print(BANNER)
    print(formatting.to_ansi(formatting.threshold_blocks(config)))
    return 0


def cmd_config(config: Config) -> int:
    payload = config.to_dict()
    payload["_config_file"] = str(config_path())
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    command = args.command or "gui"
    try:
        config = resolve_config(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    handlers = {
        "gui": lambda: cmd_gui(config),
        "once": lambda: cmd_once(config, args.markdown),
        "watch": lambda: cmd_watch(config, args.markdown),
        "ping": lambda: cmd_ping(config),
        "endpoints": lambda: cmd_endpoints(config),
        "thresholds": lambda: cmd_thresholds(config),
        "selftest": lambda: cmd_selftest(config, getattr(args, "out", None)),
        "config": lambda: cmd_config(config),
    }
    try:
        return handlers[command]()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
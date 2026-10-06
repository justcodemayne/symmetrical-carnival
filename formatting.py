from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Config
from .exceptions import friendly_rpc_message
from .fees import FeeSnapshot
from .thresholds import ADVICE, RESET, GasLevel

MARKDOWN_V2_SPECIALS = "_*[]()~`>#+-=|{}.!"

UNIT_SUFFIX = {"gwei": "Gwei", "kwei": "Kwei", "mwei": "Mwei", "wei": "Wei"}
UNIT_WEI = {"wei": 1.0, "kwei": 1e3, "mwei": 1e6, "gwei": 1e9}


def esc(text) -> str:
    out = []
    for char in str(text):
        if char in MARKDOWN_V2_SPECIALS:
            out.append("\\")
        out.append(char)
    return "".join(out)


def mono(text) -> str:
    return "`" + str(text).replace("\\", "\\\\").replace("`", "\\`") + "`"


def to_units(gwei: float | None, units: str = "gwei") -> float | None:
    if gwei is None:
        return None
    return gwei * 1e9 / UNIT_WEI.get(units, 1e9)


def gwei_str(value: float | None, units: str = "gwei") -> str:
    scaled = to_units(value, units)
    if scaled is None:
        return "n/a"
    suffix = UNIT_SUFFIX.get(units, "Gwei")
    magnitude = abs(scaled)
    if magnitude >= 1000:
        return f"{scaled:,.2f} {suffix}"
    if magnitude >= 1:
        return f"{scaled:,.3f} {suffix}"
    if magnitude >= 0.001:
        return f"{scaled:.4f} {suffix}"
    if magnitude == 0:
        return f"0 {suffix}"
    return f"{scaled:.6f} {suffix}"


def eth_str(wei: float | None) -> str:
    if wei is None:
        return "n/a"
    value = float(wei) / 1e18
    if value >= 1:
        return f"{value:,.5f} ETH"
    if value >= 0.00001:
        return f"{value:.6f} ETH"
    return f"{value:.3e} ETH"


def usd_str(value: float | None) -> str:
    if value is None or value <= 0:
        return ""
    if value >= 100:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:,.4f}"
    return f"${value:.2e}"


def age_str(stamp: float) -> str:
    delta = max(0.0, time.time() - stamp)
    if delta < 5:
        return "just now"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    return f"{int(delta // 3600)}h ago"


def utc_str(stamp: float | None) -> str:
    if not stamp:
        return "--:--:--"
    return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def trend_str(pct: float | None) -> str:
    if pct is None:
        return ""
    if pct > 0.5:
        return f"▲ +{pct:.2f}%"
    if pct < -0.5:
        return f"▼ {abs(pct):.2f}%"
    return "▬ flat"


@dataclass
class Run:
    text: str
    bold: bool = False
    code: bool = False
    level: GasLevel | None = None
    rule: bool = False


@dataclass
class Block:
    runs: list[Run] = field(default_factory=list)

    def add(self, text: str, bold: bool = False, code: bool = False, level: GasLevel | None = None) -> Block:
        self.runs.append(Run(text=text, bold=bold, code=code, level=level))
        return self

    def is_empty(self) -> bool:
        return not any(run.text for run in self.runs)


def _join(pieces: list[str]) -> str:
    out = ""
    for piece in pieces:
        if not piece:
            continue
        if out and not out.endswith(" ") and not piece.startswith(" "):
            out += " "
        out += piece
    return out.strip()


def badge(level: GasLevel) -> str:
    return f"{level.emoji} {level.value}"


def to_markdown(blocks: list[Block]) -> str:
    lines = []
    for block in blocks:
        if block.is_empty():
            lines.append("")
            continue
        pieces: list[str] = []
        for run in block.runs:
            if run.rule:
                pieces.append(esc(run.text))
                continue
            if run.text:
                if run.code:
                    pieces.append(mono(run.text))
                elif run.bold:
                    pieces.append(f"*{esc(run.text)}*")
                else:
                    pieces.append(esc(run.text))
            if run.level is not None:
                pieces.append(badge(run.level))
        lines.append(_join(pieces))
    return "\n".join(lines)


def to_ansi(blocks: list[Block]) -> str:
    lines = []
    for block in blocks:
        if block.is_empty():
            lines.append("")
            continue
        pieces: list[str] = []
        for run in block.runs:
            if run.text:
                text = run.text
                if run.bold:
                    text = f"\033[1m{text}\033[0m"
                if run.level is not None:
                    text = f"{run.level.ansi}{text}{RESET}"
                pieces.append(text)
            if run.level is not None:
                pieces.append(badge(run.level))
        lines.append(_join(pieces))
    return "\n".join(lines)


def to_plain(blocks: list[Block]) -> str:
    lines = []
    for block in blocks:
        if block.is_empty():
            lines.append("")
            continue
        pieces: list[str] = []
        for run in block.runs:
            if run.text:
                pieces.append(run.text)
            if run.level is not None:
                pieces.append(badge(run.level))
        lines.append(_join(pieces))
    return "\n".join(lines)


def build_report(snapshot: FeeSnapshot, config: Config, costs: bool = True) -> list[Block]:
    base_thresholds = config.base_thresholds
    tip_thresholds = config.tip_thresholds
    base_level = snapshot.level(base_thresholds)
    tip_level = snapshot.tip_level(tip_thresholds)
    overall = snapshot.overall_level(base_thresholds, tip_thresholds)
    units = config.units
    blocks: list[Block] = []

    blocks.append(Block([Run("", level=overall), Run("ETH GAS REPORT", bold=True)]))
    blocks.append(Block([Run("\u2501" * 62, rule=True)]))
    blocks.append(Block([
        Run("Base fee", bold=True),
        Run("   "),
        Run(gwei_str(snapshot.base_fee_gwei, units), code=True),
        Run("   "),
        Run(base_thresholds.meter(snapshot.base_fee_gwei)),
        Run("  "),
        Run("", level=base_level),
    ]))
    if snapshot.next_base_fee_gwei is not None:
        blocks.append(Block([
            Run("Next block"),
            Run("   "),
            Run(gwei_str(snapshot.next_base_fee_gwei, units), code=True),
            Run("   "),
            Run(trend_str(snapshot.base_trend_pct), level=base_level),
        ]))
    blocks.append(Block([
        Run("Tip fee", bold=True),
        Run("   "),
        Run(gwei_str(snapshot.tip_gwei, units), code=True),
        Run("   "),
        Run(tip_thresholds.meter(snapshot.tip_gwei)),
        Run("  "),
        Run("", level=tip_level),
    ]))
    blocks.append(Block([
        Run("Tip range"),
        Run("   "),
        Run(" \u00b7 ".join(gwei_str(v, units) for v in (snapshot.tip.low, snapshot.tip.median, snapshot.tip.high)), code=True),
        Run(f"   {snapshot.tip.samples} blk"),
    ]))
    if snapshot.max_fee_gwei is not None:
        blocks.append(Block([
            Run("Max fee", bold=True),
            Run("   "),
            Run(gwei_str(snapshot.max_fee_gwei, units), code=True),
        ]))
    if costs:
        rows = snapshot.costs_wei()
        if rows:
            blocks.append(Block())
            blocks.append(Block([Run("\U0001f4b0 ", level=None), Run("Estimated cost per transaction", bold=True)]))
            for label, gas, wei in rows:
                text = eth_str(wei)
                usd = usd_str(snapshot.eth_price_usd * wei / 1e18) if snapshot.eth_price_usd else ""
                if usd:
                    text = f"{text}  {usd}"
                blocks.append(Block([Run(f"{label:<18}{gas:>9} gas   "), Run(text, code=True)]))
    blocks.append(Block())
    if snapshot.block_number is not None:
        usage = f"{snapshot.gas_used_ratio * 100:.1f}% gas used" if snapshot.gas_used_ratio is not None else "usage n/a"
        blocks.append(Block([
            Run("\U0001f4e6 ", level=None),
            Run("Block "),
            Run(format(snapshot.block_number, ","), code=True),
            Run(f"   \u00b7   {usage}"),
        ]))
    if snapshot.min_base_fee_wei is not None and snapshot.max_base_fee_wei is not None:
        blocks.append(Block([
            Run(f"window over {snapshot.history_blocks} blocks:  "),
            Run(gwei_str(snapshot.min_base_fee_wei / 1e9, units), code=True),
            Run(" \u2192 "),
            Run(gwei_str(snapshot.max_base_fee_wei / 1e9, units), code=True),
        ]))
    blocks.append(Block([
        Run(f"\U0001f517 via {snapshot.endpoint}   \u00b7   {utc_str(snapshot.fetched_at)}   "),
        Run(age_str(snapshot.fetched_at)),
    ]))
    blocks.append(Block())
    blocks.append(Block([Run(ADVICE[overall], level=overall)]))
    for warning in snapshot.warnings:
        blocks.append(Block([Run(f"\u26a0\ufe0f  {warning}", level=GasLevel.MODERATE)]))
    return blocks


def render_markdown(snapshot: FeeSnapshot, config: Config, costs: bool = True) -> str:
    return to_markdown(build_report(snapshot, config, costs))


def render_terminal(snapshot: FeeSnapshot, config: Config, costs: bool = True) -> str:
    return to_ansi(build_report(snapshot, config, costs))


def render_compact(snapshot: FeeSnapshot, config: Config) -> str:
    base_thresholds = config.base_thresholds
    tip_thresholds = config.tip_thresholds
    overall = snapshot.overall_level(base_thresholds, tip_thresholds)
    units = config.units
    blocks = [Block([
        Run("", level=overall),
        Run(overall.value, bold=True),
        Run("  base "),
        Run(gwei_str(snapshot.base_fee_gwei, units), code=True),
        Run("  tip "),
        Run(gwei_str(snapshot.tip_gwei, units), code=True),
        Run("  max "),
        Run(gwei_str(snapshot.max_fee_gwei, units), code=True),
        Run(f"  blk {format(snapshot.block_number or 0, ',')}"),
    ])]
    return to_markdown(blocks)


def build_error(error: BaseException, config: Config) -> list[Block]:
    reason = friendly_rpc_message(error)
    blocks = [
        Block([Run("", level=GasLevel.EXTREME), Run("RPC CONNECTION FAILED", bold=True)]),
        Block([Run("\u2501" * 62, rule=True)]),
        Block([Run(reason, level=GasLevel.EXTREME)]),
        Block(),
        Block([Run(f"endpoints tried  {len(config.rpc_endpoints)}")]),
        Block([Run(f"timeout          {config.rpc_timeout}s")]),
        Block([Run(f"retries          {config.rpc_retries}")]),
    ]
    for name, detail in (getattr(error, "failures", None) or [])[:4]:
        blocks.append(Block([Run(f"  \u00b7 {name}: {detail[:88]}")]))
    blocks.extend([
        Block(),
        Block([Run("\U0001f6e0 ", level=GasLevel.MODERATE), Run("try in this order", bold=True)]),
        Block([Run("1. check your internet connection")]),
        Block([Run("2. switch RPC endpoint: "), Run("gaswatch endpoints", code=True)]),
        Block([Run("3. raise "), Run("GASWATCH_RPC_TIMEOUT", code=True), Run(" if the node is slow")]),
    ])
    return blocks


def render_error(error: BaseException, config: Config) -> str:
    return to_markdown(build_error(error, config))


def render_terminal_error(error: BaseException, config: Config) -> str:
    return to_ansi(build_error(error, config))


def help_blocks() -> list[Block]:
    commands = [
        ("/gas", "current base fee, priority fee and cost estimates"),
        ("/watch", "push a report on a timer"),
        ("/unwatch", "stop the push reports"),
        ("/endpoints", "RPC endpoint health and cooldowns"),
        ("/thresholds", "show the gas level cutoffs in use"),
        ("/help", "this message"),
    ]
    blocks = [
        Block([Run("\U0001f6e1\ufe0f "), Run("GasWatch", bold=True), Run("  live EIP-1559 fees, formatted")]),
        Block([Run("\u2501" * 62, rule=True)]),
        Block(),
    ]
    for name, description in commands:
        blocks.append(Block([Run(name, code=True), Run(f"   {description}")]))
    blocks.append(Block())
    blocks.append(Block([Run("just type "), Run("gas", code=True), Run(" in chat")]))
    return blocks


def render_help() -> str:
    return to_markdown(help_blocks())


def endpoint_blocks(report: list[dict]) -> list[Block]:
    blocks = [
        Block([Run("\U0001f50c "), Run("RPC ENDPOINTS", bold=True)]),
        Block([Run("\u2501" * 62, rule=True)]),
        Block(),
    ]
    for item in report:
        if item["cooldown_left"] > 0:
            level = GasLevel.EXTREME
        elif item["successes"]:
            level = GasLevel.LOW
        elif item["failures"]:
            level = GasLevel.MODERATE
        else:
            level = GasLevel.UNKNOWN
        summary = (
            f"{item['name']:<26} ok {item['successes']:>3}   fail {item['failures']:>3}"
            f"   {item['last_latency_ms']:>7.1f}ms"
        )
        if item["cooldown_left"] > 0:
            summary += f"   cooldown {item['cooldown_left']:.0f}s"
        blocks.append(Block([Run("", level=level), Run(summary, code=True)]))
        if item["last_error"]:
            blocks.append(Block([Run(f"    \u21b3 {item['last_error'][:80]}")]))
    return blocks


def render_endpoints(report: list[dict]) -> str:
    return to_markdown(endpoint_blocks(report))


def threshold_blocks(config: Config) -> list[Block]:
    base = config.base_thresholds
    tip = config.tip_thresholds
    blocks = [
        Block([Run("\u26fd "), Run("GAS LEVELS", bold=True)]),
        Block([Run("\u2501" * 62, rule=True)]),
        Block(),
    ]
    plan = [
        (GasLevel.LOW, "below", base.low_max, tip.low_max),
        (GasLevel.MODERATE, "up to", base.moderate_max, tip.moderate_max),
        (GasLevel.HIGH, "up to", base.high_max, tip.high_max),
        (GasLevel.EXTREME, "above", base.high_max, tip.high_max),
    ]
    for level, connector, base_value, tip_value in plan:
        blocks.append(Block([
            Run("", level=level),
            Run(f"   base fee {connector} {base_value:g} gwei   ·   priority fee {connector} {tip_value:g} gwei"),
        ]))
    return blocks


def render_thresholds(config: Config) -> str:
    return to_markdown(threshold_blocks(config))

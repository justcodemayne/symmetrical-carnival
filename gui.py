from __future__ import annotations

import logging
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import formatting
from .config import Config, config_path
from .exceptions import ConfigError, friendly_rpc_message
from .fees import FeeSnapshot, build_service
from .thresholds import GasLevel

log = logging.getLogger("gaswatch.gui")

BG = "#0b1120"
PANEL = "#111c33"
PANEL_HI = "#16233f"
BORDER = "#22304d"
TEXT = "#e2e8f0"
MUTED = "#8b9ab5"
ACCENT = "#38bdf8"

LABELS = ("Base fee", "Tip fee (priority)", "Max fee", "Block usage")

LEVEL_TAGS = {level.emoji: tag for level, tag in zip(
    (GasLevel.LOW, GasLevel.MODERATE, GasLevel.HIGH, GasLevel.EXTREME), ("g", "y", "o", "r"))}

MARKDOWN_TOKENS = re.compile(
    r"`[^`\n]*`|\*[^*\n]+\*|\\.|" + "|".join(re.escape(emoji) for emoji in LEVEL_TAGS))


class MetricCard(tk.Frame):
    def __init__(self, master, title: str) -> None:
        super().__init__(master, bg=PANEL, highlightbackground=BORDER, highlightthickness=1, bd=0)
        self.title_label = tk.Label(self, text=title, bg=PANEL, fg=MUTED, font=("Helvetica Neue", 10))
        self.title_label.pack(anchor="w", padx=14, pady=(10, 0))
        self.value_label = tk.Label(self, text="--", bg=PANEL, fg=TEXT, font=("Helvetica Neue", 22, "bold"))
        self.value_label.pack(anchor="w", padx=14)
        self.level_label = tk.Label(self, text="", bg=PANEL, fg=MUTED, font=("Helvetica Neue", 10))
        self.level_label.pack(anchor="w", padx=14, pady=(0, 2))
        self.meter = tk.Label(self, text="\u2591" * 10, bg=PANEL, fg=MUTED, font=("Menlo", 13))
        self.meter.pack(anchor="w", padx=14, pady=(0, 10))

    def update(self, value: str, level: GasLevel, meter: str) -> None:
        self.value_label.configure(text=value, fg=level.color)
        self.level_label.configure(text=f"{level.emoji} {level.value}", fg=level.color)
        self.meter.configure(text=meter, fg=level.color)


class GasWatchApp:
    def __init__(self, root: tk.Tk, config: Config) -> None:
        self.root = root
        self.config = config
        self.service = build_service(config)
        self.queue: queue.Queue = queue.Queue()
        self.auto_var = tk.BooleanVar(value=True)
        self.closing = False
        self._after_id: str | None = None
        self._inflight = False
        self._next_auto_at = 0.0
        self._failures = 0
        self.root.title("GasWatch")
        self.root.configure(bg=BG)
        self.root.geometry("1000x780")
        self.root.minsize(880, 700)
        self._build()
        self._install_menus()
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        self.root.after(120, self._drain)
        self.root.after(400, self.refresh)

    def _build(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Dark.TCheckbutton", background=BG, foreground=TEXT, focuscolor=BG)
        style.map("Dark.TCheckbutton", background=[("active", BG)], foreground=[("active", TEXT)])
        style.configure("Dark.Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL_HI, borderwidth=0)

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=22, pady=(18, 6))
        tk.Label(header, text="GasWatch", bg=BG, fg=TEXT, font=("Helvetica Neue", 26, "bold")).pack(side="left")
        self.status_pill = tk.Label(header, text="connecting", bg=PANEL_HI, fg=MUTED,
                                    font=("Helvetica Neue", 13, "bold"), padx=14, pady=6)
        self.status_pill.pack(side="right")
        tk.Label(header, text="live EIP-1559 base fee and priority fee", bg=BG, fg=MUTED,
                 font=("Helvetica Neue", 11)).pack(side="left", padx=12)

        cards = tk.Frame(self.root, bg=BG)
        cards.pack(fill="x", padx=22, pady=(8, 4))
        self.cards: list[MetricCard] = []
        for index, label in enumerate(LABELS):
            card = MetricCard(cards, label)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            cards.grid_columnconfigure(index, weight=1, uniform="card")
            self.cards.append(card)

        advice = tk.Frame(self.root, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        advice.pack(fill="x", padx=22, pady=(10, 4))
        self.advice_label = tk.Label(advice, text="waiting for the first update", bg=PANEL, fg=MUTED,
                                     font=("Helvetica Neue", 13), anchor="w", justify="left", wraplength=920, padx=16, pady=12)
        self.advice_label.pack(fill="x")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=22, pady=(10, 4))
        try:
            notebook.configure(style="Dark.TNotebook")
        except tk.TclError:
            pass

        details = tk.Frame(notebook, bg=BG)
        notebook.add(details, text="  Details  ")
        self.detail_text = tk.Text(details, bg=BG, fg=TEXT, font=("Menlo", 11), wrap="word",
                                   relief="flat", padx=16, pady=12, highlightthickness=0)
        detail_scroll = tk.Scrollbar(details, orient="vertical", command=self.detail_text.yview,
                                     bg=PANEL, troughcolor=BG, bd=0, width=12)
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        self.detail_text.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        self._tag(self.detail_text, "k", MUTED)
        self._tag(self.detail_text, "v", TEXT)
        self._tag(self.detail_text, "ok", GasLevel.LOW.color)
        self._tag(self.detail_text, "warn", GasLevel.MODERATE.color)
        self._tag(self.detail_text, "bad", GasLevel.EXTREME.color)
        self._tag(self.detail_text, "h", ACCENT)

        preview = tk.Frame(notebook, bg=BG)
        notebook.add(preview, text="  Markdown  ")
        self.preview_text = tk.Text(preview, bg=BG, fg=TEXT, font=("Menlo", 11), wrap="word",
                                    relief="flat", padx=16, pady=12, highlightthickness=0)
        preview_scroll = tk.Scrollbar(preview, orient="vertical", command=self.preview_text.yview,
                                      bg=PANEL, troughcolor=BG, bd=0, width=12)
        self.preview_text.configure(yscrollcommand=preview_scroll.set)
        self.preview_text.pack(side="left", fill="both", expand=True)
        preview_scroll.pack(side="right", fill="y")
        for name, color in (("b", ACCENT), ("v", TEXT), ("c", TEXT), ("x", MUTED),
                            ("g", GasLevel.LOW.color), ("y", GasLevel.MODERATE.color),
                            ("o", GasLevel.HIGH.color), ("r", GasLevel.EXTREME.color)):
            self._tag(self.preview_text, name, color)

        controls = tk.Frame(self.root, bg=BG)
        controls.pack(fill="x", padx=22, pady=(10, 6))
        self.refresh_button = ttk.Button(controls, text="Refresh now", command=self.refresh)
        self.refresh_button.pack(side="left")
        ttk.Checkbutton(controls, text="Auto refresh", variable=self.auto_var, style="Dark.TCheckbutton",
                        command=self._on_toggle_auto).pack(side="left", padx=12)
        self.interval_label = tk.Label(controls, text="", bg=BG, fg=MUTED, font=("Helvetica Neue", 11))
        self.interval_label.pack(side="left")
        ttk.Button(controls, text="Copy markdown", command=self.copy_markdown).pack(side="right")
        ttk.Button(controls, text="Settings", command=self.open_settings).pack(side="right", padx=8)

        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=22, pady=(0, 14))
        self.footer = tk.Label(footer, text="", bg=BG, fg=MUTED, font=("Helvetica Neue", 10), anchor="w")
        self.footer.pack(side="left")
        self._update_interval_label()

    @staticmethod
    def _tag(widget: tk.Text, name: str, color: str) -> None:
        widget.tag_configure(name, foreground=color)
        widget.tag_configure("mono", foreground=ACCENT)

    def _install_menus(self) -> None:
        import sys

        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="Refresh now", accelerator="Cmd+R / Ctrl+R", command=self.refresh)
        file_menu.add_separator()
        file_menu.add_command(label="Copy markdown", accelerator="Cmd+C / Ctrl+C", command=self.copy_markdown)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", accelerator="Cmd+Q / Ctrl+Q", command=self.shutdown)
        menu.add_cascade(label="File", menu=file_menu)
        view_menu = tk.Menu(menu, tearoff=0)
        view_menu.add_command(label="Settings", command=self.open_settings)
        view_menu.add_checkbutton(label="Auto refresh", variable=self.auto_var, command=self._on_toggle_auto)
        menu.add_cascade(label="View", menu=view_menu)
        self.root.configure(menu=menu)
        self.root.bind("<Command-r>", lambda _e: self.refresh())
        self.root.bind("<Control-r>", lambda _e: self.refresh())
        self.root.bind("<Command-q>", lambda _e: self.shutdown())
        self.root.bind("<Control-q>", lambda _e: self.shutdown())
        if sys.platform == "darwin":
            try:
                apple = tk.Menu(self.root, tearoff=0)
                apple.add_command(label="About GasWatch", command=self.show_about)
                apple.add_separator()
                apple.add_cascade(menu=menu)
                self.root.create_default_capsule_menu(apple)
                self.root.configure(menu=apple)
            except Exception:
                pass

    def show_about(self) -> None:
        messagebox.showinfo(
            "GasWatch",
            "GasWatch\n\nLive Ethereum base fee and priority fee with status indicators.\n\n"
            f"Config: {config_path()}",
        )

    def _update_interval_label(self) -> None:
        self.interval_label.configure(text=f"every {self.config.gui_refresh_interval}s")
        self._next_auto_at = time.monotonic() + self.config.gui_refresh_interval

    def _on_toggle_auto(self) -> None:
        self._next_auto_at = time.monotonic() + self.config.gui_refresh_interval

    def refresh(self) -> None:
        if self.closing or self._inflight:
            return
        self._inflight = True
        self.refresh_button.configure(state="disabled")
        self.status_pill.configure(text="fetching", fg=MUTED)
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        started = time.monotonic()
        try:
            snapshot = self.service.fetch()
            self.queue.put(("ok", snapshot, time.monotonic() - started))
        except Exception as exc:
            log.info("fetch failed: %s", exc)
            self.queue.put(("err", exc, time.monotonic() - started))

    def _drain(self) -> None:
        if self.closing:
            return
        while True:
            try:
                kind, payload, elapsed = self.queue.get_nowait()
            except queue.Empty:
                break
            self._inflight = False
            self.refresh_button.configure(state="normal")
            if kind == "ok":
                self._failures = 0
                self._apply_snapshot(payload, elapsed)
            else:
                self._failures += 1
                self._apply_error(payload, elapsed)
        if self.auto_var.get() and not self._inflight and time.monotonic() >= self._next_auto_at:
            self._next_auto_at = time.monotonic() + max(3, self.config.gui_refresh_interval)
            self.refresh()
        self.root.after(120, self._drain)

    def _apply_snapshot(self, snapshot: FeeSnapshot, elapsed: float) -> None:
        base_thresholds = self.config.base_thresholds
        tip_thresholds = self.config.tip_thresholds
        base_level = snapshot.level(base_thresholds)
        tip_level = snapshot.tip_level(tip_thresholds)
        overall = snapshot.overall_level(base_thresholds, tip_thresholds)
        units = self.config.units
        usage = f"{snapshot.gas_used_ratio * 100:.1f}%" if snapshot.gas_used_ratio is not None else "n/a"
        usage_level = usage_level_for(snapshot.gas_used_ratio)

        self.cards[0].update(formatting.gwei_str(snapshot.base_fee_gwei, units), base_level,
                             base_thresholds.meter(snapshot.base_fee_gwei))
        self.cards[1].update(formatting.gwei_str(snapshot.tip_gwei, units), tip_level,
                             tip_thresholds.meter(snapshot.tip_gwei))
        self.cards[2].update(formatting.gwei_str(snapshot.max_fee_gwei, units), overall,
                             base_thresholds.meter(snapshot.max_fee_gwei))
        self.cards[3].update(usage, usage_level, usage_meter(snapshot.gas_used_ratio))

        self.status_pill.configure(text=f"{overall.emoji}  {overall.value}", fg=overall.color)
        advice = ADVICE_LOCAL.get(overall, "")
        extra = f"   {formatting.trend_str(snapshot.base_trend_pct)} next block" if snapshot.base_trend_pct else ""
        self.advice_label.configure(text=f"{overall.emoji} {advice}{extra}", fg=overall.color)

        self._render_details(snapshot, elapsed)
        self._render_preview(snapshot)
        stamp = time.strftime("%H:%M:%S", time.localtime(snapshot.fetched_at))
        self.footer.configure(
            text=f"block {snapshot.block_number:,}   ·   via {snapshot.endpoint}   ·   "
                 f"updated {stamp}   ·   {elapsed * 1000:.0f}ms"
            if snapshot.block_number
            else f"via {snapshot.endpoint}   ·   updated {stamp}   ·   {elapsed * 1000:.0f}ms",
            fg=MUTED,
        )

    def _apply_error(self, error: BaseException, elapsed: float) -> None:
        self.status_pill.configure(text="\U0001f534  OFFLINE", fg=GasLevel.EXTREME.color)
        for card in self.cards:
            card.update("error", GasLevel.EXTREME, "\u2591" * 10)
        message = friendly_rpc_message(error)
        hint = "Retrying automatically. Open Settings to switch RPC endpoint."
        if self._failures > 1:
            hint = f"Failed {self._failures} times in a row. {hint}"
        self.advice_label.configure(text=f"\U0001f534 {message}\n{hint}", fg=GasLevel.EXTREME.color)
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self._write(self.detail_text, "RPC CONNECTION FAILED\n", "r")
        self._write(self.detail_text, f"{message}\n\n", "bad")
        for name, reason in (getattr(error, "failures", None) or [])[:8]:
            self._write(self.detail_text, f"{name}\n", "v")
            self._write(self.detail_text, f"    {reason[:160]}\n", "warn")
        self._write(self.detail_text, "\nendpoints tried: ", "k")
        self._write(self.detail_text, f"{len(self.config.rpc_endpoints)}\n", "v")
        self._write(self.detail_text, "timeout: ", "k")
        self._write(self.detail_text, f"{self.config.rpc_timeout}s\n", "v")
        self._write(self.detail_text, "retries: ", "k")
        self._write(self.detail_text, f"{self.config.rpc_retries}\n", "v")
        self._write(self.detail_text, "\nelapsed: ", "k")
        self._write(self.detail_text, f"{elapsed * 1000:.0f}ms\n", "v")
        self.detail_text.configure(state="disabled")
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", "RPC connection failed, see the details tab.")
        self.preview_text.configure(state="disabled")
        self.footer.configure(text=f"last attempt {elapsed * 1000:.0f}ms   ·   {message}", fg=GasLevel.EXTREME.color)

    def _render_details(self, snapshot: FeeSnapshot, elapsed: float) -> None:
        units = self.config.units
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        w = self._write

        w(self.detail_text, "BASE FEE\n", "h")
        w(self.detail_text, f"  current          {formatting.gwei_str(snapshot.base_fee_gwei, units)}\n", "v")
        if snapshot.next_base_fee_gwei is not None:
            w(self.detail_text, f"  next block      {formatting.gwei_str(snapshot.next_base_fee_gwei, units)}"
                               f"   {formatting.trend_str(snapshot.base_trend_pct)}\n", "v")
        if snapshot.min_base_fee_wei is not None:
            w(self.detail_text, f"  {snapshot.history_blocks} block window   "
                               f"{formatting.gwei_str(snapshot.min_base_fee_wei / 1e9, units)}"
                               f" → {formatting.gwei_str(snapshot.max_base_fee_wei / 1e9, units)}\n", "v")

        w(self.detail_text, "\nPRIORITY FEE (tips paid to the validator)\n", "h")
        w(self.detail_text, f"  suggested       {formatting.gwei_str(snapshot.tip_gwei, units)}\n", "v")
        w(self.detail_text, f"  p10 / p50 / p90 {formatting.gwei_str(snapshot.tip.low, units)}"
                           f" / {formatting.gwei_str(snapshot.tip.median, units)}"
                           f" / {formatting.gwei_str(snapshot.tip.high, units)}\n", "v")
        w(self.detail_text, f"  samples          {snapshot.tip.samples} blocks\n", "k")

        w(self.detail_text, "\nTRANSACTION COST ESTIMATES\n", "h")
        for label, gas, wei in snapshot.costs_wei():
            text = formatting.eth_str(wei)
            if snapshot.eth_price_usd:
                text += f"   {formatting.usd_str(snapshot.eth_price_usd * wei / 1e18)}"
            w(self.detail_text, f"  {label:<18}{gas:>9} gas   {text}\n", "v")

        w(self.detail_text, "\nBLOCK\n", "h")
        if snapshot.block_number is not None:
            w(self.detail_text, f"  number          {snapshot.block_number:,}\n", "v")
        if snapshot.block_timestamp:
            w(self.detail_text, f"  timestamp        {formatting.utc_str(snapshot.block_timestamp)}\n", "v")
        if snapshot.gas_used and snapshot.gas_limit:
            w(self.detail_text, f"  gas used         {snapshot.gas_used:,} / {snapshot.gas_limit:,}"
                               f"  ({snapshot.gas_used_ratio * 100:.1f}%)\n", "v")
        if snapshot.eth_price_usd:
            w(self.detail_text, f"  eth price        ${snapshot.eth_price_usd:,.2f}\n", "v")

        w(self.detail_text, "\nENDPOINTS\n", "h")
        for item in self.service.endpoints_report():
            color = "ok" if item["healthy"] and item["successes"] else ("warn" if item["healthy"] else "bad")
            line = f"  {item['name']:<30} ok {item['successes']:>3}  fail {item['failures']:>3}  "
            line += f"{item['last_latency_ms']:>7.1f}ms"
            if item["cooldown_left"] > 0:
                line += f"  cooldown {item['cooldown_left']:.0f}s"
            w(self.detail_text, line + "\n", color)
            if item["last_error"]:
                w(self.detail_text, f"      {item['last_error'][:110]}\n", "warn")

        if snapshot.warnings:
            w(self.detail_text, "\nWARNINGS\n", "h")
            for warning in snapshot.warnings:
                w(self.detail_text, f"  {warning}\n", "warn")
        self.detail_text.configure(state="disabled")

    def _render_preview(self, snapshot: FeeSnapshot) -> None:
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        text = formatting.to_markdown(formatting.build_report(snapshot, self.config))
        cursor = 0
        for match in MARKDOWN_TOKENS.finditer(text):
            if match.start() > cursor:
                self.preview_text.insert("end", text[cursor:match.start()], "v")
            token = match.group()
            if token.startswith("`"):
                tag = "c"
            elif token.startswith("*"):
                tag = "b"
            elif token.startswith("\\"):
                tag = "x"
            else:
                tag = LEVEL_TAGS.get(token, "v")
            self.preview_text.insert("end", token, tag)
            cursor = match.end()
        if cursor < len(text):
            self.preview_text.insert("end", text[cursor:], "v")
        self.preview_text.configure(state="disabled")

    @staticmethod
    def _write(widget: tk.Text, text: str, tag: str) -> None:
        widget.insert("end", text, tag)

    def copy_markdown(self) -> None:
        snapshot = self.service.last_snapshot
        if snapshot is None:
            messagebox.showinfo("GasWatch", "Nothing to copy yet, refresh first.")
            return
        text = formatting.to_markdown(formatting.build_report(snapshot, self.config))
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self.footer.configure(text="markdown report copied to the clipboard", fg=GasLevel.LOW.color)

    def open_settings(self) -> SettingsDialog:
        return SettingsDialog(self.root, self.config, self._on_settings_saved)

    def _on_settings_saved(self, config: Config) -> None:
        self.config = config
        self.service = build_service(config)
        self._update_interval_label()
        self._next_auto_at = 0.0
        self.refresh()

    def shutdown(self) -> None:
        self.closing = True
        self.root.destroy()


ADVICE_LOCAL = {
    GasLevel.LOW: "Cheap right now. Good moment to send.",
    GasLevel.MODERATE: "Normal network conditions. Fine for most activity.",
    GasLevel.HIGH: "Busy network. Batch transactions or wait for the next dip.",
    GasLevel.EXTREME: "Very congested. Sending now is expensive, consider waiting.",
    GasLevel.UNKNOWN: "Waiting for fee data.",
}


def usage_level_for(ratio: float | None) -> GasLevel:
    if ratio is None:
        return GasLevel.UNKNOWN
    if ratio < 0.5:
        return GasLevel.LOW
    if ratio < 0.8:
        return GasLevel.MODERATE
    if ratio < 0.95:
        return GasLevel.HIGH
    return GasLevel.EXTREME


def usage_meter(ratio: float | None) -> str:
    if ratio is None:
        return "\u2591" * 10
    filled = int(max(0.0, min(1.0, ratio)) * 10)
    return "\u2588" * filled + "\u2591" * (10 - filled)


SETTINGS_FIELDS = [

        ("RPC endpoints (one per line)", "endpoints", "text"),
        ("Request timeout (seconds)", "rpc_timeout", "float"),
        ("Retries per request", "rpc_retries", "int"),
        ("Failure cooldown (seconds)", "rpc_cooldown", "float"),
        ("Fee history blocks", "history_blocks", "int"),
        ("Auto refresh seconds", "gui_refresh_interval", "int"),
        ("Base fee LOW below (gwei)", "base_low_max", "float"),
        ("Base fee MODERATE below (gwei)", "base_moderate_max", "float"),
        ("Base fee HIGH below (gwei)", "base_high_max", "float"),
        ("Tip LOW below (gwei)", "tip_low_max", "float"),
        ("Tip MODERATE below (gwei)", "tip_moderate_max", "float"),
        ("Tip HIGH below (gwei)", "tip_high_max", "float"),
        ("Units (gwei, kwei, mwei, wei)", "units", "str"),
        ("Show ETH/USD estimates", "show_usd", "bool"),
    ]


class SettingsDialog:
    def __init__(self, parent: tk.Tk, config: Config, on_save) -> None:
        self.config = config
        self.on_save = on_save
        self.vars: dict[str, object] = {}
        self.window = tk.Toplevel(parent)
        self.window.title("GasWatch settings")
        self.window.configure(bg=BG)
        self.window.transient(parent)
        self.window.geometry("620x700")
        self._build()
        self._centre(parent)

    def _centre(self, parent: tk.Tk) -> None:
        self.window.update_idletasks()
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - self.window.winfo_width()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - self.window.winfo_height()) // 3
            self.window.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _build(self) -> None:
        body = tk.Frame(self.window, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=14)
        tk.Label(body, text="Settings are saved to\n" + str(config_path()), bg=BG, fg=MUTED,
                 font=("Helvetica Neue", 10), justify="left", anchor="w").pack(fill="x", pady=(0, 10))
        for label, key, kind in SETTINGS_FIELDS:
            row = tk.Frame(body, bg=BG)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=BG, fg=TEXT, font=("Helvetica Neue", 11),
                     width=34, anchor="w").pack(side="left")
            if kind == "bool":
                var = tk.BooleanVar(value=bool(getattr(self.config, key)))
                ttk.Checkbutton(row, variable=var, style="Dark.TCheckbutton").pack(side="left")
                self.vars[key] = var
                continue
            if kind == "text":
                var = tk.Text(row, height=5, width=30, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                              relief="flat", highlightthickness=1, highlightbackground=BORDER,
                              font=("Menlo", 10), wrap="none")
                var.insert("1.0", "\n".join(self.config.rpc_endpoints))
                var.pack(side="left", fill="x", expand=True)
                self.vars[key] = var
                continue
            var = tk.StringVar(value=str(getattr(self.config, key)))
            entry = ttk.Entry(row, textvariable=var, width=16)
            entry.pack(side="left")
            self.vars[key] = var
        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x", pady=(16, 0))
        ttk.Button(buttons, text="Reset to defaults", command=self._reset).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right", padx=8)
        self.error_label = tk.Label(body, text="", bg=BG, fg=GasLevel.EXTREME.color, font=("Helvetica Neue", 10),
                                    anchor="w", wraplength=560, justify="left")
        self.error_label.pack(fill="x", pady=(10, 0))

    def _reset(self) -> None:
        defaults = Config()
        self.window.destroy()
        self.on_save(defaults)

    def _collect(self) -> Config:
        data = self.config.to_dict()
        data["rpc_endpoints"] = [
            line.strip()
            for line in self.vars["endpoints"].get("1.0", "end").splitlines()
            if line.strip()
        ]
        for _label, key, kind in self.FIELDS:
            if kind == "text":
                continue
            value = self.vars[key].get()
            data[key] = value
        return Config.coerce(data)

    def _save(self) -> None:
        try:
            config = self._collect()
            config.validate()
        except (ConfigError, ValueError, TypeError) as exc:
            self.error_label.configure(text=str(exc))
            return
        try:
            config.save()
        except OSError as exc:
            self.error_label.configure(text=f"Could not write the config file: {exc}")
            return
        self.window.destroy()
        self.on_save(config)


def run_gui(config: Config | None = None) -> int:
    from .config import load_dotenv_if_present

    load_dotenv_if_present()
    config = config or Config.load()
    try:
        config.validate()
    except ConfigError as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("GasWatch", f"Configuration problem:\n\n{exc}")
        root.destroy()
        return 1
    root = tk.Tk()
    GasWatchApp(root, config)
    root.mainloop()
    return 0
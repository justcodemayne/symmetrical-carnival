from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gaswatch import formatting as F
from gaswatch.config import ENV_MAP, Config
from gaswatch.exceptions import (
    AllEndpointsFailedError,
    ConfigError,
    RPCConnectionError,
    friendly_rpc_message,
)
from gaswatch.fees import FeeSnapshot, GasService, TipStats, to_gwei
from gaswatch.rpc import RPCPool, pretty_endpoint
from gaswatch.thresholds import GasLevel, ThresholdSet

DEAD = "http://127.0.0.1:1/nope"


class FakeProvider:
    endpoint_uri = "https://fake.test"

    def disconnect(self) -> None:
        return None


class FakeWeb3:
    def __init__(self, connected: bool = True) -> None:
        self.provider = FakeProvider()
        self._connected = connected

    def is_connected(self) -> bool:
        return self._connected


def make_snapshot(base_gwei=0.5, tip_gwei=0.8, block=21_000_000) -> FeeSnapshot:
    return FeeSnapshot(
        base_fee_wei=int(base_gwei * 1e9),
        tip=TipStats(low=0.1, median=tip_gwei, high=2.0, suggested=tip_gwei, samples=20),
        endpoint="test-node",
        block_number=block,
        block_timestamp=1_700_000_000,
        gas_used=30_000_000,
        gas_limit=60_000_000,
        next_base_fee_wei=int(base_gwei * 0.98 * 1e9),
        min_base_fee_wei=int(0.4 * 1e9),
        max_base_fee_wei=int(0.9 * 1e9),
        history_blocks=20,
        fetched_at=time.time(),
    )


def assert_markdown_v2_valid(case: unittest.TestCase, text: str) -> None:
    specials = set(F.MARKDOWN_V2_SPECIALS)
    in_code = False
    in_bold = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            case.assertTrue(index + 1 < len(text), f"dangling backslash in {text!r}")
            index += 2
            continue
        if in_code:
            if char == "`":
                in_code = False
            index += 1
            continue
        if char == "`":
            in_code = True
            index += 1
            continue
        if char == "*":
            in_bold = not in_bold
            index += 1
            continue
        case.assertNotIn(char, specials, f"unescaped {char!r} in {text!r}")
        index += 1
    case.assertFalse(in_code, f"unclosed code span in {text!r}")
    case.assertFalse(in_bold, f"unclosed bold marker in {text!r}")


class ThresholdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = ThresholdSet(2.0, 8.0, 25.0)
        self.tip = ThresholdSet(0.3, 1.5, 5.0)

    def test_boundaries(self) -> None:
        self.assertEqual(self.base.classify(None), GasLevel.UNKNOWN)
        self.assertEqual(self.base.classify(0.0), GasLevel.LOW)
        self.assertEqual(self.base.classify(1.999), GasLevel.LOW)
        self.assertEqual(self.base.classify(2.0), GasLevel.MODERATE)
        self.assertEqual(self.base.classify(7.999), GasLevel.MODERATE)
        self.assertEqual(self.base.classify(8.0), GasLevel.HIGH)
        self.assertEqual(self.base.classify(24.9), GasLevel.HIGH)
        self.assertEqual(self.base.classify(25.0), GasLevel.EXTREME)
        self.assertEqual(self.base.classify(500), GasLevel.EXTREME)

    def test_colors_green_when_low_red_when_high(self) -> None:
        green = self.base.classify(0.4).color.lower()
        red = self.base.classify(80).color.lower()
        r_g, g_g, b_g = (int(green[i:i + 2], 16) for i in (1, 3, 5))
        r_r, g_r, b_r = (int(red[i:i + 2], 16) for i in (1, 3, 5))
        self.assertGreater(g_g, r_g)
        self.assertGreater(g_g, b_g)
        self.assertGreater(r_r, g_r)
        self.assertGreater(r_r, b_r)

    def test_emoji_ordering(self) -> None:
        self.assertEqual(GasLevel.LOW.emoji, "\U0001f7e2")
        self.assertEqual(GasLevel.EXTREME.emoji, "\U0001f534")
        order = [GasLevel.LOW, GasLevel.MODERATE, GasLevel.HIGH, GasLevel.EXTREME]
        self.assertEqual(sorted(order, key=lambda lvl: lvl.sort_index), order)

    def test_meter_monotonic(self) -> None:
        widths = {len(self.base.meter(v)) for v in (0.0, 1, 5, 20, 100, 1000)}
        self.assertEqual(widths, {8})
        filled = [self.base.meter(v).count("\u2588") for v in (0.0, 1, 5, 20, 100, 1000)]
        self.assertEqual(filled, sorted(filled))
        self.assertEqual(self.base.meter(None).count("\u2588"), 0)


class FormattingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.snapshot = make_snapshot()

    def test_esc(self) -> None:
        self.assertEqual(F.esc("a_b"), "a\\_b")
        self.assertEqual(F.esc("1.5 gwei"), "1\\.5 gwei")
        self.assertEqual(F.esc("plain"), "plain")

    def test_mono_escapes_backticks_only(self) -> None:
        self.assertEqual(F.mono("1.2 gwei"), "`1.2 gwei`")
        self.assertEqual(F.mono("a`b"), "`a\\`b`")

    def test_gwei_str_precision(self) -> None:
        self.assertIn("0.2246", F.gwei_str(0.22465))
        self.assertIn("2.000", F.gwei_str(2.0))
        self.assertIn("1,234.50", F.gwei_str(1234.5))
        self.assertEqual(F.gwei_str(None), "n/a")

    def test_unit_conversion(self) -> None:
        self.assertEqual(F.to_units(2, "gwei"), 2)
        self.assertEqual(F.to_units(2, "wei"), 2_000_000_000.0)
        self.assertEqual(F.to_units(2, "kwei"), 2_000_000.0)
        self.assertEqual(F.to_units(2, "mwei"), 2_000.0)
        self.assertIsNone(F.to_units(None, "gwei"))
        self.assertIn("Wei", F.gwei_str(2_000_000_000, "wei"))
        self.assertIn("Kwei", F.gwei_str(2_000_000_000, "kwei"))
        self.assertIn("Mwei", F.gwei_str(2_000_000_000, "mwei"))

    def test_markdown_is_valid(self) -> None:
        for snapshot in (
            make_snapshot(0.2, 0.1),
            make_snapshot(3.0, 1.0),
            make_snapshot(12.0, 2.5),
            make_snapshot(90.0, 9.0),
        ):
            text = F.render_markdown(snapshot, self.config)
            assert_markdown_v2_valid(self, text)

    def test_status_indicator_present(self) -> None:
        low = F.render_markdown(make_snapshot(0.2, 0.1), self.config)
        high = F.render_markdown(make_snapshot(90.0, 9.0), self.config)
        self.assertIn("\U0001f7e2", low)
        self.assertIn("LOW", low)
        self.assertIn("\U0001f534", high)
        self.assertIn("EXTREME", high)

    def test_trend_direction(self) -> None:
        self.assertIn("▲", F.trend_str(3.0))
        self.assertIn("▼ 3.00%", F.trend_str(-3.0))
        self.assertNotIn("-", F.trend_str(-3.0))
        self.assertIn("flat", F.trend_str(0.1))

    def test_costs_and_max_fee(self) -> None:
        snapshot = make_snapshot(1.0, 1.0)
        self.assertEqual(snapshot.max_fee_gwei, 2.0)
        rows = snapshot.costs_wei()
        self.assertEqual(rows[0][1], 21_000)
        self.assertAlmostEqual(rows[0][2], 21_000 * 2.0 * 1e9, places=0)
        self.assertAlmostEqual(rows[0][2] / 1e18, 21_000 * 2.0 * 1e-9, places=12)

    def test_renderers_agree_on_values(self) -> None:
        snapshot = make_snapshot(1.234, 5.678)
        markdown = F.render_markdown(snapshot, self.config)
        plain = F.to_plain(F.build_report(snapshot, self.config))
        ansi = F.to_ansi(F.build_report(snapshot, self.config))
        for text in (markdown, plain):
            self.assertIn("1.234", text)
            self.assertIn("5.678", text)
        self.assertIn("\033[", ansi)
        self.assertNotIn("*", plain)

    def test_compact_and_helpers_valid(self) -> None:
        assert_markdown_v2_valid(self, F.render_compact(make_snapshot(), self.config))
        assert_markdown_v2_valid(self, F.render_help())
        assert_markdown_v2_valid(self, F.render_thresholds(self.config))

    def test_error_render_has_guidance(self) -> None:
        error = AllEndpointsFailedError([("node-a", "ConnectionError: refused"), ("node-b", "Timeout")])
        text = F.render_error(error, self.config)
        assert_markdown_v2_valid(self, text)
        self.assertIn("RPC CONNECTION FAILED", text)
        self.assertIn("node\\-a", text)
        self.assertIn("\U0001f534", text)
        self.assertIn("2", text)

    def test_endpoint_blocks(self) -> None:
        report = [
            {"name": "a", "url": "https://a", "successes": 3, "failures": 0, "healthy": True,
             "cooldown_left": 0.0, "last_latency_ms": 12.0, "last_error": ""},
            {"name": "b", "url": "https://b", "successes": 0, "failures": 2, "healthy": False,
             "cooldown_left": 30.0, "last_latency_ms": 0.0, "last_error": "boom"},
        ]
        text = F.render_endpoints(report)
        assert_markdown_v2_valid(self, text)
        self.assertIn("boom", text)
        self.assertIn("\U0001f534", text)

    def test_to_gwei_helper(self) -> None:
        self.assertIsNone(to_gwei(None))
        self.assertEqual(to_gwei(1_500_000_000), 1.5)


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env_patch = dict(os.environ)
        os.environ["GASWATCH_CONFIG_DIR"] = str(self.dir)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env_patch)
        self.tmp.cleanup()

    def test_defaults_validate(self) -> None:
        Config().validate()

    def test_save_load_roundtrip(self) -> None:
        config = Config()
        config.base_low_max = 3.5
        config.rpc_endpoints = ["https://example.test/rpc"]
        path = config.save()
        self.assertTrue(path.exists())
        loaded = Config.load(use_env=False)
        self.assertEqual(loaded.base_low_max, 3.5)
        self.assertEqual(loaded.rpc_endpoints, ["https://example.test/rpc"])

    def test_env_override(self) -> None:
        os.environ["GASWATCH_RPC_TIMEOUT"] = "3.5"
        os.environ["GASWATCH_RPC_ENDPOINTS"] = "https://one.test, https://two.test"
        os.environ["GASWATCH_SHOW_USD"] = "true"
        config = Config.load()
        self.assertEqual(config.rpc_timeout, 3.5)
        self.assertEqual(config.rpc_endpoints, ["https://one.test", "https://two.test"])
        self.assertTrue(config.show_usd)

    def test_every_env_key_is_mapped(self) -> None:
        self.assertEqual(set(ENV_MAP.values()) & {"TELEGRAM_BOT_TOKEN"}, set())

    def test_validation_errors(self) -> None:
        with self.assertRaises(ConfigError):
            Config(rpc_endpoints=[]).validate()
        with self.assertRaises(ConfigError):
            Config(rpc_endpoints=["not-a-url"]).validate()
        with self.assertRaises(ConfigError):
            Config(rpc_timeout=0).validate()
        with self.assertRaises(ConfigError):
            Config(rpc_retries=-1).validate()
        with self.assertRaises(ConfigError):
            Config(history_blocks=1).validate()
        with self.assertRaises(ConfigError):
            Config(base_low_max=9, base_moderate_max=2, base_high_max=25).validate()
        with self.assertRaises(ConfigError):
            Config(units="dollars").validate()
        with self.assertRaises(ConfigError):
            Config(gui_refresh_interval=1).validate()

    def test_coerce_ignores_unknown_keys(self) -> None:
        config = Config.coerce({"rpc_timeout": "2.5", "history_blocks": "30", "nonsense": 1})
        self.assertEqual(config.rpc_timeout, 2.5)
        self.assertEqual(config.history_blocks, 30)

    def test_bad_number_raises(self) -> None:
        with self.assertRaises(ConfigError):
            Config.coerce({"rpc_timeout": "abc"})

    def test_corrupt_config_file(self) -> None:
        (self.dir / "config.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ConfigError):
            Config.load()


class RpcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["GASWATCH_CONFIG_DIR"] = self.tmp.name

    def tearDown(self) -> None:
        os.environ.pop("GASWATCH_CONFIG_DIR", None)
        self.tmp.cleanup()

    def test_pretty_endpoint(self) -> None:
        self.assertEqual(pretty_endpoint("https://eth.example.org/rpc"), "eth.example.org")
        self.assertEqual(pretty_endpoint("notaurl"), "notaurl")

    def test_all_dead_endpoints_raises_with_details(self) -> None:
        config = Config(rpc_endpoints=[DEAD, DEAD + "2"], rpc_timeout=1.0, rpc_retries=0)
        pool = RPCPool(config)
        with self.assertRaises(AllEndpointsFailedError) as ctx:
            pool.call(lambda w3: w3.eth.block_number)
        self.assertEqual(len(ctx.exception.failures), 2)
        message = friendly_rpc_message(ctx.exception)
        self.assertIn("Could not reach any of the 2 Ethereum RPC nodes", message)
        self.assertTrue(ctx.exception.failures[0][1])

    def test_single_dead_endpoint_message(self) -> None:
        config = Config(rpc_endpoints=[DEAD], rpc_timeout=1.0, rpc_retries=0)
        pool = RPCPool(config)
        with self.assertRaises(AllEndpointsFailedError) as ctx:
            pool.call(lambda w3: w3.eth.block_number)
        self.assertEqual(len(ctx.exception.failures), 1)
        self.assertIn("RPC node unreachable", friendly_rpc_message(ctx.exception))

    def test_caller_errors_are_not_charged_to_endpoint(self) -> None:
        from unittest import mock

        config = Config(rpc_endpoints=["https://fake.test"], rpc_timeout=1.0, rpc_retries=0)
        pool = RPCPool(config)

        def bug(_w3):
            raise KeyError("caller bug")

        with mock.patch("gaswatch.rpc.provider_for", return_value=FakeWeb3()):
            with self.assertRaises(KeyError):
                pool.call(bug)
        health = pool.health_snapshot()[0]
        self.assertEqual(health.successes, 1)
        self.assertEqual(health.failures, 0)
        self.assertEqual(health.last_error, "")

    def test_transient_caller_errors_are_wrapped_not_charged(self) -> None:
        from unittest import mock

        config = Config(rpc_endpoints=["https://fake.test"], rpc_timeout=1.0, rpc_retries=0)
        pool = RPCPool(config)

        def boom(_w3):
            raise ValueError("bad payload")

        with mock.patch("gaswatch.rpc.provider_for", return_value=FakeWeb3()):
            with self.assertRaises(RPCConnectionError):
                pool.call(boom)
        health = pool.health_snapshot()[0]
        self.assertEqual(health.successes, 1)
        self.assertEqual(health.failures, 0)

    def test_healthy_endpoint_is_reused_first(self) -> None:
        from unittest import mock

        config = Config(rpc_endpoints=["https://a.test", "https://b.test"], rpc_timeout=1.0, rpc_retries=0)
        pool = RPCPool(config)
        with mock.patch("gaswatch.rpc.provider_for", return_value=FakeWeb3()):
            with pool.connect() as w3:
                self.assertTrue(w3.is_connected())
            with pool.connect() as w3:
                self.assertTrue(w3.is_connected())
        best = pool.health_snapshot()[0]
        self.assertEqual(best.url, "https://a.test")
        self.assertEqual(best.successes, 2)
        self.assertEqual(best.failures, 0)

    def test_register_endpoint_extends_config(self) -> None:
        config = Config(rpc_endpoints=[])
        pool = RPCPool(config)
        pool.register_endpoint("https://added.test")
        pool.register_endpoint("  ")
        self.assertEqual(config.rpc_endpoints, ["https://added.test"])


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["GASWATCH_CONFIG_DIR"] = self.tmp.name

    def tearDown(self) -> None:
        os.environ.pop("GASWATCH_CONFIG_DIR", None)
        self.tmp.cleanup()

    def test_fetch_failure_sets_state(self) -> None:
        config = Config(rpc_endpoints=[DEAD], rpc_timeout=1.0, rpc_retries=0)
        service = GasService(config)
        with self.assertRaises(AllEndpointsFailedError):
            service.fetch()
        self.assertIsNone(service.last_snapshot)

    def test_endpoints_report_shape(self) -> None:
        config = Config(rpc_endpoints=["https://a.test", "https://b.test"])
        report = GasService(config).endpoints_report()
        self.assertEqual(len(report), 2)
        for row in report:
            self.assertEqual(
                set(row),
                {"name", "url", "successes", "failures", "healthy", "cooldown_left",
                 "last_latency_ms", "last_error"},
            )
            json.dumps(row)

    @unittest.skipUnless(os.environ.get("GASWATCH_LIVE_TESTS") == "1", "live network test")
    def test_live_fetch(self) -> None:
        config = Config()
        config.validate()
        snapshot = GasService(config).fetch()
        self.assertGreater(snapshot.base_fee_gwei, 0)
        self.assertIsNotNone(snapshot.tip.suggested)
        self.assertIsNotNone(snapshot.block_number)
        self.assertGreater(snapshot.history_blocks, 0)
        assert_markdown_v2_valid(self, F.render_markdown(snapshot, config))


class GuiTextTests(unittest.TestCase):
    class FakeText:
        def __init__(self) -> None:
            self.value = ""
            self.chunks: list[tuple[str, str | None]] = []

        def configure(self, **kwargs) -> None:
            return None

        def delete(self, *args) -> None:
            self.value = ""
            self.chunks = []

        def insert(self, _where, text, tag=None) -> None:
            self.value += text
            self.chunks.append((text, tag))

        def clipboard_clear(self) -> None:
            return None

        def clipboard_append(self, text) -> None:
            self.value = text

        def update_idletasks(self) -> None:
            return None

    def _app(self):
        from gaswatch import gui

        config = Config()
        snapshot = make_snapshot()
        app = gui.GasWatchApp.__new__(gui.GasWatchApp)
        app.config = config
        app.service = mock.Mock(last_snapshot=snapshot)
        app.preview_text = self.FakeText()
        app.root = self.FakeText()
        app.footer = self.FakeText()
        return app, snapshot, config

    def test_markdown_tab_shows_markdown_source(self) -> None:
        app, snapshot, config = self._app()
        gui_render = app._render_preview
        gui_render(snapshot)
        expected = F.render_markdown(snapshot, config)
        self.assertEqual(app.preview_text.value, expected)
        self.assertIn("*", app.preview_text.value)
        self.assertIn("`", app.preview_text.value)
        assert_markdown_v2_valid(self, app.preview_text.value)

    def test_markdown_tab_inserts_only_known_tags(self) -> None:
        app, snapshot, _ = self._app()
        app._render_preview(snapshot)
        known = {"b", "v", "c", "x", "g", "y", "o", "r"}
        for text, tag in app.preview_text.chunks:
            self.assertIn(tag, known, f"chunk {text!r} used unknown tag {tag!r}")
        self.assertEqual("".join(text for text, _ in app.preview_text.chunks),
                         app.preview_text.value)

    def test_copy_markdown_matches_preview(self) -> None:
        app, snapshot, config = self._app()
        app._render_preview(snapshot)
        preview = app.preview_text.value
        app.copy_markdown()
        self.assertEqual(app.root.value, preview)
        self.assertEqual(app.root.value, F.render_markdown(snapshot, config))

    def test_copy_markdown_without_snapshot_is_a_noop(self) -> None:
        app, _, _ = self._app()
        app.service = mock.Mock(last_snapshot=None)
        with mock.patch("gaswatch.gui.messagebox") as box:
            app.copy_markdown()
        box.showinfo.assert_called_once()
        self.assertEqual(app.root.value, "")

    def test_level_emoji_tokens_are_tagged(self) -> None:
        app, _, _ = self._app()
        for base, tip, expected in ((0.5, 0.1, "g"), (5.0, 1.0, "y"),
                                    (20.0, 3.0, "o"), (80.0, 9.0, "r")):
            app._render_preview(make_snapshot(base_gwei=base, tip_gwei=tip))
            tags = {tag for _, tag in app.preview_text.chunks}
            self.assertIn(expected, tags, f"base={base} tip={tip} missing {expected}")

    def test_markdown_syntax_tokens_are_highlighted(self) -> None:
        app, snapshot, _ = self._app()
        app._render_preview(snapshot)
        tags = {tag for _, tag in app.preview_text.chunks}
        self.assertLessEqual({"b", "c", "x", "v"}, tags)


if __name__ == "__main__":
    unittest.main(verbosity=2)
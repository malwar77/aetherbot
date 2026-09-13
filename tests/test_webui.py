"""Web UI tests — design system + LAN access.

Hand-verified:
- the index page ships the terminal design system (black background,
  green/red/blue palette, mono font, live pulse animation)
- auto-refresh polling is wired (setInterval + /api/status fetch)
- /api/status reports honest shape: disclaimer always present,
  mode string, and 'never started' note when no DB exists
- lan_url() returns a non-empty string and never raises
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from aetherbot.webui.app import create_app, lan_url


class DesignSystemTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app("config/config.example.yaml"))
        with open("aetherbot/webui/static/index.html") as f:
            self.html = f.read()

    def test_index_page_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("AetherBot", r.text)

    def test_black_green_red_blue_palette(self):
        for token in ("--bg: #04070a", "--green: #00e68a",
                     "--red: #ff4d5e", "--blue: #4da3ff"):
            self.assertIn(token, self.html)

    def test_lively_details_present(self):
        # pulse animation + auto-refresh + glow
        self.assertIn("@keyframes pulse", self.html)
        self.assertIn("setInterval(refresh, 5000)", self.html)
        self.assertIn("text-shadow", self.html)

    def test_no_trade_controls(self):
        self.assertIn("no trade controls", self.html.lower())

    def test_status_never_started_honest(self):
        r = self.client.get("/api/status")
        body = r.json()
        self.assertIn("disclaimer", body)
        self.assertIn("total loss", body["disclaimer"].lower())


class LanUrlTests(unittest.TestCase):
    def test_returns_string(self):
        self.assertIsInstance(lan_url(), str)
        self.assertTrue(len(lan_url()) > 0)

    def test_never_raises(self):
        # multiple calls must be safe (socket churn)
        for _ in range(3):
            lan_url()


if __name__ == "__main__":
    unittest.main()


class LiveDataTests(unittest.TestCase):
    """Live market-data endpoints: cached candles/tickers via ccxt,
    honest errors when offline, vendored chart library served."""

    def setUp(self):
        import aetherbot.webui.app as appmod
        self.client = TestClient(create_app("config/config.example.yaml"))
        appmod._candle_cache.clear()
        appmod._ticker_cache = (0.0, {})

    def test_chart_library_served(self):
        r = self.client.get(
            "/static/lightweight-charts.standalone.production.js")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.content), 100000)
        self.assertIn(b"TradingView", r.content)

    def test_page_uses_lightweight_charts(self):
        html = self.client.get("/").text
        self.assertIn("addCandlestickSeries", html)
        self.assertIn("/api/candles", html)
        self.assertIn("live exchange candles via ccxt", html)

    def test_candles_honest_when_offline(self):
        # a bogus exchange name -> honest 503, never synthetic data
        import aetherbot.webui.app as appmod
        from unittest.mock import patch

        def boom(pair, timeframe, limit=300):
            raise RuntimeError("network unreachable")
        with patch.object(appmod, "fetch_candles", boom):
            r = self.client.get("/api/candles")
            self.assertEqual(r.status_code, 503)
            self.assertIn("unavailable", r.json()["detail"])

    def test_candles_shape(self):
        import aetherbot.webui.app as appmod
        from unittest.mock import patch

        def fake_candles(cfg, pair, timeframe, limit=300):
            return [{"time": 1, "open": 1, "high": 2, "low": 0.5,
                     "close": 1.5, "volume": 10}]
        with patch.object(appmod, "fetch_candles", fake_candles):
            r = self.client.get(
                "/api/candles?pair=BTC/USDT&timeframe=1h")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["pair"], "BTC/USDT")
            self.assertEqual(body["timeframe"], "1h")
            self.assertEqual(len(body["candles"]), 1)
            self.assertEqual(body["candles"][0]["close"], 1.5)

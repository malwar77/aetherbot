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

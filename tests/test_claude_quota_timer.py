"""Timer quota Claude nel Command Bridge (badge top-center con countdown).

Quando Claude esaurisce la quota, il CLI spesso dichiara QUANDO la finestra si
resetta. La catena sotto test:

1. ``bridge_errors.quota_reset_epoch``: parser puro del testo d'errore
   (epoch "…|1723554000", "resets at 7pm", retry-after, ISO) → epoch o None.
2. ``lead_brain.record/get/clear_claude_quota``: stato persistito in
   ``tasks/claude-quota.json`` con autopulizia (finestra passata o TTL 5h
   quando il reset non e' noto).
3. Endpoint ``GET /api/bridge/prime/claude-quota`` registrato in routes.
4. Wiring UI in ``static/command_bridge.js`` (badge, tick 1s, poll, SSE hook).
"""
from __future__ import annotations

import pathlib
import sys
import time
import unittest
from datetime import datetime


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api import bridge_errors, lead_brain  # noqa: E402


NOW = 1_764_000_000.0  # epoch fisso per test deterministici


class TestQuotaResetEpochParser(unittest.TestCase):
    def test_epoch_after_pipe(self):
        ts = NOW + 3 * 3600
        got = bridge_errors.quota_reset_epoch(
            f"Claude AI usage limit reached|{int(ts)}", now=NOW
        )
        self.assertEqual(got, float(int(ts)))

    def test_epoch_milliseconds(self):
        ts_ms = int((NOW + 3600) * 1000)
        got = bridge_errors.quota_reset_epoch(f"usage limit|{ts_ms}", now=NOW)
        self.assertAlmostEqual(got, NOW + 3600, delta=1)

    def test_epoch_near_reset_word(self):
        ts = int(NOW + 7200)
        got = bridge_errors.quota_reset_epoch(f"429: quota resets at {ts}", now=NOW)
        self.assertEqual(got, float(ts))

    def test_clock_pm_local(self):
        # "resets at 7pm": oggi (o domani se gia' passate) alle 19:00 locali.
        got = bridge_errors.quota_reset_epoch(
            "You've hit your limit - resets at 7pm", now=NOW
        )
        self.assertIsNotNone(got)
        self.assertGreater(got, NOW)
        local = datetime.fromtimestamp(got)
        self.assertEqual((local.hour, local.minute), (19, 0))

    def test_clock_24h_with_minutes(self):
        got = bridge_errors.quota_reset_epoch(
            "usage limit reached, resets 15:30", now=NOW
        )
        self.assertIsNotNone(got)
        local = datetime.fromtimestamp(got)
        self.assertEqual((local.hour, local.minute), (15, 30))

    def test_retry_after_seconds(self):
        got = bridge_errors.quota_reset_epoch("429 retry-after: 5400", now=NOW)
        self.assertEqual(got, NOW + 5400)

    def test_iso_timestamp_utc(self):
        ts = NOW + 4 * 3600
        iso = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")
        got = bridge_errors.quota_reset_epoch(f"limit resets {iso}", now=NOW)
        self.assertAlmostEqual(got, ts, delta=1)

    def test_past_epoch_rejected(self):
        got = bridge_errors.quota_reset_epoch(f"reached|{int(NOW - 3600)}", now=NOW)
        self.assertIsNone(got)

    def test_far_future_epoch_rejected(self):
        got = bridge_errors.quota_reset_epoch(
            f"reached|{int(NOW + 30 * 86400)}", now=NOW
        )
        self.assertIsNone(got)

    def test_garbage_returns_none(self):
        for text in ("", None, "quota esaurita e basta", "429 rate limit"):
            self.assertIsNone(bridge_errors.quota_reset_epoch(text, now=NOW), text)


class TestClaudeQuotaState(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.ws = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_and_get_roundtrip(self):
        reset_at = time.time() + 3600
        lead_brain.record_claude_quota(self.ws, reason="429 usage limit", reset_at=reset_at)
        state = lead_brain.get_claude_quota_state(self.ws)
        self.assertTrue(state.get("exhausted"))
        self.assertEqual(state.get("reset_at"), reset_at)
        self.assertEqual(state.get("reason"), "429 usage limit")

    def test_expired_window_self_cleans(self):
        lead_brain.record_claude_quota(
            self.ws, reason="429", reset_at=time.time() - 600
        )
        self.assertEqual(lead_brain.get_claude_quota_state(self.ws), {})
        # autopulizia: il file non deve piu' esistere
        self.assertFalse(lead_brain._quota_state_path(self.ws).exists())

    def test_unknown_reset_expires_after_ttl(self):
        lead_brain.record_claude_quota(self.ws, reason="quota", reset_at=None)
        p = lead_brain._quota_state_path(self.ws)
        import json

        data = json.loads(p.read_text(encoding="utf-8"))
        data["detected_at"] = time.time() - (lead_brain._QUOTA_UNKNOWN_TTL_SECONDS + 60)
        p.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(lead_brain.get_claude_quota_state(self.ws), {})

    def test_unknown_reset_still_active_within_ttl(self):
        lead_brain.record_claude_quota(self.ws, reason="quota", reset_at=None)
        state = lead_brain.get_claude_quota_state(self.ws)
        self.assertTrue(state.get("exhausted"))
        self.assertIsNone(state.get("reset_at"))

    def test_clear_is_idempotent(self):
        lead_brain.clear_claude_quota(self.ws)  # nessun file: non deve alzare
        lead_brain.record_claude_quota(self.ws, reason="x", reset_at=None)
        lead_brain.clear_claude_quota(self.ws)
        self.assertEqual(lead_brain.get_claude_quota_state(self.ws), {})


class TestRoutesWiring(unittest.TestCase):
    def setUp(self):
        self.py = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")

    def test_endpoint_registered(self):
        self.assertIn('parsed.path == "/api/bridge/prime/claude-quota"', self.py)
        self.assertIn("def _handle_bridge_prime_claude_quota(", self.py)

    def test_quota_recorded_on_sse_error_branch(self):
        self.assertIn("bridge_errors.quota_reset_epoch(err_payload[\"detail\"])", self.py)
        self.assertIn('"quota_reset_at"', self.py)

    def test_quota_recorded_on_failover(self):
        self.assertIn(
            "reset_at=bridge_errors.quota_reset_epoch(ex.reason or \"\")", self.py
        )

    def test_quota_cleared_on_successful_claude_turn(self):
        self.assertIn("lead_brain.clear_claude_quota(workspace)", self.py)


class TestCommandBridgeUiWiring(unittest.TestCase):
    """La UI deve avere badge, countdown e i due canali (SSE + poll)."""

    def setUp(self):
        self.js = (REPO_ROOT / "static" / "command_bridge.js").read_text(encoding="utf-8")

    def test_badge_element_in_dom_scaffold(self):
        self.assertIn('id="cbQuotaTimer"', self.js)
        self.assertIn('id="cbQuotaTimerText"', self.js)

    def test_timer_helpers_present(self):
        for needle in (
            "function showQuotaTimer(",
            "function hideQuotaTimer(",
            "function pollClaudeQuota(",
            "function startClaudeQuotaPolling(",
        ):
            self.assertIn(needle, self.js, needle)

    def test_countdown_ticks_every_second(self):
        self.assertIn("_cbQtTick = setInterval(render, 1000)", self.js)

    def test_sse_error_hook(self):
        self.assertIn("d.branch === 'claude_quota'", self.js)
        self.assertIn("showQuotaTimer(d.quota_reset_at || null)", self.js)

    def test_poll_endpoint_and_boot(self):
        self.assertIn("api/bridge/prime/claude-quota", self.js)
        self.assertIn("startClaudeQuotaPolling();", self.js)

    def test_top_center_style(self):
        self.assertIn(".cb-quota-timer{position:absolute;top:10px;left:50%", self.js)


if __name__ == "__main__":
    unittest.main()

"""P0307: bounded host-side reload loop telemetry (no phone authority changes)."""
import _hermetic
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from agentj.serve import Host


class ReloadTelemetry(IsolatedAsyncioTestCase):
    async def test_fifth_load_logs_and_notices_once_per_device_per_ten_minutes(self):
        host = Host.__new__(Host)
        host.st = SimpleNamespace(log=Mock())
        host.local_notice = Mock()
        host.lang = "zh"
        host.reload_loads = {}
        host.reload_storm_at = {}
        with patch("agentj.serve.time.monotonic", return_value=100):
            for _ in range(4):
                Host._track_reload_load(host, "device-a", "load")
            host.st.log.assert_not_called()
            Host._track_reload_load(host, "device-a", "load")
            Host._track_reload_load(host, "device-a", "load")
            Host._track_reload_load(host, "device-b", "resume")
        host.st.log.assert_called_once_with("reload_storm", device="device-a", n=5, window=60)
        host.local_notice.assert_called_once()
        with patch("agentj.serve.time.monotonic", return_value=161):
            for _ in range(5):
                Host._track_reload_load(host, "device-a", "load")
        host.st.log.assert_called_once()
        with patch("agentj.serve.time.monotonic", return_value=701):
            for _ in range(5):
                Host._track_reload_load(host, "device-a", "load")
        self.assertEqual(host.st.log.call_count, 2)
        self.assertEqual(host.local_notice.call_count, 2)

    async def test_preview_attest_logs_only_authenticated_ready_device_and_boolean(self):
        from agentj import web_preview
        host = Host.__new__(Host)
        host.st = SimpleNamespace(log=Mock(), is_allowed=Mock(return_value=True))
        host.channel = "channel"
        host.send_app = AsyncMock()
        session = SimpleNamespace(cid=1, state="ready", device="device-a", pub=b"a" * 32)
        host.sessions = {1: session}
        obj = {"r": "1234567890abcdef", "message": "private-attestation-body"}
        with patch.object(web_preview, "attest", return_value="signature"):
            await Host.on_p33(host, session, "web_preview_attest", obj)
        host.st.log.assert_called_once_with("preview_attest", device="device-a", ok=True)
        host.st.log.reset_mock()
        session.state = "pending"
        await Host.on_p33(host, session, "web_preview_attest", obj)
        host.st.log.assert_not_called()
        session.state = "ready"
        with patch.object(web_preview, "attest", return_value=None):
            await Host.on_p33(host, session, "web_preview_attest", obj)
        host.st.log.assert_called_once_with("preview_attest", device="device-a", ok=False)

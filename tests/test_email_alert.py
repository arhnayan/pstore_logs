import asyncio
import unittest
from unittest import mock

from app import collector as collector_mod
from app.collector import Collector
from app.mock_client import MockPowerStoreClient


class FakeClient(MockPowerStoreClient):
    pass


class HostAlertEmailTests(unittest.TestCase):
    def make(self):
        c = Collector.__new__(Collector)
        c._client = FakeClient(cluster_ip="10.0.0.1")
        c.bus = mock.Mock(publish=mock.AsyncMock())
        c.host_health = []
        c.host_health_checked_at = None
        c._hosts_down_notified = set()
        c.host_alert_error = None
        return c

    def test_email_on_down_then_recovery_and_retry(self):
        import os
        c = self.make()
        sent = mock.AsyncMock()
        with mock.patch.object(collector_mod, "send_email", sent):
            with mock.patch.dict(os.environ, {"PSTORE_MOCK_DOWN": "mock-db-01"}):
                asyncio.run(c._poll_host_health())
                self.assertEqual(sent.await_count, 1)
                self.assertIn("mock-db-01", sent.await_args[0][1])
                asyncio.run(c._poll_host_health())  # still down: no repeat
                self.assertEqual(sent.await_count, 1)
            asyncio.run(c._poll_host_health())  # recovered
            self.assertEqual(sent.await_count, 2)
            self.assertIn("RECOVERED", sent.await_args[0][0])

        failing = mock.AsyncMock(side_effect=RuntimeError("smtp down"))
        c2 = self.make()
        with mock.patch.dict(os.environ, {"PSTORE_MOCK_DOWN": "mock-db-01"}):
            with mock.patch.object(collector_mod, "send_email", failing):
                asyncio.run(c2._poll_host_health())
                self.assertEqual(c2.host_alert_error, "smtp down")
                self.assertEqual(c2._hosts_down_notified, set())  # will retry
            with mock.patch.object(collector_mod, "send_email", sent):
                asyncio.run(c2._poll_host_health())
                self.assertIsNone(c2.host_alert_error)


if __name__ == "__main__":
    unittest.main()

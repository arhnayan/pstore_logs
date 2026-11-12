import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.host_health import DEGRADED, DOWN, NO_INITIATORS, UP, evaluate_hosts, problem_hosts
import host_monitor

SESSION = [{"port_name": "t1"}]


def init(host, port, sessions):
    return {"id": f"{host}{port}", "host_id": host, "port_name": port, "active_sessions": sessions}


class EvaluateTests(unittest.TestCase):
    def test_statuses(self):
        hosts = [{"id": "a", "name": "up"}, {"id": "b", "name": "down"}, {"id": "c", "name": "deg"}, {"id": "d", "name": "none"}]
        inits = [
            init("a", "1", SESSION), init("a", "2", SESSION),
            init("b", "1", []), init("b", "2", None),
            init("c", "1", SESSION), init("c", "2", []),
        ]
        got = {h.name: h for h in evaluate_hosts(hosts, inits)}
        self.assertEqual(got["up"].status, UP)
        self.assertEqual(got["down"].status, DOWN)
        self.assertEqual(got["deg"].status, DEGRADED)
        self.assertEqual(got["none"].status, NO_INITIATORS)
        self.assertIn("1/2", got["deg"].detail())

    def test_problem_filter_and_ignore(self):
        hosts = [{"id": "b", "name": "Down"}, {"id": "c", "name": "deg"}]
        res = evaluate_hosts(hosts, [init("b", "1", []), init("c", "1", SESSION), init("c", "2", [])])
        self.assertEqual([h.name for h in problem_hosts(res, include_degraded=False, ignore=set())], ["Down"])
        self.assertEqual(len(problem_hosts(res, include_degraded=True, ignore=set())), 2)
        self.assertEqual([h.name for h in problem_hosts(res, include_degraded=True, ignore={"down"})], ["deg"])


class CliTests(unittest.TestCase):
    def run_cli(self, state, env=None, extra=()):
        with mock.patch.dict(os.environ, env or {}, clear=False), mock.patch.object(host_monitor, "send_mail") as send:
            code = host_monitor.main(["--mock", "--state-file", str(state), "--mail-from", "a@b", "--mail-to", "c@d", "--smtp-host", "x", *extra])
        return code, send

    def test_alert_once_then_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "s.json"
            code, send = self.run_cli(state)
            self.assertEqual((code, send.call_count), (0, 0))

            env = {"PSTORE_MOCK_DOWN": "mock-db-01"}
            code, send = self.run_cli(state, env)
            self.assertEqual((code, send.call_count), (1, 1))
            self.assertIn("mock-db-01", send.call_args[0][1].get_content())

            code, send = self.run_cli(state, env)  # still down: no repeat mail
            self.assertEqual((code, send.call_count), (1, 0))

            code, send = self.run_cli(state)  # recovered
            self.assertEqual((code, send.call_count), (0, 1))
            self.assertIn("RECOVERED", send.call_args[0][1]["Subject"])

    def test_expect_hosts_and_ignore(self):
        with tempfile.TemporaryDirectory() as d:
            code, send = self.run_cli(Path(d) / "s.json", extra=["--expect-hosts", "8"])
            self.assertEqual((code, send.call_count), (1, 1))
            code, send = self.run_cli(Path(d) / "t.json", {"PSTORE_MOCK_DOWN": "mock-db-01"}, ["--ignore", "mock-db-01"])
            self.assertEqual((code, send.call_count), (0, 0))

    def test_failed_mail_is_retried(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "s.json"
            env = {"PSTORE_MOCK_DOWN": "mock-db-01"}
            with mock.patch.dict(os.environ, env), mock.patch.object(host_monitor, "send_mail", side_effect=OSError("smtp down")):
                host_monitor.main(["--mock", "--state-file", str(state), "--smtp-host", "x"])
            code, send = self.run_cli(state, env)
            self.assertEqual(send.call_count, 1)


if __name__ == "__main__":
    unittest.main()

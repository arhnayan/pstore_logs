import asyncio
import unittest

from app.client import PowerStoreError
from app.host_provisioning import classify, parse_hosts, provision


class ParseTests(unittest.TestCase):
    def test_comma_and_newline_lists(self):
        rows = parse_hosts("adntocesxrm36.vodafone.local, ADNTOCESXRM37.vodafone.local\nadntocesxrm38.vodafone.local;")
        self.assertEqual([r["name"] for r in rows], ["ADNTOCESXRM36", "ADNTOCESXRM37", "ADNTOCESXRM38"])
        self.assertEqual(rows[1]["initiator"], "nqn.2014-08.local.vodafone:nvme:adntocesxrm37")

    def test_bare_name_gets_domain_and_dupes_collapse(self):
        rows = parse_hosts("GSXIZMDC11,gsxizmdc11.vodafone.local,GSXIZMDC12")
        self.assertEqual([r["initiator"] for r in rows], ["nqn.2014-08.local.vodafone:nvme:gsxizmdc11", "nqn.2014-08.local.vodafone:nvme:gsxizmdc12"])

    def test_pasted_nqn_lines(self):
        rows = parse_hosts("Host NQN: nqn.2014-08.local.vodafone:nvme:adntocesxrm36\nHost NQN: nqn.2014-08.local.vodafone:nvme:adntocesxrm37\nadntocesxrm36.vodafone.local")
        self.assertEqual([r["name"] for r in rows], ["ADNTOCESXRM36", "ADNTOCESXRM37"])
        self.assertEqual(rows[0]["initiator"], "nqn.2014-08.local.vodafone:nvme:adntocesxrm36")

    def test_invalid(self):
        rows = parse_hosts("bad_name.vodafone.local,ok1.vodafone.local")
        self.assertEqual([r["status"] for r in rows], ["invalid", "new"])


class FakeClient:
    def __init__(self):
        self.created = []

    async def get_hosts(self):
        return [{"id": "h1", "name": "Existing1"}]

    async def get_initiators(self):
        return [{"port_name": "nqn.2014-08.local.vodafone:nvme:used"}]

    async def create_host(self, name, initiator):
        if name == "BOOM":
            raise PowerStoreError("nope")
        self.created.append((name, initiator))
        return "id-" + name


class FlowTests(unittest.TestCase):
    def test_classify_and_provision(self):
        rows = parse_hosts("existing1.x.local,used.vodafone.local,boom.x.local,fresh.x.local")
        client = FakeClient()
        asyncio.run(classify(client, rows))
        asyncio.run(provision(client, rows))
        self.assertEqual([r["status"] for r in rows], ["exists", "exists", "failed", "created"])
        self.assertEqual(client.created, [("FRESH", "nqn.2014-08.local.x:nvme:fresh")])
        self.assertEqual(rows[2]["detail"], "nope")


if __name__ == "__main__":
    unittest.main()


class DeleteClient:
    def __init__(self):
        self.deleted = []

    async def get_hosts(self):
        return [
            {"id": "h1", "name": "FREE1"},
            {"id": "h2", "name": "MAPPED1"},
            {"id": "h3", "name": "GROUPED1", "host_group_id": "g1"},
            {"id": "h4", "name": "OTHERNAME"},
            {"id": "h5", "name": "BOOM"},
        ]

    async def get_initiators(self):
        return [{"port_name": "nqn.2014-08.local.vodafone:nvme:viainit", "host_id": "h4"}]

    async def get_host_volume_mappings(self):
        return [{"host_id": "h2", "volume_id": "v1"}]

    async def delete_host(self, host_id):
        if host_id == "h5":
            raise PowerStoreError("nope")
        self.deleted.append(host_id)


class DeleteTests(unittest.TestCase):
    def test_only_safe_hosts_are_deleted(self):
        from app.host_provisioning import delete_hosts

        client = DeleteClient()
        rows = parse_hosts("free1,mapped1,grouped1,viainit,missing1,boom")
        asyncio.run(classify(client, rows))
        asyncio.run(delete_hosts(client, rows))
        self.assertEqual(client.deleted, ["h1"])
        self.assertEqual([r["status"] for r in rows], ["deleted", "skipped", "skipped", "skipped", "skipped", "failed"])
        self.assertIn("still mapped", rows[1]["detail"])
        self.assertIn("host group", rows[2]["detail"])
        self.assertIn("OTHERNAME", rows[3]["detail"])

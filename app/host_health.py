"""Host connectivity evaluation based on initiator login sessions.

PowerStore has no single "host is up" flag. The ``host_connectivity`` field on a
host is configuration (Local_Only / Metro_*), not live status. What the array does
expose is ``active_sessions`` on every initiator (GET /initiator, PowerStoreOS 3.0+):
the live logins between that initiator and a target port. A host whose initiators
all have zero sessions is not logged in to the array on any path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

UP = "UP"
DEGRADED = "DEGRADED"  # some initiators logged in, some not (lost path/HBA)
DOWN = "DOWN"  # initiators exist but none has a session
NO_INITIATORS = "NO_INITIATORS"  # host object has nothing to check

# A host with fewer total active sessions (summed over all its initiators) than
# this is considered impaired and raises an alert.
MIN_ACTIVE_SESSIONS = 8


@dataclass
class HostHealth:
    host_id: str
    name: str
    status: str
    initiators_total: int = 0
    initiators_logged_in: int = 0
    missing_ports: list[str] = field(default_factory=list)
    active_sessions: int = 0
    initiators: list[dict[str, Any]] = field(default_factory=list)
    by_node: dict[str, int] = field(default_factory=dict)

    @property
    def low_sessions(self) -> bool:
        return self.status != NO_INITIATORS and self.active_sessions < MIN_ACTIVE_SESSIONS

    def detail(self) -> str:
        if self.status == NO_INITIATORS:
            return "host has no initiators registered"
        text = f"{self.initiators_logged_in}/{self.initiators_total} initiators logged in"
        if self.missing_ports and self.status != UP:
            text += f" (no session: {', '.join(self.missing_ports)})"
        return text


def build_lookup(
    nodes: list[dict[str, Any]],
    appliances: list[dict[str, Any]],
    ports: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Friendly names for the ids that appear inside active_sessions."""
    app_names = {a["id"]: a.get("name") or a["id"] for a in appliances}
    node_names: dict[str, str] = {}
    for n in nodes:
        slot = n.get("slot")
        letter = "AB"[slot] if slot in (0, 1) else str(slot if slot is not None else "?")
        node_names[n["id"]] = f"{app_names.get(n.get('appliance_id'), n.get('appliance_id') or '?')} / Node {letter}"
    return {"nodes": node_names, "ports": {p["id"]: p.get("name") or p["id"] for p in ports}}


def _describe_session(sess: dict[str, Any], lookup: dict[str, dict[str, str]]) -> dict[str, Any]:
    node_id = sess.get("node_id")
    node = lookup["nodes"].get(node_id, node_id or "unknown node")
    port_id = next((sess.get(k) for k in ("fc_port_id", "eth_port_id", "veth_id", "bond_id") if sess.get(k)), None)
    return {
        "node": node,
        "port": lookup["ports"].get(port_id, port_id) if port_id else None,
        "target": sess.get("port_name"),
        "addresses": sess.get("nvme_transport_addresses") or None,
    }


def _count_by_node(own: list[dict[str, Any]], lookup: dict[str, dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in own:
        for sess in i.get("active_sessions") or []:
            node = _describe_session(sess or {}, lookup)["node"]
            counts[node] = counts.get(node, 0) + 1
    return dict(sorted(counts.items()))


def evaluate_hosts(
    hosts: list[dict[str, Any]],
    initiators: list[dict[str, Any]],
    lookup: dict[str, dict[str, str]] | None = None,
) -> list[HostHealth]:
    """Classify every host from the /host and /initiator listings."""
    lookup = lookup or {"nodes": {}, "ports": {}}
    by_host: dict[str, list[dict[str, Any]]] = {}
    for initiator in initiators:
        by_host.setdefault(initiator.get("host_id") or "", []).append(initiator)

    results: list[HostHealth] = []
    for host in hosts:
        host_id = host.get("id", "")
        own = by_host.get(host_id, [])
        up = [i for i in own if i.get("active_sessions")]
        missing = [i.get("port_name", "?") for i in own if not i.get("active_sessions")]
        if not own:
            status = NO_INITIATORS
        elif not up:
            status = DOWN
        elif missing:
            status = DEGRADED
        else:
            status = UP
        results.append(
            HostHealth(
                host_id=host_id,
                name=host.get("name") or host_id,
                status=status,
                initiators_total=len(own),
                initiators_logged_in=len(up),
                missing_ports=missing,
                initiators=[
                    {
                        "port_name": i.get("port_name", "?"),
                        "port_type": i.get("port_type"),
                        "sessions": [
                            _describe_session(sess or {}, lookup)
                            for sess in (i.get("active_sessions") or [])
                        ],
                    }
                    for i in own
                ],
                by_node=_count_by_node(own, lookup),
                active_sessions=sum(len(i.get("active_sessions") or []) for i in own),
            )
        )
    return sorted(results, key=lambda h: h.name.lower())


def problem_hosts(
    results: list[HostHealth],
    *,
    include_degraded: bool,
    ignore: set[str],
) -> list[HostHealth]:
    """Hosts that should raise an alert, honouring the maintenance ignore list."""
    bad = {DOWN, DEGRADED} if include_degraded else {DOWN}
    ignore_lc = {n.lower() for n in ignore}
    return [h for h in results if h.status in bad and h.name.lower() not in ignore_lc]

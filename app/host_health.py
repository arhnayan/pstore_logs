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


@dataclass
class HostHealth:
    host_id: str
    name: str
    status: str
    initiators_total: int = 0
    initiators_logged_in: int = 0
    missing_ports: list[str] = field(default_factory=list)

    def detail(self) -> str:
        if self.status == NO_INITIATORS:
            return "host has no initiators registered"
        text = f"{self.initiators_logged_in}/{self.initiators_total} initiators logged in"
        if self.missing_ports and self.status != UP:
            text += f" (no session: {', '.join(self.missing_ports)})"
        return text


def evaluate_hosts(
    hosts: list[dict[str, Any]],
    initiators: list[dict[str, Any]],
) -> list[HostHealth]:
    """Classify every host from the /host and /initiator listings."""
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

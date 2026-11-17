"""Mass host creation: turn a list of initiator FQDNs into PowerStore hosts.

Every host is created as ``os_type=ESXi`` with a single ``NVMe`` initiator. The
initiator port name is the host's NQN, built from the FQDN by reversing the domain:
``adntocesxrm36.vodafone.local`` -> host ``ADNTOCESXRM36`` and initiator
``nqn.2014-08.local.vodafone:nvme:adntocesxrm36``. A full NQN may be pasted instead
(a leading "Host NQN:" label is ignored); the host name is its last segment.
"""

from __future__ import annotations

import re
from typing import Any

from app.client import PowerStoreClient, PowerStoreError

OS_TYPE = "ESXi"
PORT_TYPE = "NVMe"
DEFAULT_DOMAIN = "vodafone.local"
MAX_HOSTS = 200

_SPLIT = re.compile(r"[,;\s]+")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_NQN = re.compile(r"^nqn\.\d{4}-\d{2}\.[a-z0-9.-]+:[a-z0-9._:-]+$")
_NQN_PREFIX = re.compile(r"host\s+nqn\s*:\s*", re.IGNORECASE)


def fqdn_to_nqn(fqdn: str) -> str:
    short, _, domain = fqdn.partition(".")
    return f"nqn.2014-08.{'.'.join(reversed(domain.split('.')))}:nvme:{short}"


def parse_hosts(text: str) -> list[dict[str, Any]]:
    """Parse comma/semicolon/newline separated entries into host rows.

    A bare name (no dot) gets ``.vodafone.local`` appended. Duplicates collapse to
    the first occurrence. Rows that cannot be used carry ``status="invalid"``.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _SPLIT.split(_NQN_PREFIX.sub("", text or "")):
        raw = raw.strip().strip("\"'")
        if not raw:
            continue
        value = raw.lower()
        if value.startswith("nqn."):
            nqn = value
            name = value.rsplit(":", 1)[-1].upper()
            valid = bool(_NQN.match(nqn))
        else:
            fqdn = value if "." in value else f"{value}.{DEFAULT_DOMAIN}"
            name = fqdn.split(".", 1)[0].upper()
            nqn = fqdn_to_nqn(fqdn)
            valid = all(_LABEL.match(label) for label in fqdn.split("."))
        if nqn in seen:
            continue
        seen.add(nqn)
        row: dict[str, Any] = {"input": raw, "name": name, "initiator": nqn, "status": "new", "detail": ""}
        if not valid:
            row.update(status="invalid", detail="Not a valid host name or NQN")
        rows.append(row)
    return rows


async def classify(client: PowerStoreClient, rows: list[dict[str, Any]]) -> None:
    """Mark rows that already exist on the array so they are skipped, not retried."""
    names = {(h.get("name") or "").lower(): h for h in await client.get_hosts()}
    in_use = {(i.get("port_name") or "").lower(): i for i in await client.get_initiators()}
    for row in rows:
        if row["status"] != "new":
            continue
        if row["name"].lower() in names:
            row.update(status="exists", detail="A host with this name already exists")
        elif row["initiator"] in in_use:
            row.update(status="exists", detail="This initiator is already registered to a host")


async def provision(client: PowerStoreClient, rows: list[dict[str, Any]]) -> None:
    """Create every ``new`` row one at a time; one failure never stops the rest."""
    for row in rows:
        if row["status"] != "new":
            continue
        try:
            row["id"] = await client.create_host(row["name"], row["initiator"])
            row.update(status="created", detail="")
        except PowerStoreError as exc:
            row.update(status="failed", detail=str(exc))
        except Exception as exc:  # network errors etc.
            row.update(status="failed", detail=f"{exc.__class__.__name__}: {exc}")

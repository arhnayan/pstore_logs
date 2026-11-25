"""Mass host creation: turn a list of initiator FQDNs into PowerStore hosts.

Every host is created as ``os_type=ESXi`` with a single ``NVMe`` initiator. The
initiator port name is the host's NQN, built from the FQDN by reversing the domain:
``adntocesxrm36.vodafone.local`` -> host ``ADNTOCESXRM36`` and initiator
``nqn.2014-08.local.vodafone:nvme:adntocesxrm36``. A full NQN may be pasted instead
(a leading "Host NQN:" label is ignored); the host name is its last segment.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.client import PowerStoreClient, PowerStoreError

OS_TYPE = "ESXi"
PORT_TYPE = "NVMe"
DEFAULT_DOMAIN = "vodafone.local"
MAX_HOSTS = 200
MAP_CONCURRENCY = 5  # parallel attach/detach calls; the REST API maps one host per call

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
            host = names[row["name"].lower()]
            row.update(status="exists", detail="A host with this name already exists",
                       host_id=host.get("id"), host_name=host.get("name"),
                       host_group_id=host.get("host_group_id"))
        elif row["initiator"] in in_use:
            owner = in_use[row["initiator"]].get("host_id")
            host = next((h for h in names.values() if h.get("id") == owner), {})
            row.update(status="exists", detail="This initiator is already registered to a host",
                       host_id=owner, host_name=host.get("name"), host_group_id=host.get("host_group_id"))


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


async def annotate_volume_groups(client: PowerStoreClient, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add ``volume_groups`` to every existing row and return the machine's volume groups.

    PowerStore has no "host is in volume group" field: attaching a group maps each of its
    volumes to the host. A host is therefore ``mapped`` to a group when all of the group's
    volumes are mapped to it (directly or through its host group) and ``partial`` when only
    some are, which is what a half-failed attach leaves behind.
    """
    groups = await client.get_volume_groups_with_volumes()
    mappings = await client.get_host_volume_mappings()
    by_host: dict[str, set[str]] = {}
    by_group: dict[str, set[str]] = {}
    for m in mappings:
        if m.get("host_id"):
            by_host.setdefault(m["host_id"], set()).add(m.get("volume_id"))
        if m.get("host_group_id"):
            by_group.setdefault(m["host_group_id"], set()).add(m.get("volume_id"))
    members = {g["id"]: {v.get("id") for v in (g.get("volumes") or [])} for g in groups}
    for row in rows:
        row["volume_groups"] = []
        if not row.get("host_id"):
            continue
        mapped = by_host.get(row["host_id"], set()) | by_group.get(row.get("host_group_id"), set())
        for g in groups:
            vols = members[g["id"]]
            hit = vols & mapped
            if hit:
                row["volume_groups"].append(
                    {"id": g["id"], "name": g.get("name"), "state": "mapped" if hit == vols else "partial"}
                )
    return [
        {"id": g["id"], "name": g.get("name"), "volume_count": g.get("volume_count", len(members[g["id"]]))}
        for g in sorted(groups, key=lambda g: (g.get("name") or "").lower())
    ]


async def apply_volume_group(
    client: PowerStoreClient, rows: list[dict[str, Any]], volume_group_id: str, action: str
) -> None:
    """Map or unmap one volume group for every selected row, a few hosts at a time.

    ``rows`` must already have gone through ``classify`` and ``annotate_volume_groups``.
    Each row ends as ``mapped``/``unmapped``, ``skipped`` (nothing to do) or ``failed``.
    """
    mapping = action == "map"
    done, noop = ("mapped", "already mapped") if mapping else ("unmapped", "not mapped to this group")
    gate = asyncio.Semaphore(MAP_CONCURRENCY)

    async def one(row: dict[str, Any]) -> None:
        if row["status"] != "exists":
            row.update(status="skipped", detail="Host does not exist on this machine"
                       if row["status"] == "new" else row["detail"])
            return
        if row.get("host_group_id"):
            row.update(status="skipped", detail="Host belongs to a host group; manage the mapping on the group")
            return
        current = next((g for g in row["volume_groups"] if g["id"] == volume_group_id), None)
        if (mapping and current and current["state"] == "mapped") or (not mapping and current is None):
            row.update(status="skipped", detail=noop)
            return
        async with gate:
            try:
                if mapping:
                    await client.attach_volume_group(volume_group_id, row["host_id"])
                else:
                    await client.detach_volume_group(volume_group_id, row["host_id"])
                row.update(status=done, detail="")
            except PowerStoreError as exc:
                row.update(status="failed", detail=str(exc))
            except Exception as exc:  # network errors etc.
                row.update(status="failed", detail=f"{exc.__class__.__name__}: {exc}")

    await asyncio.gather(*(one(r) for r in rows))


async def delete_hosts(client: PowerStoreClient, rows: list[dict[str, Any]], *, dry_run: bool = False) -> None:
    """Delete every listed row that is safe to delete, a few hosts at a time.

    ``rows`` must already have gone through ``classify``. A host is only deleted when
    its name matches the entry exactly (never a differently named owner of the same
    initiator), it is not in a host group and no volumes are mapped to it. Each row
    ends as ``deleted``, ``skipped`` or ``failed``; with ``dry_run`` nothing is deleted
    and deletable rows end as ``to_delete``.
    """
    mapped: dict[str, int] = {}
    for m in await client.get_host_volume_mappings():
        if m.get("host_id"):
            mapped[m["host_id"]] = mapped.get(m["host_id"], 0) + 1
    gate = asyncio.Semaphore(MAP_CONCURRENCY)

    async def one(row: dict[str, Any]) -> None:
        if row["status"] != "exists":
            row.update(status="skipped", detail="Host does not exist on this machine"
                       if row["status"] == "new" else row["detail"])
            return
        if (row.get("host_name") or "").lower() != row["name"].lower():
            row.update(status="skipped",
                       detail=f"Initiator belongs to host {row.get('host_name') or row.get('host_id')}; not deleted")
            return
        if row.get("host_group_id"):
            row.update(status="skipped", detail="Host belongs to a host group; remove it from the group first")
            return
        if mapped.get(row["host_id"]):
            row.update(status="skipped",
                       detail=f"{mapped[row['host_id']]} volume(s) still mapped; unmap them first")
            return
        if dry_run:
            row.update(status="to_delete", detail="")
            return
        async with gate:
            try:
                await client.delete_host(row["host_id"])
                row.update(status="deleted", detail="")
            except PowerStoreError as exc:
                row.update(status="failed", detail=str(exc))
            except Exception as exc:  # network errors etc.
                row.update(status="failed", detail=f"{exc.__class__.__name__}: {exc}")

    await asyncio.gather(*(one(r) for r in rows))

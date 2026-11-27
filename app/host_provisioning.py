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

import httpx

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


MAPPABLE_TYPES = ("Primary", "Clone")  # snapshots and proxies cannot be mapped to a host


async def annotate_mappings(client: PowerStoreClient, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Add the current mappings to every existing row and return what can be mapped.

    Each existing row gets ``volumes`` (the volumes mapped to it, directly or through its
    host group) and ``volume_groups``. PowerStore has no "host is in volume group" field:
    a group counts as ``mapped`` when all of its volumes are mapped to the host and
    ``partial`` when only some are.

    Returns ``{"volumes": [...], "volume_groups": [...]}``: the mappable volumes
    (Primary and Clone) and the volume groups that have at least one of them
    (snapshot sets left out), both sorted by name.
    """
    volumes = [v for v in await client.get_volumes() if v.get("type") in MAPPABLE_TYPES]
    vol_by_id = {v["id"]: v for v in volumes}
    try:
        groups = await client.get_volume_groups_with_volumes()
    except (PowerStoreError, httpx.HTTPStatusError):  # groups are optional; volumes still work
        groups = []
    groups = [g for g in groups if g.get("type") in (None, *MAPPABLE_TYPES)]
    members = {g["id"]: {v.get("id") for v in (g.get("volumes") or [])} & vol_by_id.keys() for g in groups}
    groups = [g for g in groups if members[g["id"]]]

    by_host: dict[str, set[str]] = {}
    by_group: dict[str, set[str]] = {}
    for m in await client.get_host_volume_mappings():
        if m.get("host_id"):
            by_host.setdefault(m["host_id"], set()).add(m.get("volume_id"))
        if m.get("host_group_id"):
            by_group.setdefault(m["host_group_id"], set()).add(m.get("volume_id"))

    for row in rows:
        row["volumes"], row["volume_groups"] = [], []
        if not row.get("host_id"):
            continue
        direct = by_host.get(row["host_id"], set())
        mapped = direct | by_group.get(row.get("host_group_id"), set())
        row["direct_volume_ids"] = sorted(direct)
        row["volumes"] = sorted(
            ({"id": vid, "name": vol_by_id[vid].get("name")} for vid in mapped if vid in vol_by_id),
            key=lambda v: (v["name"] or "").lower(),
        )
        for g in groups:
            vols = members[g["id"]]
            hit = vols & mapped
            if hit:
                row["volume_groups"].append(
                    {"id": g["id"], "name": g.get("name"), "state": "mapped" if hit == vols else "partial"}
                )

    name_key = lambda x: (x.get("name") or "").lower()  # noqa: E731
    return {
        "volumes": [
            {"id": v["id"], "name": v.get("name"), "size": v.get("size"), "type": v.get("type")}
            for v in sorted(volumes, key=name_key)
        ],
        "volume_groups": [
            {"id": g["id"], "name": g.get("name"), "volume_count": len(members[g["id"]]),
             "volume_ids": sorted(members[g["id"]])}
            for g in sorted(groups, key=name_key)
        ],
    }


def resolve_target(targets: dict[str, list[dict[str, Any]]], target: str) -> tuple[str, list[str]]:
    """Turn a dropdown value (``vol:<id>`` or ``vg:<id>``) into a label and its volume ids."""
    kind, _, ident = (target or "").partition(":")
    if kind == "vol":
        vol = next((v for v in targets["volumes"] if v["id"] == ident), None)
        if vol:
            return vol.get("name") or ident, [ident]
    elif kind == "vg":
        group = next((g for g in targets["volume_groups"] if g["id"] == ident), None)
        if group:
            return group.get("name") or ident, list(group["volume_ids"])
    raise ValueError("Unknown volume or volume group on this machine")


async def apply_mapping(
    client: PowerStoreClient, rows: list[dict[str, Any]], volume_ids: list[str], action: str
) -> None:
    """Map or unmap a set of volumes (one volume, or a group's members) for every row.

    ``rows`` must already have gone through ``classify`` and ``annotate_mappings``.
    Hosts run a few at a time; each host's volumes are attached one by one, since the
    REST API takes one host and one volume per call. Each row ends as
    ``mapped``/``unmapped``, ``skipped`` (nothing to do) or ``failed``.
    """
    mapping = action == "map"
    done = "mapped" if mapping else "unmapped"
    gate = asyncio.Semaphore(MAP_CONCURRENCY)

    async def one(row: dict[str, Any]) -> None:
        if row["status"] != "exists":
            row.update(status="skipped", detail="Host does not exist on this machine"
                       if row["status"] == "new" else row["detail"])
            return
        if row.get("host_group_id"):
            row.update(status="skipped", detail="Host belongs to a host group; manage the mapping on the group")
            return
        direct = set(row.get("direct_volume_ids") or [])
        todo = [v for v in volume_ids if (v not in direct) == mapping]
        if not todo:
            row.update(status="skipped", detail="already mapped" if mapping else "not mapped")
            return
        errors: list[str] = []
        async with gate:
            for vid in todo:
                try:
                    if mapping:
                        await client.attach_host_volume(row["host_id"], vid)
                    else:
                        await client.detach_host_volume(row["host_id"], vid)
                except PowerStoreError as exc:
                    errors.append(str(exc))
                except Exception as exc:  # network errors etc.
                    errors.append(f"{exc.__class__.__name__}: {exc}")
        ok = len(todo) - len(errors)
        if not errors:
            row.update(status=done, detail=f"{ok} volume(s)" if len(todo) > 1 else "")
        else:
            prefix = f"{ok}/{len(todo)} volume(s) {done}; " if ok else ""
            row.update(status="failed", detail=prefix + errors[0])

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

#!/usr/bin/env python3
"""
host_monitor.py - check that every host is logged in to a Dell PowerStore and
email an alert when one is not.

Run it from cron / Task Scheduler every few minutes. It is stateless between runs
except for a small JSON state file, which lets it mail once when a host goes down,
once when it recovers, and optionally remind while it stays down.

A host counts as DOWN when it has initiators registered but none has an active
login session (see app/host_health.py). Exit codes: 0 all fine, 1 problem hosts,
2 could not query the array.

Examples:
    export PSTORE_USER=admin PSTORE_PASSWORD='...'
    python3 host_monitor.py --cluster 10.0.0.5 --expect-hosts 8 --no-email
    python3 host_monitor.py --cluster 10.0.0.5 --expect-hosts 8 \\
        --smtp-host smtp.example.com --mail-from pstore@example.com \\
        --mail-to ops@example.com,storage@example.com
    PSTORE_MOCK_DOWN=mock-db-01 python3 host_monitor.py --mock --no-email
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import smtplib
import socket
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from app.client import PowerStoreClient
from app.host_health import HostHealth, evaluate_hosts, problem_hosts
from app.mock_client import MockPowerStoreClient

ARRAY_KEY = "array:UNREACHABLE"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Alert when a PowerStore host loses all its sessions.")
    p.add_argument("--cluster", default=os.environ.get("PSTORE_CLUSTER_IP", ""), help="management IP/FQDN")
    p.add_argument("--mock", action="store_true", help="use the built-in sample cluster (no network)")
    p.add_argument("--expect-hosts", type=int, default=0, help="alert if fewer hosts than this exist")
    p.add_argument("--ignore", default="", help="comma separated host names to skip (maintenance)")
    p.add_argument("--include-degraded", action="store_true", help="also alert when only some initiators are down")
    p.add_argument("--state-file", default="host_monitor_state.json")
    p.add_argument("--remind-hours", type=float, default=0, help="re-send while still failing (0 = never)")
    p.add_argument("--json", action="store_true", help="print results as JSON")
    p.add_argument("--no-email", action="store_true", help="print only, never send mail")

    m = p.add_argument_group("email (SMTP password from PSTORE_SMTP_PASSWORD)")
    m.add_argument("--smtp-host", default=os.environ.get("PSTORE_SMTP_HOST", ""))
    m.add_argument("--smtp-port", type=int, default=int(os.environ.get("PSTORE_SMTP_PORT", "25")))
    m.add_argument("--smtp-user", default=os.environ.get("PSTORE_SMTP_USER", ""))
    m.add_argument("--smtp-starttls", action="store_true")
    m.add_argument("--smtp-ssl", action="store_true", help="implicit TLS (usually port 465)")
    m.add_argument("--mail-from", default=os.environ.get("PSTORE_MAIL_FROM", ""))
    m.add_argument("--mail-to", default=os.environ.get("PSTORE_MAIL_TO", ""), help="comma separated")
    return p.parse_args(argv)


async def fetch(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if args.mock:
        client: PowerStoreClient = MockPowerStoreClient(cluster_ip=args.cluster or "mock")
    else:
        if not args.cluster:
            raise SystemExit("--cluster (or PSTORE_CLUSTER_IP) is required unless --mock")
        client = PowerStoreClient(cluster_ip=args.cluster)
    user = os.environ.get("PSTORE_USER", "")
    password = os.environ.get("PSTORE_PASSWORD", "")
    async with client:
        await client.login(user, password)
        return await client.get_hosts(), await client.get_initiators()


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"problems": [], "last_sent": 0}


def save_state(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def problem_key(h: HostHealth) -> str:
    return f"host:{h.name}:{h.status}"


def render_table(results: list[HostHealth]) -> str:
    if not results:
        return "(no hosts)"
    width = max(len(h.name) for h in results)
    return "\n".join(f"{h.name:<{width}}  {h.status:<13}  {h.detail()}" for h in results)


def build_message(
    args: argparse.Namespace,
    cluster: str,
    new: list[str],
    recovered: list[str],
    still: list[str],
    results: list[HostHealth],
    error: str | None,
) -> EmailMessage:
    if error:
        subject = f"[PowerStore {cluster}] cannot query array"
    elif new:
        subject = f"[PowerStore {cluster}] ALERT: {len(new)} host problem(s)"
    elif recovered and not still:
        subject = f"[PowerStore {cluster}] RECOVERED: all hosts connected"
    else:
        subject = f"[PowerStore {cluster}] host status update"

    lines: list[str] = []
    if error:
        lines += [f"Could not query the array: {error}", ""]
    if new:
        lines += ["NEW PROBLEMS:", *(f"  - {k}" for k in new), ""]
    if recovered:
        lines += ["RECOVERED:", *(f"  - {k}" for k in recovered), ""]
    if still and not new:
        lines += ["STILL FAILING:", *(f"  - {k}" for k in still), ""]
    if results:
        lines += ["Current host status:", render_table(results), ""]
    lines.append(f"Checked at {time.strftime('%Y-%m-%d %H:%M:%S')} from {socket.gethostname()}")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = args.mail_from
    msg["To"] = args.mail_to
    msg.set_content("\n".join(lines))
    return msg


def send_mail(args: argparse.Namespace, msg: EmailMessage) -> None:
    if not (args.smtp_host and args.mail_from and args.mail_to):
        raise RuntimeError("email needs --smtp-host, --mail-from and --mail-to (or use --no-email)")
    smtp_cls = smtplib.SMTP_SSL if args.smtp_ssl else smtplib.SMTP
    with smtp_cls(args.smtp_host, args.smtp_port, timeout=30) as smtp:
        if args.smtp_starttls and not args.smtp_ssl:
            smtp.starttls()
        if args.smtp_user:
            smtp.login(args.smtp_user, os.environ.get("PSTORE_SMTP_PASSWORD", ""))
        smtp.send_message(msg, to_addrs=[a.strip() for a in args.mail_to.split(",") if a.strip()])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cluster = "mock" if args.mock and not args.cluster else args.cluster
    ignore = {n.strip() for n in args.ignore.split(",") if n.strip()}
    state_path = Path(args.state_file)

    results: list[HostHealth] = []
    error: str | None = None
    problems: list[str] = []
    try:
        hosts, initiators = asyncio.run(fetch(args))
        results = evaluate_hosts(hosts, initiators)
        bad = problem_hosts(results, include_degraded=args.include_degraded, ignore=ignore)
        problems = [problem_key(h) for h in bad]
        counted = [h for h in results if h.name.lower() not in {n.lower() for n in ignore}]
        if args.expect_hosts and len(counted) < args.expect_hosts:
            problems.append(f"hosts:EXPECTED_{args.expect_hosts}_FOUND_{len(counted)}")
    except SystemExit:
        raise
    except Exception as exc:  # network, auth, unexpected API shape
        error = f"{type(exc).__name__}: {exc}"
        problems = [ARRAY_KEY]

    if args.json:
        print(json.dumps({"error": error, "hosts": [h.__dict__ for h in results], "problems": problems}, indent=2))
    else:
        print(render_table(results) if results else f"ERROR: {error}")
        if problems:
            print("\nPROBLEMS: " + ", ".join(problems))

    prev = load_state(state_path)
    prev_problems = set(prev.get("problems", []))
    cur = set(problems)
    new = sorted(cur - prev_problems)
    recovered = sorted(prev_problems - cur)
    remind = (
        args.remind_hours > 0
        and cur
        and time.time() - prev.get("last_sent", 0) >= args.remind_hours * 3600
    )

    last_sent = prev.get("last_sent", 0)
    if (new or recovered or remind) and not args.no_email:
        msg = build_message(args, cluster, new, recovered, sorted(cur), results, error)
        try:
            send_mail(args, msg)
            last_sent = time.time()
        except Exception as exc:
            # Do not record the new problems as "already reported" if mail failed.
            print(f"EMAIL FAILED: {exc}", file=sys.stderr)
            cur = prev_problems  # keep old state so the next run retries
    save_state(state_path, {"problems": sorted(cur), "last_sent": last_sent})

    if error:
        return 2
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
pstore_checks.py — Pull active alerts and recent events from a Dell PowerStore
cluster via the REST API and print them as clean tables.

Usage:
    python3 pstore_checks.py
    python3 pstore_checks.py --filter Critical
    python3 pstore_checks.py --type alerts
    python3 pstore_checks.py --type events
    python3 pstore_checks.py --type hardware
    python3 pstore_checks.py --json
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from rich.console import Console
from rich.table import Table

from app.client import SyncPowerStoreClient
from app.config import settings

console = Console()


def print_table(title: str, rows: list, columns: list) -> None:
    if not rows:
        console.print(f"[yellow]No {title.lower()} found.[/yellow]")
        return

    table = Table(title=title, show_lines=False)
    for col in columns:
        table.add_column(col)

    for row in rows:
        table.add_row(
            *[
                str(row.get(col.lower().replace(" ", "_"), row.get(col, "")))
                for col in columns
            ]
        )

    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(description="Query PowerStore cluster alerts/events")
    parser.add_argument(
        "--filter",
        choices=["Critical", "Major", "Minor", "Info"],
        help="Filter by severity",
    )
    parser.add_argument(
        "--type",
        choices=["alerts", "events", "hardware"],
        default="alerts",
        help="What to pull",
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of a table")
    args = parser.parse_args()

    console.print(f"[bold]Connecting to PowerStore cluster at {settings.cluster_ip}...[/bold]")
    username = input("Username: ")
    password = getpass.getpass("Password: ")

    try:
        with SyncPowerStoreClient(cluster_ip=settings.cluster_ip) as client:
            client.login(username, password)
            console.print("[bold green]Logged in successfully.[/bold green]\n")

            if args.type == "alerts":
                data = client.get_alerts(args.filter)
                title = "PowerStore Alerts" + (f" ({args.filter})" if args.filter else "")
                columns = [
                    "severity",
                    "state_l10n",
                    "description_l10n",
                    "raised_timestamp",
                    "resource_type",
                ]
            elif args.type == "events":
                data = client.get_events(args.filter)
                title = "PowerStore Events" + (f" ({args.filter})" if args.filter else "")
                columns = [
                    "severity",
                    "description_l10n",
                    "generated_timestamp",
                    "resource_type",
                ]
            else:
                data = client.get_hardware()
                title = "PowerStore Hardware"
                columns = ["type", "name", "lifecycle_state", "serial_number"]
    except Exception as e:
        console.print(f"[bold red]Failed:[/bold red] {e}")
        sys.exit(1)

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print_table(title, data, columns)


if __name__ == "__main__":
    main()

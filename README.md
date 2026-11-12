# PowerStore Local Monitor

Local web app for collecting and monitoring Dell PowerStore cluster alerts, events, hardware health, performance metrics, audit logs, and support log bundles.

## Requirements

- Python 3.11+
- Mac or Windows
- Network access to PowerStore cluster management IPs

## Setup

```bash
cd pstore_logs
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the dashboard

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 9090
uvicorn app.main:app --host 0.0.0.0 --port 9090
```

Open http://127.0.0.1:9090 in your browser.

### Mock data (no cluster)

Start with sample data and skip every PowerStore connection. Useful for UI and report checks without production credentials or a rebuilt exe pointed at the lab.

```bash
PSTORE_MOCK=1 uvicorn app.main:app --host 127.0.0.1 --port 9090
```

Standalone / Windows build:

```bash
python3 run_standalone.py --mock
.\pstore-monitor.exe --mock
```

The status bar shows **MOCK DATA**. Reports generate without saved credentials. Live monitoring still uses the selected location name, but the numbers come from the built-in sample cluster. Mock mode writes to `pstore-mock.db` and `reports-mock/`, so sample rows and Excel files stay separate from live data.

On first launch, go to **Settings**, pick a **live monitoring location** (Vodafone site), and save your PowerStore admin credentials. They are stored locally in the app SQLite database under your user data directory, not in the repo.

## CLI (legacy script)

The original CLI still works and uses the shared API client:

```bash
python3 pstore_checks.py
python3 pstore_checks.py --type events --filter Critical
python3 pstore_checks.py --type hardware --json
```

## Features

- **Live dashboard** with SSE updates (5s alerts, 20s cluster I/O, 60s capacity/inventory)
- **I/O Metrics** — streaming cluster IOPS/bandwidth/latency charts, per-node comparison, top volume/host I/O, drive wear
- **Capacity** — physical/logical space, efficiency, data reduction, block vs file breakdown
- **Storage inventory** — volumes, hosts, mappings (read-only)
- **NAS** — NAS servers and file systems (when present)
- **Alerts & events** with severity filtering and alert acknowledge
- **Hardware & ports** (FC link state, Ethernet status)
- **Audit trail** (requires Administrator role)
- **Log bundles** — trigger, poll, download to `~/Downloads`
- **macOS notifications** for new Critical alerts (deduplicated)
- **Pin volume** — enable fast 5s metrics for a single volume via the UI
- **Reports** — generate `All_Locations_Storage_Report.xlsx` (summary) or `All_TMPs.xlsx` (hourly TMP sheets) across all Vodafone PowerStore sites

## Reports

Go to **Reports** in the sidebar. Six Vodafone locations are pre-seeded with **per-server MGMT IPs** from `VODAFONE NETWORK IP.xlsx`. Save credentials in **Settings**, then click **Generate Summary Report** or **Generate Hourly TMP Report**. The app connects to each server's MGMT IP, pulls hourly host metrics, and writes the Excel workbook to your app data `reports/` folder.

- **Summary report** (`All_Locations_Storage_Report.xlsx`): one cover sheet, then for each location an executive sheet, a capacity and averaged-performance sheet, and one hourly sheet per server. Hourly sheets omit max-latency columns.
- **Hourly TMP report** (`All_TMPs.xlsx`): one sheet per server with hourly rows (Latency, I/O size, IOPS, CPU) matching the legacy `All_TMPs.xlsx` layout.

## Data storage

SQLite database: platform-specific app data folder (see `app/paths.py`)

## Standalone Windows build (GitHub Actions)

You can build a Windows `.exe` from macOS by pushing to GitHub and letting Actions run on a Windows runner. PyInstaller cannot cross-compile Windows binaries from macOS.

### One-time setup

1. Create a GitHub repo and push this project.
2. In the repo on GitHub, open **Actions** and enable workflows if prompted.

### Build manually

1. Open the repo on GitHub → **Actions** → **Build Windows**.
2. Click **Run workflow** → **Run workflow**.
3. When it finishes, open the run and download the **pstore-monitor-windows** artifact (folder + zip).

### Build on release tag

```bash
git tag v1.0.0
git push origin v1.0.0
```

The workflow uploads `pstore-monitor-windows.zip` to GitHub Releases automatically.

### Run on Windows

1. Unzip the artifact.
2. Double-click **`run.bat`** (recommended) or `pstore-monitor.exe`.
3. Browser opens to http://127.0.0.1:9090.
4. Pick a monitoring location and enter PowerStore credentials under **Settings**.

Optional: override the selected location IP with `PSTORE_CLUSTER_IP` before starting the app.

If it fails instantly, open **`run.bat`** instead of the exe — it keeps the window open. Errors are also logged to:

```
%LOCALAPPDATA%\pstore-monitor\startup.log
```

Data is stored in `%LOCALAPPDATA%\pstore-monitor\`. Credentials go to Windows Credential Manager.

**Change port** (if 9090 is in use):

```powershell
.\pstore-monitor.exe --port 9090
.\pstore-monitor.exe --mock
```

Or set an environment variable before starting:

```powershell
set PSTORE_PORT=9090
.\pstore-monitor.exe
```

Optional: override the monitoring location IP with `PSTORE_CLUSTER_IP` before starting the app.

### Local Windows build (optional)

On a Windows machine with Python 3.11+:

```powershell
pip install -r requirements.txt pyinstaller
pyinstaller pstore-monitor.spec --noconfirm
```

Output: `dist/pstore-monitor/`


## Architecture

Single Python process: FastAPI serves the UI and REST API; a background collector polls the array on configurable intervals and pushes updates to the browser via Server-Sent Events.

## Host connectivity monitor (email alerts)

`host_monitor.py` checks that every PowerStore host is still logged in and emails when one is not. PowerStore has no "host up" flag (`host_connectivity` is config, not status), so it reads `GET /initiator` (PowerStoreOS 3.0+) and treats a host whose initiators all have empty `active_sessions` as DOWN.

```bash
export PSTORE_USER=admin PSTORE_PASSWORD='...'
python3 host_monitor.py --cluster 10.0.0.5 --expect-hosts 8 --no-email        # dry run
python3 host_monitor.py --cluster 10.0.0.5 --expect-hosts 8 \
  --smtp-host smtp.example.com --mail-from pstore@example.com --mail-to ops@example.com
PSTORE_MOCK_DOWN=mock-db-01 python3 host_monitor.py --mock --no-email         # simulate an outage
```

Run it from cron or Task Scheduler every 5 minutes. It mails once when a host goes down, once on recovery, and `--remind-hours N` re-sends while it stays down. Other flags: `--include-degraded` (some initiators lost), `--ignore host1,host2` (maintenance), `--smtp-starttls` / `--smtp-ssl`, SMTP password via `PSTORE_SMTP_PASSWORD`. Exit code 0 = fine, 1 = problem hosts, 2 = array unreachable, so any existing monitoring tool can use it without email. Tests: `python3 -m unittest discover -s tests -t .`

# PowerStore Local Monitor

Local web app for collecting and monitoring Dell PowerStore cluster alerts, events, hardware health, performance metrics, audit logs, and support log bundles.

## Requirements

- Python 3.11+
- Mac (credentials stored in Keychain)
- Network access to the PowerStore cluster management IP (`192.168.1.40`)

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

On first launch, go to **Settings** and save your PowerStore admin credentials. They are stored in the macOS Keychain, not in the repo.

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
2. Double-click `pstore-monitor.exe` (or run from a terminal).
3. Browser opens to http://127.0.0.1:9090.
4. Enter PowerStore credentials under **Settings**.

Data is stored in `%LOCALAPPDATA%\pstore-monitor\`. Credentials go to Windows Credential Manager.

**Change port** (if 9090 is in use):

```powershell
.\pstore-monitor.exe --port 9090
```

Or set an environment variable before starting:

```powershell
set PSTORE_PORT=9090
.\pstore-monitor.exe
```

To change the cluster IP, set `PSTORE_CLUSTER_IP` the same way, or edit `app/config.py` before rebuilding.

### Local Windows build (optional)

On a Windows machine with Python 3.11+:

```powershell
pip install -r requirements.txt pyinstaller
pyinstaller pstore-monitor.spec --noconfirm
```

Output: `dist/pstore-monitor/`


## Architecture

Single Python process: FastAPI serves the UI and REST API; a background collector polls the array on configurable intervals and pushes updates to the browser via Server-Sent Events.

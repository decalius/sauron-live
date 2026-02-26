#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""


Parallel-pings all stores from the input CSV and produces machine-readable outputs
for dashboards.

Behavior:
- Initial pass: 1 ping per host (parallel)
- Retry pass:   N pings (default 3) for initial failures (parallel)
- Optional gateway check (uses "Gateway" column; falls back to .1 if blank)
  - summary_<run_id>.json
  - failures_<run_id>.json
  - optional failures_<run_id>.csv
  - optional <run_id>_ping_report_v2.txt
- Also updates "latest" files in the base logs folder for convenience.

NEW (Live Map Feed):
- Writes a full “all stores” status feed:
  - map_status_<run_id>.csv/json (per-run)
  - map_status_latest.csv/json (latest)
  - map_status_latest.geojson (published)
- Colors can be driven off the `status` field:
    green  = server_up
    yellow = server_down + gateway_up (requires --gateway-check)
    red    = server_down + gateway_down (or unknown)

Usage examples:
    python sauron.py
    python sauron.py C:\path\stores.csv --gateway-check --write-txt --write-csv
    python sauron.py --output-dir C:\path\logs --run-id latest --zip-run

Live map publishing (recommended when serving cesium_map.html):
    python sauron.py .\stores.csv --gateway-check --output-dir .\logs --publish-dir .
"""

import argparse
import csv
import os
import re
import sys
import ipaddress
import subprocess
import json
import sqlite3
import shutil
import urllib.request
import urllib.error
import time
from typing import Optional, List, Tuple, Dict
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

# =======================
# Configuration Defaults (edit these for your environment)
# =======================
# NOTE: These are only defaults. CLI flags always win.
# Default to the folder containing this script (i.e., the project folder when run from the repo).
# Can be overridden via environment variable: SAURON_BASE_DIR
BASE_DIR = Path(
    os.environ.get(
        "SAURON_BASE_DIR",
        str(Path(__file__).resolve().parent),
    )
).expanduser()

STORES_CSV_DEFAULT = BASE_DIR / "stores.csv"
DC_CSV_DEFAULT = BASE_DIR / "DC_LIST.csv"

# Logs (per-run folders created under here)
OUTPUT_DIR_DEFAULT = BASE_DIR / "logs"

# Where to publish the live feed file that the map reads (map_status_latest.geojson).
# If you're serving the map out of BASE_DIR, leave this as BASE_DIR.
PUBLISH_DIR_DEFAULT = BASE_DIR

# Backwards-compatible names used throughout the script
DEFAULT_CSV = str(STORES_CSV_DEFAULT)
DC_CSV = str(DC_CSV_DEFAULT)
DEFAULT_OUTPUTDIR = OUTPUT_DIR_DEFAULT
DEFAULT_PUBLISH_DIR = PUBLISH_DIR_DEFAULT

PING_TIMEOUT_MS = 1000
MAX_WORKERS = max(8, min(32, (os.cpu_count() or 4) * 2))
PROGRESS_EVERY = 250
GW_PROGRESS_EVERY = 200
RETRY_PINGS = 5
FINAL_CONFIRM_PINGS = 5

# =======================
_leading_digits = re.compile(r"^(\d+)")

def is_windows() -> bool:
    return os.name == "nt"

def _render_progress_bar(
    done: int,
    total: int,
    *,
    label: str = "progress",
    width: int = 40,
    final: bool = False,
    quiet: bool = False,
) -> None:
    """Render an in-place (single-line) progress bar.

    Uses carriage-return so the line updates instead of printing a growing list.
    """
    if quiet:
        return
    if total <= 0:
        return

    done = max(0, min(done, total))
    ratio = done / total
    filled = int(round(width * ratio))
    filled = max(0, min(filled, width))
    bar = "=" * filled + "-" * (width - filled)
    pct = int(ratio * 100)

    # Pad with spaces to clear remnants from a previous longer line.
    line = f"\r  {label}: [{bar}] {pct:3d}% ({done}/{total})"
    if final:
        sys.stdout.write(line + "\n")
    else:
        sys.stdout.write(line + " " * 8)
    sys.stdout.flush()

def ping_host(ip: str, count: int, timeout_ms: int) -> bool:
    """
    Returns True if the OS ping command returns success.
    Uses return code rather than parsing output text.
    """
    if not ip:
        return False
    if is_windows():
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), ip]
    else:
        secs = max(1, int((timeout_ms + 999) // 1000))
        cmd = ["ping", "-c", str(count), "-W", str(secs), ip]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(5, int((timeout_ms * count) / 1000) + 5)
        )
        return result.returncode == 0
    except Exception:
        return False

def derive_gateway_ip(server_ip: str) -> str:
    """
    Fallback gateway: same subnet, last octet = 1
    """
    try:
        ip_obj = ipaddress.ip_address(server_ip)
        if ip_obj.version != 4:
            return ""
        parts = server_ip.split(".")
        if len(parts) != 4:
            return ""
        parts[-1] = "1"
        return ".".join(parts)
    except Exception:
        return ""

def first4_digits(store: str) -> str:
    """
    Uses leading digits of StoreNumber. Returns first 4 digits.
    """
    if not store:
        return ""
    m = _leading_digits.match(store.strip())
    if not m:
        return ""
    digits = m.group(1)
    return digits[:4] if len(digits) >= 4 else digits

def store_sort_key(store: str) -> Tuple[int, str]:
    """
    Numeric sort by leading digits, fallback to string.
    """
    m = _leading_digits.match(store or "")
    if m:
        return (int(m.group(1)), store)
    return (10**12, store or "")

def load_rows(csv_path: Path):
    """
    Reads StoreNumber, IPAddress, optional Gateway, and optional location columns.

    Required headers:
      - StoreNumber
      - IPAddress

    Optional headers:
      - Gateway
      - Address, City, State, ZIP
      - Latitude, Longitude
    """
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print(f"ERROR: CSV appears to have no headers: {csv_path}", file=sys.stderr)
            sys.exit(1)

        norm = {h.strip().lower().replace(" ", ""): h for h in reader.fieldnames}

        sn = norm.get("storenumber") or norm.get("store") or norm.get("storeno") or norm.get("storenbr")
        ip = norm.get("ipaddress") or norm.get("ip") or norm.get("ipaddr")
        gw = norm.get("gateway") or norm.get("gw") or norm.get("gatewayip")

        # Optional location/metadata fields (used for live map + richer dashboards)
        addr_h = norm.get("address")
        city_h = norm.get("city")
        state_h = norm.get("state")
        zip_h = norm.get("zip") or norm.get("zipcode") or norm.get("postalcode")
        lat_h = norm.get("latitude") or norm.get("lat")
        lon_h = norm.get("longitude") or norm.get("long") or norm.get("lng") or norm.get("lon")

        if not sn or not ip:
            print("ERROR: CSV must include headers for StoreNumber and IPAddress.", file=sys.stderr)
            sys.exit(1)

        def _to_float(v: str):
            try:
                return float(v)
            except Exception:
                return None

        for row in reader:
            store = (row.get(sn) or "").strip()
            ipaddr = (row.get(ip) or "").strip()
            gateway = (row.get(gw) or "").strip() if gw else ""

            rec = {
                "StoreNumber": store,
                "IPAddress": ipaddr,
                "Gateway": gateway,
                "Address": (row.get(addr_h) or "").strip() if addr_h else "",
                "City": (row.get(city_h) or "").strip() if city_h else "",
                "State": (row.get(state_h) or "").strip() if state_h else "",
                "ZIP": (row.get(zip_h) or "").strip() if zip_h else "",
                "Latitude": _to_float((row.get(lat_h) or "").strip()) if lat_h else None,
                "Longitude": _to_float((row.get(lon_h) or "").strip()) if lon_h else None,
            }
            if store and ipaddr:
                yield rec

def load_dc_map(dc_csv_path: Path) -> Dict[str, str]:
    """
    Reads DC_LIST.csv and returns a DC code -> City/Name map.
    """
    if not dc_csv_path.exists():
        print(f"WARNING: DC file not found: {dc_csv_path}. DC names will show as 'Unknown DC <code>'.")
        return {}

    with dc_csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print(f"WARNING: DC CSV has no headers: {dc_csv_path}.", file=sys.stderr)
            return {}

        norm = {h.strip().lower().replace(" ", ""): h for h in reader.fieldnames}
        city_h = norm.get("city")
        dc_h = norm.get("dc")
        if not dc_h:
            print("WARNING: DC CSV missing 'DC' header. Continuing without names.", file=sys.stderr)
            return {}

        dc_to_city = {}
        for row in reader:
            dc_code = (row.get(dc_h) or "").strip()
            city = (row.get(city_h) or "").strip() if city_h else ""
            if dc_code:
                dc_to_city[dc_code] = city or f"DC {dc_code}"
        return dc_to_city

def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp_path, path)

def init_sqlite_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                input_csv TEXT,
                total_stores INTEGER,
                initial_responding INTEGER,
                initial_timeouts INTEGER,
                recovered_after_retry INTEGER,
                recovered_after_final_confirm INTEGER,
                final_timeouts INTEGER,
                gateway_check_enabled INTEGER,
                gateway_online_count INTEGER,
                gateway_offline_count INTEGER,
                scan_duration_seconds REAL,
                new_offline_count INTEGER,
                back_online_count INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS store_status_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                store TEXT,
                dc_code TEXT,
                dc_name TEXT,
                server_ip TEXT,
                gateway_ip TEXT,
                server_up INTEGER,
                gateway_up INTEGER,
                status TEXT,
                status_code INTEGER,
                latitude REAL,
                longitude REAL,
                address TEXT,
                city TEXT,
                state TEXT,
                zip TEXT,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                item_count INTEGER,
                sent INTEGER,
                delivery_ok INTEGER,
                delivery_detail TEXT,
                message TEXT,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );

            CREATE INDEX IF NOT EXISTS idx_store_status_events_run_id
                ON store_status_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_store_status_events_store
                ON store_status_events(store);
            CREATE INDEX IF NOT EXISTS idx_alerts_run_id
                ON alerts(run_id);
            """
        )

def write_run_to_sqlite(db_path: Path, summary: Dict, map_status_rows: List[Dict], alert_rows: List[Dict]) -> None:
    init_sqlite_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id, timestamp, input_csv, total_stores, initial_responding, initial_timeouts,
                recovered_after_retry, recovered_after_final_confirm, final_timeouts,
                gateway_check_enabled, gateway_online_count, gateway_offline_count,
                scan_duration_seconds, new_offline_count, back_online_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.get("run_id", ""),
                summary.get("timestamp", ""),
                summary.get("input_csv", ""),
                int(summary.get("total_stores", 0) or 0),
                int(summary.get("initial_responding", 0) or 0),
                int(summary.get("initial_timeouts", 0) or 0),
                int(summary.get("recovered_after_retry", 0) or 0),
                int(summary.get("recovered_after_final_confirm", 0) or 0),
                int(summary.get("final_timeouts", 0) or 0),
                1 if bool(summary.get("gateway_check_enabled", False)) else 0,
                int(summary.get("gateway_online_count", 0) or 0),
                int(summary.get("gateway_offline_count", 0) or 0),
                float(summary.get("scan_duration_seconds", 0.0) or 0.0),
                int(summary.get("new_offline_count", 0) or 0),
                int(summary.get("back_online_count", 0) or 0),
            ),
        )

        conn.execute("DELETE FROM store_status_events WHERE run_id = ?", (summary.get("run_id", ""),))
        if map_status_rows:
            conn.executemany(
                """
                INSERT INTO store_status_events (
                    run_id, timestamp, store, dc_code, dc_name, server_ip, gateway_ip,
                    server_up, gateway_up, status, status_code, latitude, longitude,
                    address, city, state, zip
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row.get("run_id", ""),
                        row.get("timestamp", ""),
                        row.get("store", ""),
                        row.get("dc_code", ""),
                        row.get("dc_name", ""),
                        row.get("server_ip", ""),
                        row.get("gateway_ip", ""),
                        1 if _to_bool(row.get("server_up", False)) else 0,
                        None if str(row.get("gateway_up", "")).strip() == "" else (1 if _to_bool(row.get("gateway_up", False)) else 0),
                        row.get("status", ""),
                        int(row.get("status_code", 0) or 0),
                        row.get("Latitude", None),
                        row.get("Longitude", None),
                        row.get("Address", ""),
                        row.get("City", ""),
                        row.get("State", ""),
                        row.get("ZIP", ""),
                    )
                    for row in map_status_rows
                ],
            )

        conn.execute("DELETE FROM alerts WHERE run_id = ?", (summary.get("run_id", ""),))
        if alert_rows:
            conn.executemany(
                """
                INSERT INTO alerts (
                    run_id, timestamp, alert_type, item_count, sent, delivery_ok, delivery_detail, message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        a.get("run_id", ""),
                        a.get("timestamp", ""),
                        a.get("alert_type", ""),
                        int(a.get("item_count", 0) or 0),
                        1 if bool(a.get("sent", False)) else 0,
                        None if a.get("delivery_ok") is None else (1 if bool(a.get("delivery_ok", False)) else 0),
                        a.get("delivery_detail", ""),
                        a.get("message", ""),
                    )
                    for a in alert_rows
                ],
            )
        conn.commit()

def zip_run_folder(run_dir: Path) -> Path:
    """
    Zips the run_dir and returns the created zip path.
    """
    zip_base = str(run_dir)
    zip_path = shutil.make_archive(zip_base, "zip", root_dir=str(run_dir))
    return Path(zip_path)

def parallel_ping(
    targets: List[Tuple[str, str]],
    count: int,
    timeout_ms: int,
    max_workers: int,
    progress_every: int = 0,
    quiet: bool = False
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """
    Parallel pings. Returns (successes, failures), each item is (store, ip).
    """
    successes, failures = [], []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(ping_host, ip, count, timeout_ms): (store, ip) for store, ip in targets}
        done = 0
        total = len(targets)
        if (not quiet) and progress_every and total:
            _render_progress_bar(0, total, label="pings", quiet=quiet)
        for fut in as_completed(futs):
            store, ip = futs[fut]
            ok = False
            try:
                ok = bool(fut.result())
            except Exception:
                ok = False

            if ok:
                successes.append((store, ip))
            else:
                failures.append((store, ip))

            done += 1
            if (not quiet) and progress_every and (done % progress_every == 0 or done == total):
                _render_progress_bar(done, total, label="pings", final=(done == total), quiet=quiet)

    successes.sort(key=lambda x: store_sort_key(x[0]))
    failures.sort(key=lambda x: store_sort_key(x[0]))
    return successes, failures

def check_gateways_for_failures(
    failures: List[Tuple[str, str]],
    store_to_gateway: Dict[str, str],
    timeout_ms: int,
    max_workers: int,
    count: int,
    progress_every: int = 0,
) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str]]]:
    """
    For each failed store: ping its gateway ip. Returns (gw_online, gw_offline),
    where each item is (store, server_ip, gateway_ip)
    """
    gw_targets = []
    for store, srv_ip in failures:
        gw_ip = store_to_gateway.get(store, "") or derive_gateway_ip(srv_ip)
        if gw_ip:
            gw_targets.append((store, srv_ip, gw_ip))

    gw_online, gw_offline = [], []
    if not gw_targets:
        return gw_online, gw_offline

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(ping_host, gw_ip, count, timeout_ms): (store, srv_ip, gw_ip)
                for store, srv_ip, gw_ip in gw_targets}
        done = 0
        total = len(gw_targets)
        if progress_every and total:
            _render_progress_bar(0, total, label="gateways", quiet=False)
        for fut in as_completed(futs):
            store, srv_ip, gw_ip = futs[fut]
            ok = False
            try:
                ok = bool(fut.result())
            except Exception:
                ok = False

            if ok:
                gw_online.append((store, srv_ip, gw_ip))
            else:
                gw_offline.append((store, srv_ip, gw_ip))

            done += 1
            if progress_every and (done % progress_every == 0 or done == total):
                _render_progress_bar(done, total, label="gateways", final=(done == total), quiet=False)

    gw_online.sort(key=lambda x: store_sort_key(x[0]))
    gw_offline.sort(key=lambda x: store_sort_key(x[0]))
    return gw_online, gw_offline

def group_failures_by_dc(
    failures: List[Tuple[str, str]],
    dc_map: Dict[str, str]
) -> Dict[str, List[Tuple[str, str]]]:
    """
    Returns dict: dc_name -> list of (store, ip)
    """
    grouped = defaultdict(list)
    for store, ip in failures:
        dc_code = first4_digits(store)
        dc_name = dc_map.get(dc_code, f"Unknown DC {dc_code}")
        grouped[dc_name].append((store, ip))
    for dc_name in grouped:
        grouped[dc_name].sort(key=lambda x: store_sort_key(x[0]))
    return dict(sorted(grouped.items(), key=lambda kv: kv[0].lower()))

def write_failures_csv(path: Path, rows: List[Dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    if not rows:
        headers = ["timestamp", "run_id", "store", "dc_code", "dc_name",
                   "server_ip", "stage_failed", "gateway_ip", "gateway_up"]
        with tmp_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
        os.replace(tmp_path, path)
        return
    headers = list(rows[0].keys())
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp_path, path)

def write_map_status_csv(path: Path, rows: List[Dict]):
    """Writes full store status rows for map consumption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    if not rows:
        headers = ["timestamp","run_id","store","dc_code","dc_name","server_ip","gateway_ip","server_up","gateway_up","status","Latitude","Longitude","Address","City","State","ZIP"]
        with tmp_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
        os.replace(tmp_path, path)
        return
    headers = list(rows[0].keys())
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp_path, path)

def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False

def load_latest_map_status(path: Path) -> Dict[Tuple[str, str], Dict]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return {}
        out: Dict[Tuple[str, str], Dict] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            store = str(row.get("store", "")).strip()
            ip = str(row.get("server_ip", "")).strip()
            if store and ip:
                out[(store, ip)] = row
        return out
    except Exception:
        return {}

def build_offline_alert_message(new_offlines: List[Dict], run_id: str, run_ts: str, max_items: int) -> str:
    lines = [
        f"New offline stores detected: {len(new_offlines)}",
        f"Run: {run_id} @ {run_ts}",
    ]
    for row in new_offlines[:max_items]:
        store = row.get("store", "")
        dc_name = row.get("dc_name", "")
        server_ip = row.get("server_ip", "")
        status = row.get("status", "")
        lines.append(f"- {store} ({dc_name}) {server_ip} status={status}")
    remaining = len(new_offlines) - max_items
    if remaining > 0:
        lines.append(f"...and {remaining} more")
    return "\n".join(lines)

def build_back_online_alert_message(back_online: List[Dict], run_id: str, run_ts: str, max_items: int) -> str:
    lines = [
        f"Stores back online: {len(back_online)}",
        f"Run: {run_id} @ {run_ts}",
    ]
    for row in back_online[:max_items]:
        store = row.get("store", "")
        dc_name = row.get("dc_name", "")
        server_ip = row.get("server_ip", "")
        last_seen_offline_at = row.get("last_seen_offline_at", "unknown")
        lines.append(
            f"- Store back online - Last seen offline at {last_seen_offline_at}: "
            f"{store} ({dc_name}) {server_ip}"
        )
    remaining = len(back_online) - max_items
    if remaining > 0:
        lines.append(f"...and {remaining} more")
    return "\n".join(lines)

def send_teams_webhook(webhook_url: str, message: str) -> Tuple[bool, str]:
    payload = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        return True, body
    except urllib.error.HTTPError as ex:
        try:
            body = ex.read().decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        return False, f"HTTP {ex.code} {body}".strip()
    except Exception as ex:
        return False, str(ex)

def write_txt_report(
    path: Path,
    csv_path: Path,
    total: int,
    initial_success: int,
    initial_failures: List[Tuple[str, str]],
    recovered_count: int,
    confirm_recovered_count: int,
    final_failures: List[Tuple[str, str]],
    dc_map: Dict[str, str],
    gw_online: List[Tuple[str, str, str]],
    gw_offline: List[Tuple[str, str, str]],
    run_ts: str,
):
    """
    Writes a human-friendly text report (legacy format) to path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fw:
        fw.write("===== Initial Ping Summary =====\n")
        fw.write(f"Timestamp: {run_ts}\n")
        fw.write(f"Input CSV: {csv_path}\n")
        fw.write(f"Total Stores: {total}\n")
        fw.write(f"Initial Responding: {initial_success}\n")
        fw.write(f"Initial Timeouts: {len(initial_failures)}\n\n")

        fw.write("===== Retest Results =====\n")
        fw.write(f"Recovered after retry: {recovered_count}\n")
        fw.write(f"Recovered after final confirm: {confirm_recovered_count}\n")
        fw.write(f"Final Timeouts: {len(final_failures)}\n\n")

        fw.write("===== Final Timeouts Grouped by DC =====\n")
        grouped = group_failures_by_dc(final_failures, dc_map)
        for dc_name, items in grouped.items():
            fw.write(f"\n{dc_name} ({len(items)}):\n")
            for store, ip in items:
                fw.write(f"  {store}  {ip}\n")

        if gw_online or gw_offline:
            fw.write("\n===== Gateway Check =====\n")
            fw.write(f"Gateways ONLINE (server down): {len(gw_online)}\n")
            for store, srv_ip, gw_ip in gw_online:
                fw.write(f"  {store}  server={srv_ip}  gateway={gw_ip}\n")
            fw.write(f"\nGateways OFFLINE (server down): {len(gw_offline)}\n")
            for store, srv_ip, gw_ip in gw_offline:
                fw.write(f"  {store}  server={srv_ip}  gateway={gw_ip}\n")

def _run_once(args):
    scan_start_perf = time.perf_counter()
    timing_breakdown = {
        "initial_ping_seconds": 0.0,
        "retry_ping_seconds": 0.0,
        "final_confirm_seconds": 0.0,
        "gateway_check_seconds": 0.0,
    }
    publish_dir = Path(args.publish_dir) if args.publish_dir else DEFAULT_PUBLISH_DIR

    csv_path = Path(args.stores_csv)
    dc_map = load_dc_map(Path(args.dc_csv))

    rows = list(load_rows(csv_path))
    total = len(rows)
    if total == 0:
        print(f"No usable rows found in {csv_path}.")
        sys.exit(0)

    store_to_gateway = {r["StoreNumber"]: (r.get("Gateway") or "").strip() for r in rows}

    base_out_dir = Path(args.output_dir)
    base_out_dir.mkdir(parents=True, exist_ok=True)
    run_dt = datetime.now().astimezone()  # local tz
    run_id = (args.run_id or run_dt.strftime("%Y%m%d_%H%M%S")).strip()
    export_run_files = bool(
        args.export_run_files
        or args.write_txt
        or args.zip_run
        or args.remove_run_folder_after_zip
    )
    run_dir: Optional[Path] = None
    if export_run_files:
        run_dir = base_out_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

    targets = [(r["StoreNumber"], r["IPAddress"]) for r in rows]

    if not args.quiet:
        print(f"Scanning {total} stores (initial pass: 1 ping) ...")

    phase_start = time.perf_counter()
    initial_successes, initial_failures = parallel_ping(
        targets,
        count=1,
        timeout_ms=args.timeout_ms,
        max_workers=args.max_workers,
        progress_every=0 if args.quiet else PROGRESS_EVERY,
        quiet=args.quiet
    )
    timing_breakdown["initial_ping_seconds"] = round(time.perf_counter() - phase_start, 3)
    initial_success = len(initial_successes)

    recovered_count = 0
    final_failures = initial_failures

    if initial_failures and args.retry_pings > 0:
        if not args.quiet:
            print(f"Retrying {len(initial_failures)} initial failures ({args.retry_pings} pings each) ...")
        phase_start = time.perf_counter()
        retry_successes, retry_failures = parallel_ping(
            initial_failures,
            count=args.retry_pings,
            timeout_ms=args.timeout_ms,
            max_workers=args.max_workers,
            progress_every=0 if args.quiet else PROGRESS_EVERY,
            quiet=args.quiet
        )
        timing_breakdown["retry_ping_seconds"] = round(time.perf_counter() - phase_start, 3)
        recovered_count = len(retry_successes)
        final_failures = retry_failures

    gw_online, gw_offline = [], []
    confirm_recovered_count = 0
    if args.gateway_check and final_failures:
        pre_confirm_failures = list(final_failures)
        if not args.quiet:
            print(
                "Final confirm on "
                f"{len(pre_confirm_failures)} non-green stores "
                f"({FINAL_CONFIRM_PINGS} pings each for server+gateway) ..."
            )
        phase_start = time.perf_counter()
        confirm_successes, confirm_failures = parallel_ping(
            pre_confirm_failures,
            count=FINAL_CONFIRM_PINGS,
            timeout_ms=args.timeout_ms,
            max_workers=args.max_workers,
            progress_every=0 if args.quiet else PROGRESS_EVERY,
            quiet=args.quiet,
        )
        timing_breakdown["final_confirm_seconds"] = round(time.perf_counter() - phase_start, 3)
        confirm_recovered_count = len(confirm_successes)
        final_failures = confirm_failures

        if not args.quiet:
            print(f"Gateway-checking {len(pre_confirm_failures)} non-green stores ...")
        phase_start = time.perf_counter()
        gw_online, gw_offline = check_gateways_for_failures(
            pre_confirm_failures,
            store_to_gateway=store_to_gateway,
            timeout_ms=args.timeout_ms,
            max_workers=args.max_workers,
            count=max(1, int(args.gateway_pings)),
            progress_every=0 if args.quiet else GW_PROGRESS_EVERY,
        )
        timing_breakdown["gateway_check_seconds"] = round(time.perf_counter() - phase_start, 3)

    # Capture timestamp when the scan is complete
    run_dt_end = datetime.now().astimezone()  # local tz
    run_ts = run_dt_end.isoformat(timespec="seconds")

    # Build failure details for outputs
    failures_detail = []
    for store, ip in final_failures:
        dc_code = first4_digits(store)
        dc_name = dc_map.get(dc_code, f"Unknown DC {dc_code}")
        gw_ip = store_to_gateway.get(store, "") or derive_gateway_ip(ip)
        failures_detail.append({
            "timestamp": run_ts,
            "run_id": run_id,
            "store": store,
            "dc_code": dc_code,
            "dc_name": dc_name,
            "server_ip": ip,
            "stage_failed": "retry" if initial_failures else "initial",
            "gateway_ip": gw_ip or "",
            "gateway_up": ""  # filled below
        })
    if args.gateway_check and (gw_online or gw_offline):
        gw_lookup = {}
        for store, srv_ip, gw_ip in gw_online:
            gw_lookup[(store, srv_ip)] = True
        for store, srv_ip, gw_ip in gw_offline:
            gw_lookup[(store, srv_ip)] = False
        for r in failures_detail:
            key = (r["store"], r["server_ip"])
            if key in gw_lookup:
                r["gateway_up"] = "true" if gw_lookup[key] else "false"

    # Build full map status rows (green/yellow/red) for ALL stores
    failure_map = {(d["store"], d["server_ip"]): d for d in failures_detail}
    map_status_rows = []
    for rec in rows:
        store = rec.get("StoreNumber", "")
        ip = rec.get("IPAddress", "")
        dc_code = first4_digits(store)
        dc_name = dc_map.get(dc_code, f"Unknown DC {dc_code}")
        gw_ip = (rec.get("Gateway") or "").strip() or derive_gateway_ip(ip)
        lat = rec.get("Latitude")
        lon = rec.get("Longitude")

        key = (store, ip)
        server_up = key not in failure_map

        gateway_up = None
        if not server_up:
            gw_val = failure_map[key].get("gateway_up", "")
            if gw_val == "true":
                gateway_up = True
            elif gw_val == "false":
                gateway_up = False

        if server_up:
            status = "green"
            status_code = 0
        else:
            if gateway_up is True:
                status = "yellow"
                status_code = 1
            else:
                status = "red"
                status_code = 2

        map_status_rows.append({
            "timestamp": run_ts,
            "run_id": run_id,
            "store": store,
            "dc_code": dc_code,
            "dc_name": dc_name,
            "server_ip": ip,
            "gateway_ip": gw_ip or "",
            "server_up": "true" if server_up else "false",
            "gateway_up": "" if gateway_up is None else ("true" if gateway_up else "false"),
            "status": status,
            "status_code": status_code,
            "Latitude": lat,
            "Longitude": lon,
            "Address": rec.get("Address", ""),
            "City": rec.get("City", ""),
            "State": rec.get("State", ""),
            "ZIP": rec.get("ZIP", ""),
        })

    # Detect stores that flipped from online -> offline since the previous run
    previous_map = load_latest_map_status(base_out_dir / "map_status_latest.json")
    new_offlines: List[Dict] = []
    back_online: List[Dict] = []
    alert_rows: List[Dict] = []
    if previous_map:
        for row in map_status_rows:
            key = (row.get("store", ""), row.get("server_ip", ""))
            prev = previous_map.get(key)
            if not prev:
                continue
            was_up = _to_bool(prev.get("server_up"))
            is_up = _to_bool(row.get("server_up"))
            if was_up and not is_up:
                new_offlines.append(row)
            elif (not was_up) and is_up:
                recovered_row = dict(row)
                recovered_row["last_seen_offline_at"] = prev.get("timestamp", "unknown")
                back_online.append(recovered_row)

    if new_offlines:
        alert_message = build_offline_alert_message(
            new_offlines,
            run_id,
            run_ts,
            max(1, int(args.alert_max_items)),
        )
        offline_alert_row = {
            "run_id": run_id,
            "timestamp": run_ts,
            "alert_type": "offline",
            "item_count": len(new_offlines),
            "sent": bool(args.teams_webhook),
            "delivery_ok": None,
            "delivery_detail": "",
            "message": alert_message,
        }
        if args.teams_webhook:
            ok, detail = send_teams_webhook(args.teams_webhook, alert_message)
            offline_alert_row["delivery_ok"] = bool(ok)
            offline_alert_row["delivery_detail"] = detail
            if not ok:
                print(f"WARNING: Teams webhook failed: {detail}", file=sys.stderr)
        alert_rows.append(offline_alert_row)
        if not args.quiet:
            print("\n===== NEW OFFLINE STORES =====")
            print(alert_message)

    if back_online:
        back_online_alert = build_back_online_alert_message(
            back_online,
            run_id,
            run_ts,
            max(1, int(args.alert_max_items)),
        )
        online_alert_row = {
            "run_id": run_id,
            "timestamp": run_ts,
            "alert_type": "back_online",
            "item_count": len(back_online),
            "sent": bool(args.teams_webhook),
            "delivery_ok": None,
            "delivery_detail": "",
            "message": back_online_alert,
        }
        if args.teams_webhook:
            ok, detail = send_teams_webhook(args.teams_webhook, back_online_alert)
            online_alert_row["delivery_ok"] = bool(ok)
            online_alert_row["delivery_detail"] = detail
            if not ok:
                print(f"WARNING: Teams webhook failed: {detail}", file=sys.stderr)
        alert_rows.append(online_alert_row)
        if not args.quiet:
            print("\n===== STORES BACK ONLINE =====")
            print(back_online_alert)

    scan_duration_seconds = round(time.perf_counter() - scan_start_perf, 3)

    # Summary object
    summary = {
        "timestamp": run_ts,
        "run_id": run_id,
        "input_csv": str(csv_path),
        "total_stores": total,
        "initial_responding": initial_success,
        "initial_timeouts": len(initial_failures),
        "recovered_after_retry": recovered_count,
        "recovered_after_final_confirm": confirm_recovered_count,
        "final_timeouts": len(final_failures),
        "gateway_check_enabled": bool(args.gateway_check),
        "gateway_online_count": len(gw_online),
        "gateway_offline_count": len(gw_offline),
        "scan_duration_seconds": scan_duration_seconds,
        "timing_breakdown_seconds": timing_breakdown,
        "new_offline_count": len(new_offlines),
        "back_online_count": len(back_online),
    }

    # ===== Write outputs into the per-run folder (optional) =====
    summary_path: Optional[Path] = None
    failures_path: Optional[Path] = None
    if export_run_files and run_dir is not None:
        summary_path = run_dir / f"summary_{run_id}.json"
        failures_path = run_dir / f"failures_{run_id}.json"
        write_json(summary_path, summary)
        write_json(failures_path, failures_detail)
        # Full map status feed (all stores)
        write_json(run_dir / f"map_status_{run_id}.json", map_status_rows)
        write_map_status_csv(run_dir / f"map_status_{run_id}.csv", map_status_rows)
        if args.write_csv:
            write_failures_csv(run_dir / f"failures_{run_id}.csv", failures_detail)
        if args.write_txt:
            write_txt_report(
                run_dir / f"{run_id}_ping_report_v2.txt",
                csv_path, total, initial_success, initial_failures,
                recovered_count, confirm_recovered_count, final_failures,
                dc_map, gw_online, gw_offline, run_ts
            )

    # ===== Also update "latest" convenience files at base_out_dir =====
    write_json(base_out_dir / "summary_latest.json", summary)
    write_json(base_out_dir / "failures_latest.json", failures_detail)
    write_json(base_out_dir / "map_status_latest.json", map_status_rows)
    write_map_status_csv(base_out_dir / "map_status_latest.csv", map_status_rows)
    if args.write_csv:
        write_failures_csv(base_out_dir / "failures_latest.csv", failures_detail)

    # ===== SQLite history write (Phase 1 dual-write) =====
    db_path = Path(args.db_path).expanduser() if args.db_path else (base_out_dir / "sauron.db")
    try:
        write_run_to_sqlite(db_path, summary, map_status_rows, alert_rows)
    except Exception as ex:
        print(f"WARNING: could not write SQLite history to {db_path}: {ex}", file=sys.stderr)

    # Also publish the live feed files to --publish-dir (if different)
    try:
        publish_dir.mkdir(parents=True, exist_ok=True)
        write_json(publish_dir / "map_status_latest.json", map_status_rows)
        write_map_status_csv(publish_dir / "map_status_latest.csv", map_status_rows)
        # convenience: geojson for other tools
        features = []
        for r in map_status_rows:
            if r.get("Latitude") is None or r.get("Longitude") is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["Longitude"], r["Latitude"]]},
                "properties": {k: v for k, v in r.items() if k not in ("Latitude", "Longitude")}
            })
        write_json(publish_dir / "map_status_latest.geojson", {"type": "FeatureCollection", "features": features})
    except Exception as ex:
        print(f"WARNING: could not publish live feed to {publish_dir}: {ex}", file=sys.stderr)

    # ===== Zip per-run folder if requested =====
    zip_file: Optional[Path] = None
    if args.zip_run and run_dir is not None:
        zip_file = zip_run_folder(run_dir)
        if not args.quiet:
            print(f"Created ZIP: {zip_file}")
        if args.remove_run_folder_after_zip:
            try:
                shutil.rmtree(run_dir)
                if not args.quiet:
                    print(f"Removed run folder after zip: {run_dir}")
            except Exception as ex:
                print(f"WARNING: Could not remove run folder {run_dir}: {ex}", file=sys.stderr)

    # ===== Console recap ====
    if not args.quiet:
        print("\n===== DONE =====")
        print(f"Run ID: {run_id}")
        print(f"Total stores: {total}")
        print(f"Initial responding: {initial_success}")
        print(f"Recovered after retry: {recovered_count}")
        print(f"Final timeouts: {len(final_failures)}")
        print(f"Scan duration (s): {scan_duration_seconds}")
        print(
            "Phase timing (s): "
            f"initial={timing_breakdown['initial_ping_seconds']}, "
            f"retry={timing_breakdown['retry_ping_seconds']}, "
            f"confirm={timing_breakdown['final_confirm_seconds']}, "
            f"gateway={timing_breakdown['gateway_check_seconds']}"
        )
        if summary_path and failures_path and run_dir is not None:
            print(f"Wrote: {summary_path}")
            print(f"Wrote: {failures_path}")
            print(f"Wrote: {run_dir / f'map_status_{run_id}.csv'}")
        else:
            print("Per-run snapshot export: disabled (use --export-run-files to enable)")
        print(f"Latest feed: {publish_dir / 'map_status_latest.csv'}")
        print(f"SQLite: {db_path}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stores_csv", nargs="?", default=DEFAULT_CSV,
                    help="Stores CSV path (StoreNumber, IPAddress[, Gateway])")
    ap.add_argument(
        "-a",
        "--auto",
        action="store_true",
        help="Shortcut: equivalent to --gateway-check --gateway-pings 5 --retry-pings 10 --dc-csv DC_LIST.csv",
    )
    ap.add_argument(
        "-l",
        "--low-power",
        action="store_true",
        help=(
            "Low compute preset: equivalent to --max-workers 8 --retry-pings 2 "
            "--gateway-pings 3"
        ),
    )
    ap.add_argument("--dc-csv", default=DC_CSV, help="DC list CSV path (City, DC)")
    ap.add_argument("--timeout-ms", type=int, default=PING_TIMEOUT_MS)
    ap.add_argument("--max-workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--retry-pings", type=int, default=RETRY_PINGS)
    ap.add_argument("--gateway-pings", type=int, default=3,
                    help="Gateway ping count per failed store (default: 3)")
    ap.add_argument("--gateway-check", action="store_true",
                    help="Ping the Gateway column IP (fallback to .1 only if blank)")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUTDIR),
                    help="Directory to write outputs")
    ap.add_argument("--db-path", default="",
                    help="SQLite DB path for historical run/status/alert data (default: <output-dir>/sauron.db)")
    ap.add_argument("--publish-dir", default="",
                    help="Also write/overwrite live map feed files (map_status_latest.*) into this directory. If blank, uses the project folder (script directory).")
    ap.add_argument("--run-id", default="",
                    help="Optional run id string; if blank, timestamp is used")
    ap.add_argument("--write-txt", action="store_true",
                    help="Also write legacy text report")
    ap.add_argument("--write-csv", action="store_true",
                    help="Also write failures CSV (Power BI friendly)")
    ap.add_argument("--export-run-files", action="store_true",
                    help="Write per-run JSON/CSV snapshots under <output-dir>/<run_id> (default: off; latest files + SQLite still written)")
    ap.add_argument("--quiet", action="store_true", help="Less console output")
    ap.add_argument("--zip-run", action="store_true",
                    help="Zip each run folder after writing outputs")
    ap.add_argument("--remove-run-folder-after-zip", action="store_true",
                    help="Delete the per-run folder once zip is created")
    ap.add_argument(
        "-t",
        "--teams-webhook",
        nargs="?",
        const="__ENV__",
        default="",
        help=(
            "Teams incoming webhook URL for alerts about new offline stores. "
            "Shortcut: use -t with no URL to pull from SAURON_TEAMS_WEBHOOK."
        ),
    )
    ap.add_argument("--alert-max-items", type=int, default=25,
                    help="Max number of stores listed in alert message (default: 25)")
    ap.add_argument("--loop", action="store_true",
                    help="Run continuously in a loop (sleeping between runs)")
    ap.add_argument("--interval-seconds", type=int, default=100,
                    help="Loop sleep interval in seconds (default: 100 = 1 minute 40 seconds)")
    args = ap.parse_args()

    if getattr(args, "auto", False):
        # Apply the requested preset.
        args.gateway_check = True
        args.gateway_pings = 3
        args.retry_pings = 6
        args.max_workers = min(int(args.max_workers), 24)
        # Keep it relative to the project folder by default.
        args.dc_csv = "DC_LIST.csv"

    if getattr(args, "low_power", False):
        # Apply a conservative preset to reduce CPU/process/memory pressure.
        args.gateway_check = True
        args.max_workers = min(int(args.max_workers), 8)
        args.retry_pings = min(int(args.retry_pings), 2)
        args.gateway_pings = min(int(args.gateway_pings), 3)

    # Allow -t/--teams-webhook with no URL to use an env var.
    if getattr(args, "teams_webhook", "") == "__ENV__":
        args.teams_webhook = (os.environ.get("SAURON_TEAMS_WEBHOOK", "") or "").strip()
        if not args.teams_webhook:
            print(
                "ERROR: -t/--teams-webhook was provided without a URL, but SAURON_TEAMS_WEBHOOK is not set.",
                file=sys.stderr,
            )
            sys.exit(2)

    if args.loop:
        import time
        if not args.quiet:
            print(f"Loop mode enabled: polling every {args.interval_seconds} seconds (Ctrl+C to stop).")
        while True:
            _run_once(args)
            time.sleep(max(1, int(args.interval_seconds)))
    else:
        _run_once(args)

if __name__ == "__main__":
    main()

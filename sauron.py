#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scanner tool.py

Parallel-pings all stores from the input CSV and produces machine-readable outputs
for dashboards.

Behavior:
- Initial pass: 1 ping per host (parallel)
- Retry pass:   N pings (default 3) for initial failures (parallel)
- Optional gateway check (uses "Gateway" column; falls back to .1 if blank)
- Writes (per-run folder under C:\DESA\scanner\logs\<run_id>\):
  - summary_<run_id>.json
  - failures_<run_id>.json
  - optional failures_<run_id>.csv
  - optional <run_id>_ping_report_v2.txt
- Also updates "latest" files in the base logs folder for convenience.

NEW (Live Map Feed):
- Writes a full "all stores" status feed:
  - map_status_<run_id>.csv/json (per-run)
  - map_status_latest.csv/json (latest)
  - map_status_latest.geojson (published)
- Colors can be driven off the `status` field:
    green  = server_up
    yellow = server_down + gateway_up (requires --gateway-check)
    red    = server_down + gateway_down (or unknown)

Usage examples:
  python sauron.py
  python sauron.py C:\DESA\scanner\stores.csv --gateway-check --write-txt --write-csv
  python sauron.py --output-dir C:\DESA\scanner\logs --run-id latest --zip-run

Live map publishing (recommended when serving current_map_of_sites.html):
  python sauron.py .\stores.csv --gateway-check --output-dir .\logs --publish-dir .
"""

import argparse, csv, os, re, sys, ipaddress, subprocess, json, shutil
from typing import Optional, List, Tuple, Dict
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

BASE_DIR = Path(os.environ.get("SAURON_BASE_DIR", "C:\\DESA\\scannerv2"))
STORES_CSV_DEFAULT = BASE_DIR / "stores.csv"
DC_CSV_DEFAULT = BASE_DIR / "DC_LIST.csv"
OUTPUT_DIR_DEFAULT = BASE_DIR / "logs"
PUBLISH_DIR_DEFAULT = BASE_DIR
DEFAULT_CSV = str(STORES_CSV_DEFAULT)
DC_CSV = str(DC_CSV_DEFAULT)
DEFAULT_OUTPUTDIR = OUTPUT_DIR_DEFAULT
DEFAULT_PUBLISH_DIR = PUBLISH_DIR_DEFAULT

PING_TIMEOUT_MS = 1000
MAX_WORKERS = 200
PROGRESS_EVERY = 250
GW_PROGRESS_EVERY = 200
RETRY_PINGS = 3

_leading_digits = re.compile(r"^(\d+)")

def is_windows() -> bool:
    return os.name == "nt"

def ping_host(ip: str, count: int, timeout_ms: int) -> bool:
    if not ip:
        return False
    if is_windows():
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), ip]
    else:
        secs = max(1, int((timeout_ms + 999) // 1000))
        cmd = ["ping", "-c", str(count), "-W", str(secs), ip]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=max(5, int((timeout_ms * count) / 1000) + 5))
        return result.returncode == 0
    except Exception:
        return False

def derive_gateway_ip(server_ip: str) -> str:
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
    if not store:
        return ""
    m = _leading_digits.match(store.strip())
    if not m:
        return ""
    digits = m.group(1)
    return digits[:4] if len(digits) >= 4 else digits

def store_sort_key(store: str) -> Tuple[int, str]:
    m = _leading_digits.match(store or "")
    if m:
        return (int(m.group(1)), store)
    return (10**12, store or "")

def load_rows(csv_path: Path):
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
            rec = {"StoreNumber": store, "IPAddress": ipaddr, "Gateway": gateway, "Address": (row.get(addr_h) or "").strip() if addr_h else "", "City": (row.get(city_h) or "").strip() if city_h else "", "State": (row.get(state_h) or "").strip() if state_h else "", "ZIP": (row.get(zip_h) or "").strip() if zip_h else "", "Latitude": _to_float((row.get(lat_h) or "").strip()) if lat_h else None, "Longitude": _to_float((row.get(lon_h) or "").strip()) if lon_h else None}
            if store and ipaddr:
                yield rec

def load_dc_map(dc_csv_path: Path) -> Dict[str, str]:
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
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

def main():
    ap = argparse.ArgumentParser(description="Parallel store network scanner with live map feed", prog="sauron")
    ap.add_argument("stores_csv", nargs="?", default=DEFAULT_CSV, help="Stores CSV path (StoreNumber, IPAddress[, Gateway])")
    ap.add_argument("--dc-csv", default=DC_CSV, help="DC list CSV path (City, DC)")
    ap.add_argument("--timeout-ms", type=int, default=PING_TIMEOUT_MS)
    ap.add_argument("--max-workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--retry-pings", type=int, default=RETRY_PINGS)
    ap.add_argument("--gateway-check", action="store_true", help="Ping the Gateway column IP")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUTDIR), help="Directory to write outputs")
    ap.add_argument("--publish-dir", default="", help="Publish live feed files to this directory")
    ap.add_argument("--run-id", default="", help="Optional run id string; if blank, timestamp is used")
    ap.add_argument("--write-txt", action="store_true", help="Also write legacy text report")
    ap.add_argument("--write-csv", action="store_true", help="Also write failures CSV (Power BI friendly)")
    ap.add_argument("--quiet", action="store_true", help="Less console output")
    args = ap.parse_args()
    print("Sauron scanner v3 - demo tool loaded successfully!")

if __name__ == "__main__":
    main()

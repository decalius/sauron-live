#!/usr/bin/env python3
"""Build a public-safe demo feed from internal scanner output.

This script removes or transforms sensitive fields (IPs, DC names/codes, site ids,
exact addresses, exact coordinates) while keeping shape and status distributions
useful for demos.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

US_STATES = [
    "AL", "AZ", "CA", "CO", "FL", "GA", "IA", "IL", "IN", "KS", "KY", "LA",
    "MA", "MD", "MI", "MN", "MO", "NC", "NJ", "NM", "NV", "NY", "OH", "OK",
    "OR", "PA", "SC", "TN", "TX", "UT", "VA", "WA", "WI",
]


def _stable_int(seed: str, modulus: int) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % modulus


def _stable_float(seed: str, low: float, high: float) -> float:
    span = high - low
    n = _stable_int(seed, 1_000_000)
    return low + (n / 999_999.0) * span


def _fake_ip(index: int, role: str) -> str:
    blocks = [(198, 51, 100), (203, 0, 113), (192, 0, 2)]
    b1, b2, b3 = blocks[index % len(blocks)]
    if role == "gateway":
        host = 1 + (index % 50)
    else:
        host = 10 + (index % 200)
    return f"{b1}.{b2}.{b3}.{host}"


def _build_site_map(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    raw_sites = sorted({str(r.get("site") or r.get("store") or "UNKNOWN").strip() for r in rows})
    return {site: f"SITE-{i + 1:04d}" for i, site in enumerate(raw_sites)}


def _build_dc_map(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    raw_dcs = sorted({str(r.get("dc_code") or "DC").strip() for r in rows})
    return {dc: i + 1 for i, dc in enumerate(raw_dcs)}


def sanitize_rows(rows: List[Dict[str, Any]], run_id: str) -> List[Dict[str, Any]]:
    site_map = _build_site_map(rows)
    dc_map = _build_dc_map(rows)
    cleaned: List[Dict[str, Any]] = []

    for i, row in enumerate(rows):
        raw_site = str(row.get("site") or row.get("store") or "UNKNOWN").strip()
        raw_dc = str(row.get("dc_code") or "DC").strip()
        site_id = site_map.get(raw_site, f"SITE-{i + 1:04d}")
        dc_num = dc_map.get(raw_dc, 0)

        lat = round(_stable_float(f"{site_id}:lat", 25.0, 48.8), 4)
        lon = round(_stable_float(f"{site_id}:lon", -124.0, -67.0), 4)

        state = US_STATES[_stable_int(f"{site_id}:state", len(US_STATES))]
        city_idx = _stable_int(f"{site_id}:city", 300)

        status_code = int(row.get("status_code", 0) or 0)
        if status_code not in (0, 1, 2):
            status_code = 0

        status_lookup = {0: "green", 1: "yellow", 2: "red"}

        cleaned.append(
            {
                "timestamp": row.get("timestamp"),
                "run_id": run_id,
                "site": site_id,
                "dc_code": f"DC{dc_num:02d}",
                "dc_name": f"Region {dc_num:02d}",
                "server_ip": _fake_ip(i, "server"),
                "gateway_ip": _fake_ip(i, "gateway"),
                "server_up": bool(row.get("server_up", status_code == 0)),
                "gateway_up": bool(row.get("gateway_up", status_code in (0, 1))),
                "status": status_lookup[status_code],
                "status_code": status_code,
                "Latitude": lat,
                "Longitude": lon,
                "Address": f"{100 + (i % 900)} Example Ave",
                "City": f"Metro {city_idx:03d}",
                "State": state,
                "ZIP": f"{10000 + (i % 89999):05d}",
            }
        )

    return cleaned


def main() -> None:
    parser = argparse.ArgumentParser(description="Sanitize scanner JSON for public demo use.")
    parser.add_argument(
        "--input",
        default="logs/map_status_latest.json",
        help="Path to internal scanner JSON feed.",
    )
    parser.add_argument(
        "--output",
        default="sample_data/map_status_sample.json",
        help="Path to write sanitized public JSON feed.",
    )
    parser.add_argument(
        "--run-id",
        default="public-demo",
        help="Run id written into sanitized records.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    rows = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit("Input JSON must be an array of row objects.")

    cleaned = sanitize_rows(rows, run_id=args.run_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    print(f"Wrote {len(cleaned)} sanitized rows to {output_path}")


if __name__ == "__main__":
    main()

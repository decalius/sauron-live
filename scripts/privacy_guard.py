#!/usr/bin/env python3
"""Fail CI if tracked files appear to contain private operational data."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEXT_CHECK_GLOBS = ["README.md", "*.html", "assets/**/*.md"]
JSON_CHECK_GLOBS = ["sample_data/**/*.json"]

PRIVATE_IPV4 = re.compile(
    r"\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.(?:\d{1,3})\.(?:\d{1,3})|172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3})\.(?:\d{1,3}))\b"
)

TEST_NET_IPV4 = re.compile(r"^(?:198\.51\.100|203\.0\.113|192\.0\.2)\.\d{1,3}$")
SITE_ID = re.compile(r"^SITE-\d{4}$")
DC_CODE = re.compile(r"^DC\d{2}$")
DC_NAME = re.compile(r"^Region\s\d{2}$")


def _iter_files(patterns):
    seen = set()
    for pattern in patterns:
        for path in ROOT.glob(pattern):
            if path.is_file() and path not in seen:
                seen.add(path)
                yield path


def _check_text_file(path: Path):
    rel = path.relative_to(ROOT).as_posix()
    text = path.read_text(encoding="utf-8", errors="ignore")
    violations = []
    if PRIVATE_IPV4.search(text):
        violations.append((rel, "private_ipv4"))
    return violations


def _check_public_json(path: Path):
    rel = path.relative_to(ROOT).as_posix()
    violations = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [(rel, "invalid_json")]

    if not isinstance(payload, list):
        return [(rel, "expected_json_array")]

    for idx, row in enumerate(payload):
        if not isinstance(row, dict):
            violations.append((rel, f"row_{idx}_not_object"))
            continue

        if PRIVATE_IPV4.search(json.dumps(row)):
            violations.append((rel, f"row_{idx}_private_ipv4"))

        for key in ("server_ip", "gateway_ip"):
            value = str(row.get(key, ""))
            if value and not TEST_NET_IPV4.match(value):
                violations.append((rel, f"row_{idx}_{key}_not_testnet"))

        site = str(row.get("site", ""))
        if site and not SITE_ID.match(site):
            violations.append((rel, f"row_{idx}_site_not_generic"))

        dc_code = str(row.get("dc_code", ""))
        if dc_code and not DC_CODE.match(dc_code):
            violations.append((rel, f"row_{idx}_dc_code_not_generic"))

        dc_name = str(row.get("dc_name", ""))
        if dc_name and not DC_NAME.match(dc_name):
            violations.append((rel, f"row_{idx}_dc_name_not_generic"))

    return violations


def main() -> int:
    violations = []

    for file_path in _iter_files(TEXT_CHECK_GLOBS):
        violations.extend(_check_text_file(file_path))

    for file_path in _iter_files(JSON_CHECK_GLOBS):
        violations.extend(_check_public_json(file_path))

    if violations:
        print("Privacy guard failed. Potential sensitive content detected:")
        for rel, name in violations:
            print(f" - {rel}: {name}")
        print("\nUse scripts/sanitize_public_feed.py and replace sensitive values before publishing.")
        return 1

    print("Privacy guard passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_MAP_PIN = "/sys/fs/bpf/nodeport_tc/maps/nodeport_stats_map"
STAT_NAMES = [
    "tcp_packets",
    "nodeport_hit",
    "backend_selected",
    "backend_lookup_miss",
    "rr_update",
    "snat_install",
    "request_rewrite",
    "revnat_hit",
    "ct_lookup_miss",
    "response_rewrite",
    "same_node_skip",
    "fwd_ct_hit",
    "new_conn",
    "map_miss",
    "rewrite_fail",
    "redirect_ok",
    "redirect_fail",
    "fallback_pass",
]


def parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except ValueError:
            return None
    if isinstance(value, dict):
        for item in value.values():
            parsed = parse_int(item)
            if parsed is not None:
                return parsed
    if isinstance(value, list):
        if value and all(isinstance(item, str) for item in value):
            try:
                return int.from_bytes(bytes(int(item, 0) for item in value), "little")
            except ValueError:
                return None
        for item in value:
            parsed = parse_int(item)
            if parsed is not None:
                return parsed
    return None


def parse_entry_total(entry: dict[str, Any]) -> int:
    if "values" in entry and isinstance(entry["values"], list):
        total = 0
        for item in entry["values"]:
            parsed = parse_int(item.get("value") if isinstance(item, dict) else item)
            if parsed is not None:
                total += parsed
        return total

    formatted = entry.get("formatted", {})
    if isinstance(formatted, dict) and isinstance(formatted.get("values"), list):
        total = 0
        for item in formatted["values"]:
            if isinstance(item, dict):
                parsed = parse_int(item.get("value"))
                if parsed is not None:
                    total += parsed
        return total

    return parse_int(entry.get("value")) or 0


def load_stats(map_pin: str) -> list[int]:
    result = subprocess.run(
        ["bpftool", "-j", "map", "dump", "pinned", map_pin],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"bpftool failed for {map_pin}: {stderr}")

    payload = json.loads(result.stdout)
    values = [0] * len(STAT_NAMES)
    for entry in payload:
        index = parse_int(entry.get("key"))
        if index is not None and 0 <= index < len(values):
            values[index] = parse_entry_total(entry)
    return values


def main() -> int:
    map_pin = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MAP_PIN
    if not Path(map_pin).exists():
        print(f"missing stats map: {map_pin}", file=sys.stderr)
        return 1

    for name, value in zip(STAT_NAMES, load_stats(map_pin)):
        print(f"{name}={value}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3

import argparse
import csv
import http.client
import json
import math
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple concurrent HTTP benchmark for Phase 5.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--warmup-seconds", type=float, default=10.0)
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def now_ms() -> int:
    return int(time.time() * 1000.0)


def percentile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    index = ratio * (len(ordered) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[lower])
    weight = index - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


def request_once(connection: Optional[http.client.HTTPConnection], host: str, port: int, path: str) -> tuple[Optional[http.client.HTTPConnection], bool, int, float, str]:
    if connection is None:
        connection = http.client.HTTPConnection(host, port, timeout=2.0)
    start = time.perf_counter()
    error = ""
    status = 0
    ok = False
    try:
        connection.request("GET", path, headers={"Connection": "keep-alive"})
        response = connection.getresponse()
        body = response.read()
        status = response.status
        ok = 200 <= status < 400 and len(body) >= 0
    except Exception as exc:  # noqa: BLE001
        error = type(exc).__name__
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass
        connection = None
    latency_ms = (time.perf_counter() - start) * 1000.0
    return connection, ok, status, latency_ms, error


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(args.url)
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    if not host:
        raise SystemExit(f"invalid url: {args.url}")

    request_rows: List[Dict[str, object]] = []
    lock = threading.Lock()
    wall_start = time.time()
    warmup_deadline = time.monotonic() + args.warmup_seconds
    measure_start = warmup_deadline
    measure_end = measure_start + args.duration_seconds
    measure_start_wall_ms = int((wall_start + args.warmup_seconds) * 1000.0)
    measure_end_wall_ms = int((wall_start + args.warmup_seconds + args.duration_seconds) * 1000.0)

    def worker() -> None:
        connection: Optional[http.client.HTTPConnection] = None
        try:
            while time.monotonic() < measure_end:
                current = time.monotonic()
                in_measurement = current >= measure_start
                connection, ok, status, latency_ms, error = request_once(connection, host, port, path)
                timestamp = now_ms()
                if in_measurement:
                    row = {
                        "ts_unix_ms": timestamp,
                        "ok": 1 if ok else 0,
                        "status_code": status,
                        "latency_ms": f"{latency_ms:.3f}",
                        "error": error,
                    }
                    with lock:
                        request_rows.append(row)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:  # noqa: BLE001
                    pass

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.concurrency)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    request_rows.sort(key=lambda row: row["ts_unix_ms"])
    total_requests = len(request_rows)
    successful_rows = [row for row in request_rows if row["ok"] == 1]
    successful_latencies = [float(row["latency_ms"]) for row in successful_rows]
    success_count = len(successful_rows)
    throughput_rps = float(total_requests) / float(args.duration_seconds) if args.duration_seconds > 0 else 0.0
    success_rate = float(success_count) / float(total_requests) if total_requests else 0.0

    summary = {
        "url": args.url,
        "warmup_seconds": args.warmup_seconds,
        "duration_seconds": args.duration_seconds,
        "concurrency": args.concurrency,
        "total_requests": total_requests,
        "successful_requests": success_count,
        "success_rate": round(success_rate, 6),
        "throughput_rps": round(throughput_rps, 6),
        "p50_latency_ms": round(percentile(successful_latencies, 0.50), 3),
        "p99_latency_ms": round(percentile(successful_latencies, 0.99), 3),
        "measurement_start_unix_ms": measure_start_wall_ms,
        "measurement_end_unix_ms": measure_end_wall_ms,
    }

    with (args.output_dir / "bench_requests.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ts_unix_ms", "ok", "status_code", "latency_ms", "error"])
        writer.writeheader()
        writer.writerows(request_rows)

    with (args.output_dir / "bench_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")

    print(json.dumps(summary, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

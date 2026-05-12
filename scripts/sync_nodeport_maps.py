#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import pwd
import shlex
import socket
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_UPDATE_SCRIPT = SCRIPT_DIR / "update_nodeport_map.sh"
DEFAULT_SERVICE_MAP_PIN = "/sys/fs/bpf/nodeport_tc/maps/nodeport_service_map"
DEFAULT_BACKEND_MAP_PIN = "/sys/fs/bpf/nodeport_tc/maps/nodeport_backend_map"
DEFAULT_RR_STATE_MAP_PIN = "/sys/fs/bpf/nodeport_tc/maps/nodeport_rr_state_map"


@dataclass(frozen=True)
class Backend:
    address: str
    port: int
    node_ip: str


@dataclass(frozen=True)
class NodePortEntry:
    namespace: str
    name: str
    node_ip: str
    snat_ip: str
    node_port: int
    backends: tuple[Backend, ...]


def log(message: str) -> None:
    print(message, file=sys.stderr)


def format_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run_read(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {format_command(command)}\n{result.stderr.strip()}"
        )
    return result.stdout


def run_write(command: list[str], dry_run: bool) -> None:
    log(f"+ {format_command(command)}")
    if dry_run:
        return

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        raise RuntimeError(f"command failed ({result.returncode}): {format_command(command)}")
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


def root_command(command: list[str]) -> list[str]:
    if os.geteuid() == 0:
        return command
    return ["sudo", "-n", *command]


def resolve_kubeconfig_path() -> str | None:
    kubeconfig = os.environ.get("KUBECONFIG")
    if kubeconfig:
        return kubeconfig

    if os.geteuid() == 0:
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and sudo_user != "root":
            try:
                candidate = Path(pwd.getpwnam(sudo_user).pw_dir) / ".kube" / "config"
            except KeyError:
                return None
            if candidate.is_file():
                return str(candidate)
    return None


def kubectl_command(kubectl: str, *args: str) -> list[str]:
    command = [kubectl, *args]
    kubeconfig_path = resolve_kubeconfig_path()
    if kubeconfig_path:
        return ["env", f"KUBECONFIG={kubeconfig_path}", *command]
    return command


def kubectl_get_json(kubectl: str, resource: str) -> dict[str, Any]:
    return json.loads(run_read(kubectl_command(kubectl, "get", resource, "-A", "-o", "json")))


def detect_iface_ipv4(iface: str) -> str:
    output = run_read(["ip", "-o", "-4", "addr", "show", "dev", iface])
    for line in output.splitlines():
        parts = line.split()
        if "inet" not in parts:
            continue
        value = parts[parts.index("inet") + 1]
        return value.split("/", 1)[0]
    raise RuntimeError(f"unable to detect IPv4 address for interface {iface}")


def detect_node_name(kubectl: str, requested: str | None) -> str:
    if requested:
        return requested
    if os.environ.get("NODE_NAME"):
        return os.environ["NODE_NAME"]

    nodes = kubectl_get_json(kubectl, "nodes")
    hostname = socket.gethostname()
    for item in nodes.get("items", []):
        name = item["metadata"]["name"]
        if name == hostname or name.startswith(hostname) or hostname.startswith(name):
            return name

    raise RuntimeError("unable to determine node name; pass --node-name or set NODE_NAME")


def parse_service_selector(raw: str | None) -> tuple[str, str] | None:
    if not raw:
        return None
    if "/" not in raw:
        raise RuntimeError("--service must use namespace/name")
    namespace, name = raw.split("/", 1)
    if not namespace or not name:
        raise RuntimeError("--service must use namespace/name")
    return namespace, name


def node_internal_ips(nodes: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in nodes.get("items", []):
        name = item.get("metadata", {}).get("name", "")
        for address in item.get("status", {}).get("addresses", []):
            if address.get("type") == "InternalIP" and "." in address.get("address", ""):
                result[name] = address["address"]
                break
    return result


def build_slice_index(endpoint_slices: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in endpoint_slices.get("items", []):
        labels = item.get("metadata", {}).get("labels", {})
        namespace = item.get("metadata", {}).get("namespace", "")
        service_name = labels.get("kubernetes.io/service-name")
        if namespace and service_name:
            index[(namespace, service_name)].append(item)
    return index


def eligible_endpoint(endpoint: dict[str, Any]) -> bool:
    conditions = endpoint.get("conditions") or {}
    target_ref = endpoint.get("targetRef") or {}
    if conditions.get("ready") is False:
        return False
    if conditions.get("serving") is False:
        return False
    if conditions.get("terminating") is True:
        return False
    if target_ref.get("kind") and target_ref.get("kind") != "Pod":
        return False
    return bool(endpoint.get("nodeName"))


def first_ipv4_address(endpoint: dict[str, Any]) -> str | None:
    for address in endpoint.get("addresses") or []:
        if "." in address:
            return address
    return None


def matching_slice_ports(service_port: dict[str, Any], endpoint_slice: dict[str, Any]) -> list[dict[str, Any]]:
    tcp_ports = [
        port
        for port in (endpoint_slice.get("ports") or [])
        if (port.get("protocol") or "TCP") == "TCP" and port.get("port") is not None
    ]
    if not tcp_ports:
        return []

    service_port_name = service_port.get("name") or ""
    target_port = service_port.get("targetPort")

    if service_port_name:
        named = [port for port in tcp_ports if (port.get("name") or "") == service_port_name]
        if named:
            return named

    if isinstance(target_port, int):
        matched = [port for port in tcp_ports if port.get("port") == target_port]
        if matched:
            return matched

    if len(tcp_ports) == 1:
        return tcp_ports

    return [port for port in tcp_ports if port.get("port") == service_port.get("port")]


def collect_backends(
    service_port: dict[str, Any],
    slices: list[dict[str, Any]],
    node_ips: dict[str, str],
) -> list[Backend]:
    backends: list[Backend] = []
    seen: set[tuple[str, int]] = set()

    for endpoint_slice in slices:
        matched_ports = matching_slice_ports(service_port, endpoint_slice)
        if not matched_ports:
            continue

        for endpoint in endpoint_slice.get("endpoints") or []:
            if not eligible_endpoint(endpoint):
                continue

            address = first_ipv4_address(endpoint)
            node_name = endpoint.get("nodeName", "")
            node_ip = node_ips.get(node_name)
            if not address or not node_ip:
                continue

            for matched_port in matched_ports:
                backend_port = matched_port.get("port")
                if not isinstance(backend_port, int):
                    continue
                key = (address, backend_port)
                if key in seen:
                    continue
                seen.add(key)
                backends.append(Backend(address=address, port=backend_port, node_ip=node_ip))

    return backends


def load_desired_entries(args: argparse.Namespace, node_name: str) -> list[NodePortEntry]:
    nodes = kubectl_get_json(args.kubectl, "nodes")
    services = kubectl_get_json(args.kubectl, "services")
    endpoint_slices = kubectl_get_json(args.kubectl, "endpointslices.discovery.k8s.io")
    node_ips = node_internal_ips(nodes)
    node_ip = node_ips.get(node_name)
    if not node_ip:
        raise RuntimeError(f"unable to find InternalIP for node {node_name}")
    snat_ip = args.snat_ip or detect_iface_ipv4(args.snat_iface)

    selector = parse_service_selector(args.service)
    slice_index = build_slice_index(endpoint_slices)
    entries: list[NodePortEntry] = []

    for service in services.get("items", []):
        metadata = service.get("metadata", {})
        spec = service.get("spec", {})
        namespace = metadata.get("namespace", "")
        name = metadata.get("name", "")

        if selector and (namespace, name) != selector:
            continue
        if spec.get("type") != "NodePort":
            continue
        if spec.get("externalTrafficPolicy", "Cluster") != "Cluster":
            log(f"skipping {namespace}/{name}: only externalTrafficPolicy=Cluster is supported")
            continue

        slices = slice_index.get((namespace, name), [])
        if not slices:
            log(f"skipping {namespace}/{name}: no EndpointSlice found")
            continue

        for service_port in spec.get("ports") or []:
            if (service_port.get("protocol") or "TCP") != "TCP":
                continue
            node_port = service_port.get("nodePort")
            if not isinstance(node_port, int):
                continue

            backends = collect_backends(service_port, slices, node_ips)
            if not backends:
                log(f"skipping {namespace}/{name}:{node_port}: no eligible backend")
                continue

            entries.append(
                NodePortEntry(
                    namespace=namespace,
                    name=name,
                    node_ip=node_ip,
                    snat_ip=snat_ip,
                    node_port=node_port,
                    backends=tuple(backends),
                )
            )

    entries.sort(key=lambda item: (item.namespace, item.name, item.node_port))
    return entries


def parse_dump_keys(output: str) -> list[list[str]]:
    keys: list[list[str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("key:"):
            continue
        key_part = line.split("value:", 1)[0]
        key_bytes = key_part.replace("key:", "", 1).strip().split()
        if key_bytes:
            keys.append(key_bytes)
    return keys


def clear_map(map_pin: str, dry_run: bool) -> int:
    if dry_run:
        log(f"# dry-run: skipping clear for {map_pin}")
        return 0

    output = run_read(root_command(["bpftool", "map", "dump", "pinned", map_pin]))
    keys = parse_dump_keys(output)
    for key in keys:
        run_write(root_command(["bpftool", "map", "delete", "pinned", map_pin, "key", "hex", *key]), False)
    return len(keys)


def update_command(
    args: argparse.Namespace,
    entry: NodePortEntry,
    backend: Backend,
    index: int,
) -> list[str]:
    return root_command(
        [
            "env",
            f"SERVICE_MAP_PIN={args.service_map_pin}",
            f"BACKEND_MAP_PIN={args.backend_map_pin}",
            args.update_script,
            entry.node_ip,
            str(entry.node_port),
            entry.snat_ip,
            backend.address,
            str(backend.port),
            backend.node_ip,
            str(index),
            str(len(entry.backends)),
        ]
    )


def sync_entries(args: argparse.Namespace, entries: list[NodePortEntry]) -> None:
    svc_cleared = clear_map(args.service_map_pin, args.dry_run)
    backend_cleared = clear_map(args.backend_map_pin, args.dry_run)
    rr_cleared = clear_map(args.rr_state_map_pin, args.dry_run)
    log(f"cleared stale keys: service={svc_cleared}, backend={backend_cleared}, rr={rr_cleared}")

    total_backends = sum(len(entry.backends) for entry in entries)
    log(f"selected nodeport services: {len(entries)}, total backends: {total_backends}")

    for entry in entries:
        for index, backend in enumerate(entry.backends):
            run_write(update_command(args, entry, backend, index), args.dry_run)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synchronize NodePort/TCP Services into BPF maps")
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--node-name")
    parser.add_argument("--service", help="optional namespace/name selector")
    parser.add_argument("--snat-ip", help="SNAT source IP; defaults to --snat-iface IPv4")
    parser.add_argument("--snat-iface", default="cni0", help="interface used to detect SNAT source IP")
    parser.add_argument("--update-script", default=str(DEFAULT_UPDATE_SCRIPT))
    parser.add_argument("--service-map-pin", default=DEFAULT_SERVICE_MAP_PIN)
    parser.add_argument("--backend-map-pin", default=DEFAULT_BACKEND_MAP_PIN)
    parser.add_argument("--rr-state-map-pin", default=DEFAULT_RR_STATE_MAP_PIN)
    parser.add_argument("--poll-interval", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    node_name = detect_node_name(args.kubectl, args.node_name)
    log(f"node name: {node_name}")
    if args.service:
        log(f"service selector: {args.service}")

    previous_snapshot: tuple[NodePortEntry, ...] | None = None
    iteration = 0

    while True:
        iteration += 1
        entries = load_desired_entries(args, node_name)
        snapshot = tuple(entries)
        if snapshot != previous_snapshot:
            log(f"iteration {iteration}: syncing NodePort map state")
            sync_entries(args, entries)
            previous_snapshot = snapshot
            log("sync complete")
        else:
            log(f"iteration {iteration}: state unchanged")

        if args.poll_interval <= 0:
            return 0

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        log(f"error: {error}")
        raise SystemExit(1)

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ipaddress
import json
import shlex
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any


ENCAP_CANDIDATES = (
    "flannel.1",
    "cilium_vxlan",
    "cilium_geneve",
    "vxlan.calico",
    "genev_sys_6081",
    "tunl0",
)
LOCAL_DELIVERY_CANDIDATES = (
    "cni0",
    "kube-bridge",
    "weave",
    "docker0",
)


@dataclass(frozen=True)
class DeliveryEnvironment:
    external_iface: str
    local_delivery_iface: str
    remote_delivery_iface: str
    routing_mode: str
    attach_inner_ifaces: tuple[str, ...]


@dataclass(frozen=True)
class DeliveryClusterFacts:
    node_ip: str
    local_pod_cidrs: tuple[str, ...]
    remote_delivery_targets: tuple[str, ...]


def run_read(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(shlex.quote(part) for part in command)}\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout


def iface_exists(name: str) -> bool:
    if not name:
        return False
    result = subprocess.run(["ip", "link", "show", "dev", name], capture_output=True, text=True, check=False)
    return result.returncode == 0


def normalize_iface_list(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return dedupe_strings(*(item.strip() for item in raw.split(",")))


def normalize_value_list(raw: list[str] | None) -> tuple[str, ...]:
    if not raw:
        return ()
    values: list[str] = []
    for item in raw:
        for piece in item.split(","):
            stripped = piece.strip()
            if stripped:
                values.append(stripped)
    return dedupe_strings(*values)


def dedupe_strings(*items: str) -> tuple[str, ...]:
    result = []
    seen = set()
    for item in items:
        if not item or item in seen:
            continue
        result.append(item)
        seen.add(item)
    return tuple(result)


def first_existing(candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        if iface_exists(candidate):
            return candidate
    return ""


def detect_default_route_iface() -> str:
    output = run_read(["ip", "-o", "route", "show", "default"])
    for line in output.splitlines():
        parts = line.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    raise RuntimeError("unable to detect default route interface")


def parse_route_dev(output: str) -> str:
    for line in output.splitlines():
        parts = line.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    return ""


def detect_iface_for_target(address: str) -> str:
    output = run_read(["ip", "-o", "route", "get", address])
    return parse_route_dev(output)


def detect_iface_for_targets(targets: tuple[str, ...]) -> str:
    for target in targets:
        iface = detect_iface_for_target(target)
        if iface:
            return iface
    return ""


def detect_iface_for_node_ip(node_ip: str) -> str:
    if not node_ip:
        return ""

    output = run_read(["ip", "-o", "-4", "addr", "show"])
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        iface = parts[1]
        address = parts[3].split("/", 1)[0]
        if address == node_ip:
            return iface
    return ""


def probe_address_for_cidr(cidr: str) -> str:
    network = ipaddress.ip_network(cidr, strict=False)
    if network.version != 4:
        raise ValueError(f"only IPv4 CIDR is supported: {cidr}")

    base = int(network.network_address)
    broadcast = int(network.broadcast_address)
    if network.num_addresses == 1:
        return str(network.network_address)
    if network.num_addresses >= 4:
        candidate = base + 2
        if candidate < broadcast:
            return str(ipaddress.IPv4Address(candidate))
    if base + 1 <= broadcast:
        return str(ipaddress.IPv4Address(base + 1))
    return str(network.network_address)


def cidr_probe_targets(cidrs: tuple[str, ...]) -> tuple[str, ...]:
    targets: list[str] = []
    for cidr in cidrs:
        if "." not in cidr:
            continue
        try:
            targets.append(probe_address_for_cidr(cidr))
        except ValueError:
            continue
    return dedupe_strings(*targets)


def kubectl_get_json(kubectl: str, resource: str) -> dict[str, Any]:
    return json.loads(run_read([kubectl, "get", resource, "-o", "json"]))


def detect_node_name(nodes: dict[str, Any], requested: str | None) -> str:
    if requested:
        return requested

    hostname = socket.gethostname()
    for item in nodes.get("items", []):
        name = item.get("metadata", {}).get("name", "")
        if name == hostname or name.startswith(hostname) or hostname.startswith(name):
            return name
    raise RuntimeError("unable to determine node name; pass --node-name")


def node_internal_ip(nodes: dict[str, Any], node_name: str) -> str:
    for item in nodes.get("items", []):
        if item.get("metadata", {}).get("name") != node_name:
            continue
        for address in item.get("status", {}).get("addresses", []):
            candidate = address.get("address", "")
            if address.get("type") == "InternalIP" and "." in candidate:
                return candidate
        break
    raise RuntimeError(f"unable to find IPv4 InternalIP for node {node_name}")


def node_ipv4_pod_cidrs(node: dict[str, Any]) -> tuple[str, ...]:
    cidrs: list[str] = []
    primary = node.get("spec", {}).get("podCIDR")
    if isinstance(primary, str) and "." in primary:
        cidrs.append(primary)
    for candidate in node.get("spec", {}).get("podCIDRs") or []:
        if isinstance(candidate, str) and "." in candidate:
            cidrs.append(candidate)
    return dedupe_strings(*cidrs)


def build_cluster_facts(
    nodes: dict[str, Any],
    node_name: str,
    remote_delivery_targets: tuple[str, ...] = (),
) -> DeliveryClusterFacts:
    local_node: dict[str, Any] | None = None
    remote_pod_cidrs: list[str] = []

    for item in nodes.get("items", []):
        name = item.get("metadata", {}).get("name", "")
        if name == node_name:
            local_node = item
            continue
        remote_pod_cidrs.extend(node_ipv4_pod_cidrs(item))

    if local_node is None:
        raise RuntimeError(f"unable to find node in cluster state: {node_name}")

    inferred_remote_targets = cidr_probe_targets(tuple(remote_pod_cidrs))
    return DeliveryClusterFacts(
        node_ip=node_internal_ip(nodes, node_name),
        local_pod_cidrs=node_ipv4_pod_cidrs(local_node),
        remote_delivery_targets=dedupe_strings(*remote_delivery_targets, *inferred_remote_targets),
    )


def detect_environment(
    *,
    external_iface: str | None = None,
    local_delivery_iface: str | None = None,
    remote_delivery_iface: str | None = None,
    routing_mode: str | None = None,
    attach_iface: str | None = None,
    inner_ifaces: str | None = None,
    snat_iface: str | None = None,
    node_ip: str | None = None,
    local_pod_cidrs: tuple[str, ...] = (),
    remote_delivery_targets: tuple[str, ...] = (),
) -> DeliveryEnvironment:
    normalized_inner = normalize_iface_list(inner_ifaces)
    local_targets = cidr_probe_targets(dedupe_strings(*local_pod_cidrs))
    remote_targets = dedupe_strings(*remote_delivery_targets)

    resolved_external = (external_iface or attach_iface or "").strip()
    if resolved_external and not iface_exists(resolved_external):
        raise RuntimeError(f"configured external iface does not exist: {resolved_external}")
    if not resolved_external and node_ip:
        resolved_external = detect_iface_for_node_ip(node_ip)
    if not resolved_external:
        resolved_external = detect_default_route_iface()

    resolved_local = (local_delivery_iface or "").strip()
    if resolved_local and not iface_exists(resolved_local):
        raise RuntimeError(f"configured local delivery iface does not exist: {resolved_local}")
    if not resolved_local and local_targets:
        resolved_local = detect_iface_for_targets(local_targets)
    if not resolved_local and normalized_inner:
        resolved_local = normalized_inner[0]
    if not resolved_local and snat_iface and iface_exists(snat_iface):
        resolved_local = snat_iface
    if not resolved_local:
        resolved_local = first_existing(LOCAL_DELIVERY_CANDIDATES)
    if not resolved_local:
        raise RuntimeError("unable to detect local delivery iface")

    resolved_remote = (remote_delivery_iface or "").strip()
    if resolved_remote and not iface_exists(resolved_remote):
        raise RuntimeError(f"configured remote delivery iface does not exist: {resolved_remote}")
    if not resolved_remote and remote_targets:
        resolved_remote = detect_iface_for_targets(remote_targets)
    if not resolved_remote and len(normalized_inner) > 1:
        resolved_remote = normalized_inner[1]
    if not resolved_remote:
        resolved_remote = first_existing(ENCAP_CANDIDATES)

    resolved_routing_mode = (routing_mode or "").strip().lower()
    if resolved_routing_mode and resolved_routing_mode not in {"native", "encap"}:
        raise RuntimeError("routing mode must be native or encap")
    if not resolved_routing_mode:
        if resolved_remote and resolved_remote != resolved_external:
            resolved_routing_mode = "encap"
        else:
            resolved_routing_mode = "native"

    if resolved_routing_mode == "encap" and not resolved_remote:
        raise RuntimeError("routing_mode=encap requires a remote delivery iface")

    return DeliveryEnvironment(
        external_iface=resolved_external,
        local_delivery_iface=resolved_local,
        remote_delivery_iface=resolved_remote,
        routing_mode=resolved_routing_mode,
        attach_inner_ifaces=dedupe_strings(
            *(iface for iface in (resolved_local, resolved_remote) if iface and iface != resolved_external)
        ),
    )


def emit_shell(profile: DeliveryEnvironment) -> None:
    values = {
        "NODEPORT_DETECTED_EXTERNAL_IFACE": profile.external_iface,
        "NODEPORT_DETECTED_LOCAL_DELIVERY_IFACE": profile.local_delivery_iface,
        "NODEPORT_DETECTED_REMOTE_DELIVERY_IFACE": profile.remote_delivery_iface,
        "NODEPORT_DETECTED_ROUTING_MODE": profile.routing_mode,
        "NODEPORT_DETECTED_ATTACH_INNER_IFACES": ",".join(profile.attach_inner_ifaces),
    }
    for key, value in values.items():
        print(f"{key}={shlex.quote(value)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect NodePort delivery environment")
    parser.add_argument("--external-iface")
    parser.add_argument("--local-delivery-iface")
    parser.add_argument("--remote-delivery-iface")
    parser.add_argument("--routing-mode")
    parser.add_argument("--attach-iface")
    parser.add_argument("--inner-ifaces")
    parser.add_argument("--snat-iface")
    parser.add_argument("--kubectl", default="")
    parser.add_argument("--node-name")
    parser.add_argument("--node-ip")
    parser.add_argument("--local-pod-cidr", action="append", default=[])
    parser.add_argument("--remote-target", action="append", default=[])
    parser.add_argument("--format", choices=("json", "shell"), default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    node_ip = args.node_ip or ""
    local_pod_cidrs = normalize_value_list(args.local_pod_cidr)
    remote_targets = normalize_value_list(args.remote_target)

    if args.kubectl:
        nodes = kubectl_get_json(args.kubectl, "nodes")
        node_name = detect_node_name(nodes, args.node_name)
        cluster_facts = build_cluster_facts(nodes, node_name, remote_targets)
        if not node_ip:
            node_ip = cluster_facts.node_ip
        if not local_pod_cidrs:
            local_pod_cidrs = cluster_facts.local_pod_cidrs
        remote_targets = dedupe_strings(*remote_targets, *cluster_facts.remote_delivery_targets)

    profile = detect_environment(
        external_iface=args.external_iface,
        local_delivery_iface=args.local_delivery_iface,
        remote_delivery_iface=args.remote_delivery_iface,
        routing_mode=args.routing_mode,
        attach_iface=args.attach_iface,
        inner_ifaces=args.inner_ifaces,
        snat_iface=args.snat_iface,
        node_ip=node_ip or None,
        local_pod_cidrs=local_pod_cidrs,
        remote_delivery_targets=remote_targets,
    )
    if args.format == "shell":
        emit_shell(profile)
    else:
        print(json.dumps(asdict(profile), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)

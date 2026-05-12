#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import pwd
import queue
import shlex
import socket
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from detect_nodeport_environment import DeliveryEnvironment, build_cluster_facts, detect_environment

try:
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config
    from kubernetes import watch as k8s_watch
    from kubernetes.client import ApiException
except ImportError:
    k8s_client = None
    k8s_config = None
    k8s_watch = None
    ApiException = Exception


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_UPDATE_SCRIPT = SCRIPT_DIR / "update_nodeport_map.sh"
DEFAULT_UPDATE_CONFIG_SCRIPT = SCRIPT_DIR / "update_nodeport_config_map.sh"
DEFAULT_SERVICE_MAP_PIN = "/sys/fs/bpf/nodeport_tc/maps/nodeport_service_map"
DEFAULT_BACKEND_MAP_PIN = "/sys/fs/bpf/nodeport_tc/maps/nodeport_backend_map"
DEFAULT_RR_STATE_MAP_PIN = "/sys/fs/bpf/nodeport_tc/maps/nodeport_rr_state_map"
DEFAULT_CONFIG_MAP_PIN = "/sys/fs/bpf/nodeport_tc/maps/nodeport_config_map"


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


def load_cluster_state(kubectl: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        kubectl_get_json(kubectl, "nodes"),
        kubectl_get_json(kubectl, "services"),
        kubectl_get_json(kubectl, "endpointslices.discovery.k8s.io"),
    )


def load_kube_api_clients() -> tuple[Any, Any, Any]:
    if not k8s_client or not k8s_config or not k8s_watch:
        raise RuntimeError("watch mode requires the Python kubernetes client to be installed")

    try:
        k8s_config.load_incluster_config()
    except Exception:
        kubeconfig_path = resolve_kubeconfig_path()
        if kubeconfig_path:
            k8s_config.load_kube_config(config_file=kubeconfig_path)
        else:
            k8s_config.load_kube_config()
    return k8s_client.CoreV1Api(), k8s_client.DiscoveryV1Api(), k8s_client.CustomObjectsApi()


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


def load_desired_entries(
    args: argparse.Namespace,
    node_name: str,
    *,
    nodes: dict[str, Any] | None = None,
    services: dict[str, Any] | None = None,
    endpoint_slices: dict[str, Any] | None = None,
) -> list[NodePortEntry]:
    nodes = nodes or kubectl_get_json(args.kubectl, "nodes")
    services = services or kubectl_get_json(args.kubectl, "services")
    endpoint_slices = endpoint_slices or kubectl_get_json(args.kubectl, "endpointslices.discovery.k8s.io")
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


def iface_ifindex(iface: str) -> int:
    output = run_read(["cat", f"/sys/class/net/{iface}/ifindex"])
    return int(output.strip())


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


def config_update_command(args: argparse.Namespace, profile: DeliveryEnvironment) -> list[str]:
    routing_mode = 1 if profile.routing_mode == "encap" else 0
    remote_ifindex = iface_ifindex(profile.remote_delivery_iface) if profile.remote_delivery_iface else 0
    return root_command(
        [
            "env",
            f"CONFIG_MAP_PIN={args.config_map_pin}",
            args.update_config_script,
            str(iface_ifindex(profile.external_iface)),
            str(iface_ifindex(profile.local_delivery_iface)),
            str(remote_ifindex),
            str(routing_mode),
        ]
    )


def sync_delivery_config(args: argparse.Namespace, profile: DeliveryEnvironment) -> None:
    log(
        "updating delivery config: "
        f"external={profile.external_iface} local={profile.local_delivery_iface} "
        f"remote={profile.remote_delivery_iface or 'none'} mode={profile.routing_mode}"
    )
    run_write(config_update_command(args, profile), args.dry_run)


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


def remote_backend_targets(entries: tuple[NodePortEntry, ...]) -> tuple[str, ...]:
    targets: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        for backend in entry.backends:
            if backend.node_ip == entry.node_ip:
                continue
            if backend.address in seen:
                continue
            seen.add(backend.address)
            targets.append(backend.address)
    return tuple(targets)


def compute_desired_state(
    args: argparse.Namespace,
    node_name: str,
) -> tuple[tuple[NodePortEntry, ...], DeliveryEnvironment, list[NodePortEntry]]:
    nodes, services, endpoint_slices = load_cluster_state(args.kubectl)
    entries = load_desired_entries(
        args,
        node_name,
        nodes=nodes,
        services=services,
        endpoint_slices=endpoint_slices,
    )
    snapshot = tuple(entries)
    cluster_facts = build_cluster_facts(nodes, node_name, remote_backend_targets(snapshot))
    profile = detect_environment(
        external_iface=args.external_iface,
        local_delivery_iface=args.local_delivery_iface,
        remote_delivery_iface=args.remote_delivery_iface,
        routing_mode=args.routing_mode,
        attach_iface=args.attach_iface,
        inner_ifaces=args.inner_ifaces,
        snat_iface=args.snat_iface,
        node_ip=cluster_facts.node_ip,
        local_pod_cidrs=cluster_facts.local_pod_cidrs,
        remote_delivery_targets=cluster_facts.remote_delivery_targets,
    )
    return snapshot, profile, entries


def reconcile(
    args: argparse.Namespace,
    node_name: str,
    previous_snapshot: tuple[NodePortEntry, ...] | None,
    previous_profile: DeliveryEnvironment | None,
    *,
    reason: str,
) -> tuple[tuple[NodePortEntry, ...] | None, DeliveryEnvironment | None]:
    snapshot, profile, entries = compute_desired_state(args, node_name)
    if profile != previous_profile:
        log(f"{reason}: syncing delivery config")
        sync_delivery_config(args, profile)
        previous_profile = profile
    if snapshot != previous_snapshot:
        log(f"{reason}: syncing NodePort map state")
        sync_entries(args, entries)
        previous_snapshot = snapshot
        log("sync complete")
    elif profile == previous_profile:
        log(f"{reason}: state unchanged")
    return previous_snapshot, previous_profile


def object_metadata_fields(obj: Any) -> tuple[str, str, str]:
    if isinstance(obj, dict):
        metadata = obj.get("metadata") or {}
        return (
            metadata.get("resourceVersion", ""),
            metadata.get("namespace", ""),
            metadata.get("name", ""),
        )

    metadata = getattr(obj, "metadata", None)
    return (
        getattr(metadata, "resource_version", "") if metadata else "",
        getattr(metadata, "namespace", "") if metadata else "",
        getattr(metadata, "name", "") if metadata else "",
    )


def watcher_list_metadata_response(list_response: Any) -> tuple[str, int]:
    if isinstance(list_response, dict):
        metadata = list_response.get("metadata") or {}
        return metadata.get("resourceVersion", ""), len(list_response.get("items") or [])

    metadata = getattr(list_response, "metadata", None)
    resource_version = getattr(metadata, "resource_version", "") if metadata else ""
    count = len(getattr(list_response, "items", []) or [])
    return resource_version, count


def describe_watch_event(resource_name: str, event: dict[str, Any]) -> str:
    event_type = event.get("type", "UNKNOWN")
    obj = event.get("object")
    _, namespace, name = object_metadata_fields(obj)
    qualified = f"{namespace}/{name}" if namespace else name
    return f"{resource_name}:{event_type}:{qualified or '-'}"


def watch_resource(
    *,
    resource_name: str,
    list_fn: Any,
    event_queue: queue.Queue[str],
    stop_event: threading.Event,
) -> None:
    resource_version = ""
    while not stop_event.is_set():
        try:
            if not resource_version:
                response = list_fn(_request_timeout=30)
                resource_version, count = watcher_list_metadata_response(response)
                log(f"watch {resource_name}: listed {count} objects rv={resource_version}")

            watcher = k8s_watch.Watch()
            for event in watcher.stream(
                list_fn,
                resource_version=resource_version,
                timeout_seconds=300,
                _request_timeout=330,
            ):
                if stop_event.is_set():
                    watcher.stop()
                    break
                obj = event.get("object")
                new_rv, _, _ = object_metadata_fields(obj)
                if new_rv:
                    resource_version = new_rv
                event_queue.put(describe_watch_event(resource_name, event))
            continue
        except ApiException as error:
            if getattr(error, "status", None) == 410:
                log(f"watch {resource_name}: resourceVersion expired, relisting")
                resource_version = ""
                event_queue.put(f"{resource_name}:RESYNC:-")
                time.sleep(1)
                continue
            log(f"watch {resource_name}: api error: {error}")
        except Exception as error:
            log(f"watch {resource_name}: error: {error}")
        time.sleep(1)


def run_watch_loop(args: argparse.Namespace, node_name: str) -> int:
    core_api, discovery_api, custom_api = load_kube_api_clients()
    previous_snapshot: tuple[NodePortEntry, ...] | None = None
    previous_profile: DeliveryEnvironment | None = None
    stop_event = threading.Event()
    event_queue: queue.Queue[str] = queue.Queue()
    threads = [
        threading.Thread(
            target=watch_resource,
            kwargs={
                "resource_name": "Service",
                "list_fn": core_api.list_service_for_all_namespaces,
                "event_queue": event_queue,
                "stop_event": stop_event,
            },
            daemon=True,
        ),
        threading.Thread(
            target=watch_resource,
            kwargs={
                "resource_name": "Node",
                "list_fn": core_api.list_node,
                "event_queue": event_queue,
                "stop_event": stop_event,
            },
            daemon=True,
        ),
        threading.Thread(
            target=watch_resource,
            kwargs={
                "resource_name": "EndpointSlice",
                "list_fn": lambda **kwargs: custom_api.list_cluster_custom_object(
                    group="discovery.k8s.io",
                    version="v1",
                    plural="endpointslices",
                    **kwargs,
                ),
                "event_queue": event_queue,
                "stop_event": stop_event,
            },
            daemon=True,
        ),
    ]

    previous_snapshot, previous_profile = reconcile(
        args,
        node_name,
        previous_snapshot,
        previous_profile,
        reason="initial sync",
    )

    for thread in threads:
        thread.start()

    try:
        while True:
            first_event = event_queue.get()
            events = [first_event]
            deadline = time.monotonic() + args.watch_debounce
            while True:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break
                try:
                    events.append(event_queue.get(timeout=timeout))
                except queue.Empty:
                    break
            log(f"watch: received {len(events)} event(s); reconciling")
            previous_snapshot, previous_profile = reconcile(
                args,
                node_name,
                previous_snapshot,
                previous_profile,
                reason="watch reconcile",
            )
    except KeyboardInterrupt:
        stop_event.set()
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synchronize NodePort/TCP Services into BPF maps")
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--node-name")
    parser.add_argument("--service", help="optional namespace/name selector")
    parser.add_argument("--snat-ip", help="SNAT source IP; defaults to --snat-iface IPv4")
    parser.add_argument("--snat-iface", default="cni0", help="interface used to detect SNAT source IP")
    parser.add_argument("--external-iface")
    parser.add_argument("--local-delivery-iface")
    parser.add_argument("--remote-delivery-iface")
    parser.add_argument("--routing-mode", choices=("native", "encap"))
    parser.add_argument("--attach-iface")
    parser.add_argument("--inner-ifaces")
    parser.add_argument("--update-script", default=str(DEFAULT_UPDATE_SCRIPT))
    parser.add_argument("--update-config-script", default=str(DEFAULT_UPDATE_CONFIG_SCRIPT))
    parser.add_argument("--service-map-pin", default=DEFAULT_SERVICE_MAP_PIN)
    parser.add_argument("--backend-map-pin", default=DEFAULT_BACKEND_MAP_PIN)
    parser.add_argument("--rr-state-map-pin", default=DEFAULT_RR_STATE_MAP_PIN)
    parser.add_argument("--config-map-pin", default=DEFAULT_CONFIG_MAP_PIN)
    parser.add_argument("--sync-mode", choices=("oneshot", "poll", "watch"), default="oneshot")
    parser.add_argument("--poll-interval", type=float, default=0.0)
    parser.add_argument("--watch-debounce", type=float, default=0.2)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    node_name = detect_node_name(args.kubectl, args.node_name)
    log(f"node name: {node_name}")
    if args.service:
        log(f"service selector: {args.service}")
    if args.sync_mode == "watch":
        return run_watch_loop(args, node_name)

    previous_snapshot: tuple[NodePortEntry, ...] | None = None
    previous_profile: DeliveryEnvironment | None = None
    iteration = 0

    while True:
        iteration += 1
        previous_snapshot, previous_profile = reconcile(
            args,
            node_name,
            previous_snapshot,
            previous_profile,
            reason=f"iteration {iteration}",
        )

        if args.sync_mode != "poll":
            return 0
        if args.poll_interval <= 0:
            raise RuntimeError("poll mode requires --poll-interval > 0")
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        log(f"error: {error}")
        raise SystemExit(1)

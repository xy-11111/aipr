package envdetect

import (
	"context"
	"fmt"
	"net"
	"net/netip"
	"os"
	"os/exec"
	"sort"
	"strings"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

var (
	encapCandidates = []string{
		"flannel.1",
		"cilium_vxlan",
		"cilium_geneve",
		"vxlan.calico",
		"genev_sys_6081",
		"tunl0",
	}
	localDeliveryCandidates = []string{
		"cni0",
		"kube-bridge",
		"weave",
		"docker0",
	}
)

type Environment struct {
	ExternalIface       string
	LocalDeliveryIface  string
	RemoteDeliveryIface string
	RoutingMode         string
	AttachInnerIfaces   []string
}

type ClusterFacts struct {
	NodeName              string
	NodeIP                string
	LocalPodCIDRs         []string
	RemoteDeliveryTargets []string
}

type DetectOptions struct {
	ExternalIface       string
	LocalDeliveryIface  string
	RemoteDeliveryIface string
	RoutingMode         string
	AttachIface         string
	InnerIfaces         string
	SNATIface           string
}

func LoadClusterFacts(
	ctx context.Context,
	client kubernetes.Interface,
	requestedNode string,
	explicitRemoteTargets []string,
) (ClusterFacts, error) {
	nodes, err := client.CoreV1().Nodes().List(ctx, metav1.ListOptions{})
	if err != nil {
		return ClusterFacts{}, fmt.Errorf("list nodes: %w", err)
	}
	return BuildClusterFacts(nodes.Items, requestedNode, explicitRemoteTargets)
}

func BuildClusterFacts(nodes []corev1.Node, requestedNode string, explicitRemoteTargets []string) (ClusterFacts, error) {
	nodeName, err := resolveNodeName(nodes, requestedNode)
	if err != nil {
		return ClusterFacts{}, err
	}

	var localNode *corev1.Node
	var remotePodCIDRs []string
	for i := range nodes {
		node := &nodes[i]
		if node.Name == nodeName {
			localNode = node
			continue
		}
		remotePodCIDRs = append(remotePodCIDRs, nodeIPv4PodCIDRs(*node)...)
	}
	if localNode == nil {
		return ClusterFacts{}, fmt.Errorf("unable to find node in cluster state: %s", nodeName)
	}

	nodeIP, err := nodeInternalIPv4(*localNode)
	if err != nil {
		return ClusterFacts{}, err
	}

	return ClusterFacts{
		NodeName:              nodeName,
		NodeIP:                nodeIP,
		LocalPodCIDRs:         nodeIPv4PodCIDRs(*localNode),
		RemoteDeliveryTargets: dedupeStrings(explicitRemoteTargets, cidrProbeTargets(remotePodCIDRs)),
	}, nil
}

func Detect(opts DetectOptions, facts ClusterFacts) (Environment, error) {
	normalizedInner := normalizeCSV(opts.InnerIfaces)
	localTargets := cidrProbeTargets(facts.LocalPodCIDRs)
	remoteTargets := dedupeStrings(facts.RemoteDeliveryTargets)

	resolvedExternal := firstNonEmpty(strings.TrimSpace(opts.ExternalIface), strings.TrimSpace(opts.AttachIface))
	if resolvedExternal != "" && !ifaceExists(resolvedExternal) {
		return Environment{}, fmt.Errorf("configured external iface does not exist: %s", resolvedExternal)
	}
	if resolvedExternal == "" && facts.NodeIP != "" {
		resolvedExternal = detectIfaceForNodeIP(facts.NodeIP)
	}
	if resolvedExternal == "" {
		var err error
		resolvedExternal, err = detectDefaultRouteIface()
		if err != nil {
			return Environment{}, err
		}
	}

	resolvedLocal := strings.TrimSpace(opts.LocalDeliveryIface)
	if resolvedLocal != "" && !ifaceExists(resolvedLocal) {
		return Environment{}, fmt.Errorf("configured local delivery iface does not exist: %s", resolvedLocal)
	}
	if resolvedLocal == "" && len(localTargets) > 0 {
		resolvedLocal = detectIfaceForTargets(localTargets)
	}
	if resolvedLocal == "" && len(normalizedInner) > 0 {
		resolvedLocal = normalizedInner[0]
	}
	if resolvedLocal == "" && strings.TrimSpace(opts.SNATIface) != "" && ifaceExists(strings.TrimSpace(opts.SNATIface)) {
		resolvedLocal = strings.TrimSpace(opts.SNATIface)
	}
	if resolvedLocal == "" {
		resolvedLocal = firstExisting(localDeliveryCandidates)
	}
	if resolvedLocal == "" {
		return Environment{}, fmt.Errorf("unable to detect local delivery iface")
	}

	resolvedRemote := strings.TrimSpace(opts.RemoteDeliveryIface)
	if resolvedRemote != "" && !ifaceExists(resolvedRemote) {
		return Environment{}, fmt.Errorf("configured remote delivery iface does not exist: %s", resolvedRemote)
	}
	if resolvedRemote == "" && len(remoteTargets) > 0 {
		resolvedRemote = detectIfaceForTargets(remoteTargets)
	}
	if resolvedRemote == "" && len(normalizedInner) > 1 {
		resolvedRemote = normalizedInner[1]
	}
	if resolvedRemote == "" {
		resolvedRemote = firstExisting(encapCandidates)
	}

	resolvedMode := strings.ToLower(strings.TrimSpace(opts.RoutingMode))
	if resolvedMode != "" && resolvedMode != "native" && resolvedMode != "encap" {
		return Environment{}, fmt.Errorf("routing mode must be native or encap")
	}
	if resolvedMode == "" {
		if resolvedRemote != "" && resolvedRemote != resolvedExternal {
			resolvedMode = "encap"
		} else {
			resolvedMode = "native"
		}
	}
	if resolvedMode == "encap" && resolvedRemote == "" {
		return Environment{}, fmt.Errorf("routing_mode=encap requires a remote delivery iface")
	}

	return Environment{
		ExternalIface:       resolvedExternal,
		LocalDeliveryIface:  resolvedLocal,
		RemoteDeliveryIface: resolvedRemote,
		RoutingMode:         resolvedMode,
		AttachInnerIfaces: dedupeStrings(
			filterNonEmpty([]string{resolvedLocal, resolvedRemote}, func(iface string) bool {
				return iface != resolvedExternal
			}),
		),
	}, nil
}

func resolveNodeName(nodes []corev1.Node, requested string) (string, error) {
	requested = strings.TrimSpace(requested)
	if requested != "" {
		for _, node := range nodes {
			if node.Name == requested {
				return requested, nil
			}
		}
		return "", fmt.Errorf("unable to find node in cluster state: %s", requested)
	}

	hostname, err := os.Hostname()
	if err != nil {
		return "", fmt.Errorf("detect hostname: %w", err)
	}
	for _, node := range nodes {
		if node.Name == hostname || strings.HasPrefix(node.Name, hostname) || strings.HasPrefix(hostname, node.Name) {
			return node.Name, nil
		}
	}
	return "", fmt.Errorf("unable to determine node name; pass --node-name or set NODE_NAME")
}

func nodeInternalIPv4(node corev1.Node) (string, error) {
	for _, address := range node.Status.Addresses {
		ip := net.ParseIP(address.Address).To4()
		if address.Type == corev1.NodeInternalIP && ip != nil {
			return ip.String(), nil
		}
	}
	return "", fmt.Errorf("unable to find IPv4 InternalIP for node %s", node.Name)
}

func nodeIPv4PodCIDRs(node corev1.Node) []string {
	var cidrs []string
	if cidr := strings.TrimSpace(node.Spec.PodCIDR); cidr != "" && strings.Contains(cidr, ".") {
		cidrs = append(cidrs, cidr)
	}
	for _, candidate := range node.Spec.PodCIDRs {
		candidate = strings.TrimSpace(candidate)
		if candidate != "" && strings.Contains(candidate, ".") {
			cidrs = append(cidrs, candidate)
		}
	}
	return dedupeStrings(cidrs)
}

func cidrProbeTargets(cidrs []string) []string {
	var targets []string
	for _, cidr := range cidrs {
		target, err := probeAddressForCIDR(cidr)
		if err != nil {
			continue
		}
		targets = append(targets, target)
	}
	return dedupeStrings(targets)
}

func probeAddressForCIDR(raw string) (string, error) {
	prefix, err := netip.ParsePrefix(raw)
	if err != nil {
		return "", err
	}
	if !prefix.Addr().Is4() {
		return "", fmt.Errorf("only IPv4 CIDR is supported: %s", raw)
	}

	addr := prefix.Masked().Addr()
	ones, bits := prefix.Bits(), prefix.Addr().BitLen()
	hostBits := bits - ones
	base := addr.As4()
	value := uint32(base[0])<<24 | uint32(base[1])<<16 | uint32(base[2])<<8 | uint32(base[3])

	switch {
	case hostBits == 0:
		return addr.String(), nil
	case hostBits >= 2:
		candidate := value + 2
		broadcast := value + (1 << hostBits) - 1
		if candidate < broadcast {
			return uint32ToIPv4(candidate), nil
		}
		fallthrough
	default:
		candidate := value + 1
		broadcast := value + (1 << hostBits) - 1
		if candidate <= broadcast {
			return uint32ToIPv4(candidate), nil
		}
		return addr.String(), nil
	}
}

func uint32ToIPv4(value uint32) string {
	return net.IPv4(byte(value>>24), byte(value>>16), byte(value>>8), byte(value)).String()
}

func normalizeCSV(raw string) []string {
	if strings.TrimSpace(raw) == "" {
		return nil
	}
	return dedupeStrings(strings.FieldsFunc(raw, func(r rune) bool { return r == ',' }))
}

func ifaceExists(name string) bool {
	name = strings.TrimSpace(name)
	if name == "" {
		return false
	}
	_, err := net.InterfaceByName(name)
	return err == nil
}

func firstExisting(candidates []string) string {
	for _, candidate := range candidates {
		if ifaceExists(candidate) {
			return candidate
		}
	}
	return ""
}

func detectDefaultRouteIface() (string, error) {
	output, err := runRead("ip", "-o", "route", "show", "default")
	if err != nil {
		return "", err
	}
	for _, line := range strings.Split(output, "\n") {
		fields := strings.Fields(line)
		for i := 0; i+1 < len(fields); i++ {
			if fields[i] == "dev" {
				return fields[i+1], nil
			}
		}
	}
	return "", fmt.Errorf("unable to detect default route interface")
}

func detectIfaceForTarget(target string) string {
	output, err := runRead("ip", "-o", "route", "get", target)
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(output, "\n") {
		fields := strings.Fields(line)
		for i := 0; i+1 < len(fields); i++ {
			if fields[i] == "dev" {
				return fields[i+1]
			}
		}
	}
	return ""
}

func detectIfaceForTargets(targets []string) string {
	for _, target := range targets {
		if iface := detectIfaceForTarget(target); iface != "" {
			return iface
		}
	}
	return ""
}

func detectIfaceForNodeIP(nodeIP string) string {
	interfaces, err := net.Interfaces()
	if err != nil {
		return ""
	}
	for _, iface := range interfaces {
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, addr := range addrs {
			switch value := addr.(type) {
			case *net.IPNet:
				if ip := value.IP.To4(); ip != nil && ip.String() == nodeIP {
					return iface.Name
				}
			case *net.IPAddr:
				if ip := value.IP.To4(); ip != nil && ip.String() == nodeIP {
					return iface.Name
				}
			}
		}
	}
	return ""
}

func runRead(name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("command failed: %s %s: %w: %s", name, strings.Join(args, " "), err, strings.TrimSpace(string(output)))
	}
	return string(output), nil
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func filterNonEmpty(values []string, keep func(string) bool) []string {
	var result []string
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		if keep != nil && !keep(value) {
			continue
		}
		result = append(result, value)
	}
	return result
}

func dedupeStrings(groups ...[]string) []string {
	seen := make(map[string]struct{})
	var result []string
	for _, group := range groups {
		for _, item := range group {
			item = strings.TrimSpace(item)
			if item == "" {
				continue
			}
			if _, ok := seen[item]; ok {
				continue
			}
			seen[item] = struct{}{}
			result = append(result, item)
		}
	}
	sort.Strings(result)
	return result
}

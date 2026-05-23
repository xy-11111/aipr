package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/cilium/ebpf"
	corev1 "k8s.io/api/core/v1"
	discoveryv1 "k8s.io/api/discovery/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/util/intstr"
	informers "k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	corelisters "k8s.io/client-go/listers/core/v1"
	discoverylisters "k8s.io/client-go/listers/discovery/v1"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/cache"
	"k8s.io/client-go/tools/clientcmd"
)

const (
	protoTCP                 = 6
	routingNative            = "native"
	routingEncap             = "encap"
	defaultSvcPin            = "/sys/fs/bpf/nodeport_tc/maps/nodeport_service_map"
	defaultBckPin            = "/sys/fs/bpf/nodeport_tc/maps/nodeport_backend_map"
	defaultRRPin             = "/sys/fs/bpf/nodeport_tc/maps/nodeport_rr_state_map"
	defaultCfgPin            = "/sys/fs/bpf/nodeport_tc/maps/nodeport_config_map"
	defaultCTPin             = "/sys/fs/bpf/nodeport_tc/maps/nodeport_ct_map"
	defaultFwdCTPin          = "/sys/fs/bpf/nodeport_tc/maps/nodeport_fwd_ct_map"
	defaultStatsPin          = "/sys/fs/bpf/nodeport_tc/maps/nodeport_stats_map"
	defaultDebounce          = 200 * time.Millisecond
	defaultConntrackTimeout  = 10 * time.Minute
	defaultConntrackInterval = 30 * time.Second
	defaultTelemetryWindow   = time.Second
	defaultTelemetryFormat   = "csv"
	defaultTelemetryOutput   = "/var/log/ebpf-nodeport/telemetry"
)

type options struct {
	NodeName            string
	ServiceSelector     string
	ClearTargetState    bool
	SNATIP              string
	SNATIface           string
	ExternalIface       string
	LocalDeliveryIface  string
	RemoteDeliveryIface string
	RoutingMode         string
	ServiceMapPin       string
	BackendMapPin       string
	RRStateMapPin       string
	ConfigMapPin        string
	CTMapPin            string
	FwdCTMapPin         string
	StatsMapPin         string
	SyncMode            string
	PollInterval        time.Duration
	WatchDebounce       time.Duration
	CTEntryTimeout      time.Duration
	CTGCInterval        time.Duration
	TelemetryEnable     bool
	TelemetryWindow     time.Duration
	TelemetryFormat     string
	TelemetryOutput     string
	TelemetryExperiment string
	TelemetryEventsFile string
	TelemetryService    string
	DryRun              bool
}

type serviceSelector struct {
	Namespace string
	Name      string
}

type serviceID struct {
	Namespace string
	Name      string
	NodePort  int32
}

type backend struct {
	Address string
	Port    int32
	NodeIP  string
}

type nodePortEntry struct {
	ID       serviceID
	NodeIP   string
	SNATIP   string
	Backends []backend
}

type deliveryConfig struct {
	ExternalIfindex       uint32
	LocalDeliveryIfindex  uint32
	RemoteDeliveryIfindex uint32
	RoutingMode           uint32
}

type nodePortKey struct {
	Address [4]byte
	Port    [2]byte
	Proto   uint8
	Pad     uint8
}

type nodePortValue struct {
	BackendCount uint32
	Flags        uint32
	SNATIP       [4]byte
}

type nodePortBackendKey struct {
	Service nodePortKey
	Slot    uint32
}

type nodePortBackendValue struct {
	Address [4]byte
	Port    [2]byte
	Pad     [2]byte
	NodeIP  [4]byte
}

type pinnedMaps struct {
	dryRun        bool
	serviceMapPin string
	backendMapPin string
	rrStateMapPin string
	configMapPin  string
	ctMapPin      string
	fwdCTMapPin   string
	statsMapPin   string
	serviceMap    *ebpf.Map
	backendMap    *ebpf.Map
	rrStateMap    *ebpf.Map
	configMap     *ebpf.Map
	ctMap         *ebpf.Map
	fwdCTMap      *ebpf.Map
	statsMap      *ebpf.Map
	possibleCPUs  int
}

type controller struct {
	opts           options
	selector       *serviceSelector
	telemetrySel   *serviceSelector
	serviceLister  corelisters.ServiceLister
	nodeLister     corelisters.NodeLister
	sliceLister    discoverylisters.EndpointSliceLister
	triggerCh      chan string
	maps           *pinnedMaps
	nodeName       string
	appliedConfig  *deliveryConfig
	appliedEntries map[serviceID]nodePortEntry
	stateMu        sync.RWMutex
	telemetry      *telemetryWindowAccumulator
}

func main() {
	log.SetFlags(log.LstdFlags)

	opts, err := parseOptions()
	if err != nil {
		log.Fatalf("error: %v", err)
	}

	selector, err := parseServiceSelector(opts.ServiceSelector)
	if err != nil {
		log.Fatalf("error: %v", err)
	}
	telemetrySel, err := resolveTelemetrySelector(opts)
	if err != nil {
		log.Fatalf("error: %v", err)
	}

	clientConfig, err := loadKubeConfig()
	if err != nil {
		log.Fatalf("error: %v", err)
	}

	clientset, err := kubernetes.NewForConfig(clientConfig)
	if err != nil {
		log.Fatalf("error: create kubernetes client: %v", err)
	}

	factory := informers.NewSharedInformerFactory(clientset, 0)
	serviceInformer := factory.Core().V1().Services()
	nodeInformer := factory.Core().V1().Nodes()
	sliceInformer := factory.Discovery().V1().EndpointSlices()

	maps, err := openPinnedMaps(opts)
	if err != nil {
		log.Fatalf("error: %v", err)
	}
	defer maps.Close()

	ctrl := &controller{
		opts:           opts,
		selector:       selector,
		telemetrySel:   telemetrySel,
		serviceLister:  serviceInformer.Lister(),
		nodeLister:     nodeInformer.Lister(),
		sliceLister:    sliceInformer.Lister(),
		triggerCh:      make(chan string, 256),
		maps:           maps,
		nodeName:       strings.TrimSpace(opts.NodeName),
		appliedEntries: make(map[serviceID]nodePortEntry),
		telemetry:      newTelemetryWindowAccumulator(),
	}

	serviceHandler := cache.ResourceEventHandlerFuncs{
		AddFunc: func(obj interface{}) {
			ctrl.noteServiceEvent(obj)
			ctrl.enqueue("add")
		},
		UpdateFunc: func(oldObj, newObj interface{}) {
			if sameResourceVersion(oldObj, newObj) {
				return
			}
			ctrl.noteServiceEvent(newObj)
			ctrl.enqueue("update")
		},
		DeleteFunc: func(obj interface{}) {
			ctrl.noteServiceEvent(obj)
			ctrl.enqueue("delete")
		},
	}
	nodeHandler := cache.ResourceEventHandlerFuncs{
		AddFunc: func(obj interface{}) {
			ctrl.noteNodeEvent(obj)
			ctrl.enqueue("add")
		},
		UpdateFunc: func(oldObj, newObj interface{}) {
			if sameResourceVersion(oldObj, newObj) {
				return
			}
			ctrl.noteNodeEvent(newObj)
			ctrl.enqueue("update")
		},
		DeleteFunc: func(obj interface{}) {
			ctrl.noteNodeEvent(obj)
			ctrl.enqueue("delete")
		},
	}
	sliceHandler := cache.ResourceEventHandlerFuncs{
		AddFunc: func(obj interface{}) {
			ctrl.noteSliceEvent(obj)
			ctrl.enqueue("add")
		},
		UpdateFunc: func(oldObj, newObj interface{}) {
			if sameResourceVersion(oldObj, newObj) {
				return
			}
			ctrl.noteSliceEvent(newObj)
			ctrl.enqueue("update")
		},
		DeleteFunc: func(obj interface{}) {
			ctrl.noteSliceEvent(obj)
			ctrl.enqueue("delete")
		},
	}

	if _, err := serviceInformer.Informer().AddEventHandler(serviceHandler); err != nil {
		log.Fatalf("error: add service handler: %v", err)
	}
	if _, err := nodeInformer.Informer().AddEventHandler(nodeHandler); err != nil {
		log.Fatalf("error: add node handler: %v", err)
	}
	if _, err := sliceInformer.Informer().AddEventHandler(sliceHandler); err != nil {
		log.Fatalf("error: add endpointslice handler: %v", err)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	log.Printf("sync mode=%s", opts.SyncMode)
	if ctrl.nodeName != "" {
		log.Printf("node name: %s", ctrl.nodeName)
	}
	if selector != nil {
		log.Printf("service selector: %s/%s", selector.Namespace, selector.Name)
	}

	factory.Start(ctx.Done())

	if !cache.WaitForCacheSync(
		ctx.Done(),
		serviceInformer.Informer().HasSynced,
		nodeInformer.Informer().HasSynced,
		sliceInformer.Informer().HasSynced,
	) {
		log.Fatalf("error: timed out waiting for informer caches to sync")
	}

	if opts.ClearTargetState {
		if err := ctrl.clearTargetState("initial target clear"); err != nil {
			log.Fatalf("error: %v", err)
		}
	}
	if err := ctrl.reconcile("initial sync"); err != nil {
		log.Fatalf("error: %v", err)
	}

	if err := runSteadyState(ctx, ctrl); err != nil {
		log.Fatalf("error: %v", err)
	}
}

func parseOptions() (options, error) {
	var opts options
	var pollInterval float64
	var watchDebounce float64

	flag.StringVar(&opts.NodeName, "node-name", os.Getenv("NODE_NAME"), "local node name")
	flag.StringVar(&opts.ServiceSelector, "service", "", "optional namespace/name selector")
	flag.BoolVar(&opts.ClearTargetState, "clear-target-state", false, "in oneshot mode, clear current target service state before reconciling it")
	flag.StringVar(&opts.SNATIP, "snat-ip", "", "SNAT source IPv4 address")
	flag.StringVar(&opts.SNATIface, "snat-iface", "cni0", "interface used to detect SNAT IPv4")
	flag.StringVar(&opts.ExternalIface, "external-iface", "", "external interface name")
	flag.StringVar(&opts.LocalDeliveryIface, "local-delivery-iface", "", "local pod delivery interface")
	flag.StringVar(&opts.RemoteDeliveryIface, "remote-delivery-iface", "", "remote pod delivery interface")
	flag.StringVar(&opts.RoutingMode, "routing-mode", "", "native or encap")
	flag.StringVar(&opts.ServiceMapPin, "service-map-pin", defaultSvcPin, "pinned service map path")
	flag.StringVar(&opts.BackendMapPin, "backend-map-pin", defaultBckPin, "pinned backend map path")
	flag.StringVar(&opts.RRStateMapPin, "rr-state-map-pin", defaultRRPin, "pinned rr-state map path")
	flag.StringVar(&opts.ConfigMapPin, "config-map-pin", defaultCfgPin, "pinned config map path")
	flag.StringVar(&opts.CTMapPin, "ct-map-pin", defaultCTPin, "pinned conntrack map path")
	flag.StringVar(&opts.FwdCTMapPin, "fwd-ct-map-pin", defaultFwdCTPin, "pinned forward conntrack map path")
	flag.StringVar(&opts.StatsMapPin, "stats-map-pin", defaultStatsPin, "pinned stats map path")
	flag.StringVar(&opts.SyncMode, "sync-mode", "watch", "oneshot, poll, or watch")
	flag.Float64Var(&pollInterval, "poll-interval", 5.0, "poll interval in seconds")
	flag.Float64Var(&watchDebounce, "watch-debounce", 0.2, "watch debounce in seconds")
	flag.DurationVar(&opts.CTEntryTimeout, "ct-entry-timeout", defaultConntrackTimeout, "maximum idle time before a conntrack entry is reaped")
	flag.DurationVar(&opts.CTGCInterval, "ct-gc-interval", defaultConntrackInterval, "interval between conntrack GC runs; set to 0 to disable")
	flag.BoolVar(&opts.TelemetryEnable, "telemetry-enable", false, "enable node-level telemetry export")
	flag.DurationVar(&opts.TelemetryWindow, "telemetry-window", defaultTelemetryWindow, "telemetry collection window")
	flag.StringVar(&opts.TelemetryFormat, "telemetry-format", defaultTelemetryFormat, "telemetry output format")
	flag.StringVar(&opts.TelemetryOutput, "telemetry-output", defaultTelemetryOutput, "telemetry output root directory")
	flag.StringVar(&opts.TelemetryExperiment, "telemetry-experiment-id", "default", "telemetry experiment identifier")
	flag.StringVar(&opts.TelemetryEventsFile, "telemetry-events-file", "", "optional external events manifest for label mapping")
	flag.StringVar(&opts.TelemetryService, "telemetry-service", "", "target service for telemetry labels and backend context")
	flag.BoolVar(&opts.DryRun, "dry-run", false, "log map operations without applying them")
	flag.Parse()

	if err := validateOptions(&opts, pollInterval, watchDebounce); err != nil {
		return opts, err
	}
	return opts, nil
}

func validateOptions(opts *options, pollInterval, watchDebounce float64) error {
	opts.SyncMode = strings.ToLower(strings.TrimSpace(opts.SyncMode))
	if opts.SyncMode != "oneshot" && opts.SyncMode != "poll" && opts.SyncMode != "watch" {
		return fmt.Errorf("--sync-mode must be oneshot, poll, or watch")
	}

	opts.RoutingMode = strings.ToLower(strings.TrimSpace(opts.RoutingMode))
	if opts.RoutingMode != "" && opts.RoutingMode != routingNative && opts.RoutingMode != routingEncap {
		return fmt.Errorf("--routing-mode must be %q or %q", routingNative, routingEncap)
	}

	if strings.TrimSpace(opts.ExternalIface) == "" {
		return fmt.Errorf("--external-iface is required")
	}
	if strings.TrimSpace(opts.LocalDeliveryIface) == "" {
		return fmt.Errorf("--local-delivery-iface is required")
	}
	if opts.RoutingMode == routingEncap && strings.TrimSpace(opts.RemoteDeliveryIface) == "" {
		return fmt.Errorf("--remote-delivery-iface is required when routing-mode=encap")
	}

	opts.PollInterval = durationFromSeconds(pollInterval)
	opts.WatchDebounce = durationFromSeconds(watchDebounce)
	if opts.WatchDebounce <= 0 {
		opts.WatchDebounce = defaultDebounce
	}
	if opts.PollInterval <= 0 {
		opts.PollInterval = 5 * time.Second
	}
	if opts.CTEntryTimeout < 0 {
		return fmt.Errorf("--ct-entry-timeout must be >= 0")
	}
	if opts.CTGCInterval < 0 {
		return fmt.Errorf("--ct-gc-interval must be >= 0")
	}
	opts.TelemetryFormat = strings.ToLower(strings.TrimSpace(opts.TelemetryFormat))
	if opts.TelemetryEnable {
		if opts.TelemetryWindow <= 0 {
			return fmt.Errorf("--telemetry-window must be > 0 when telemetry is enabled")
		}
		if opts.TelemetryFormat == "" {
			opts.TelemetryFormat = defaultTelemetryFormat
		}
		if opts.TelemetryFormat != defaultTelemetryFormat {
			return fmt.Errorf("--telemetry-format must be %q", defaultTelemetryFormat)
		}
		if strings.TrimSpace(opts.TelemetryOutput) == "" {
			return fmt.Errorf("--telemetry-output is required when telemetry is enabled")
		}
		if strings.TrimSpace(opts.TelemetryExperiment) == "" {
			return fmt.Errorf("--telemetry-experiment-id is required when telemetry is enabled")
		}
	}

	if opts.ClearTargetState {
		if opts.SyncMode != "oneshot" {
			return fmt.Errorf("--clear-target-state requires --sync-mode=oneshot")
		}
		if strings.TrimSpace(opts.ServiceSelector) == "" {
			return fmt.Errorf("--clear-target-state requires --service namespace/name")
		}
	}

	return nil
}

func durationFromSeconds(seconds float64) time.Duration {
	return time.Duration(seconds * float64(time.Second))
}

func parseServiceSelector(raw string) (*serviceSelector, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, nil
	}
	parts := strings.SplitN(raw, "/", 2)
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" {
		return nil, fmt.Errorf("--service must use namespace/name")
	}
	return &serviceSelector{Namespace: parts[0], Name: parts[1]}, nil
}

func resolveTelemetrySelector(opts options) (*serviceSelector, error) {
	if !opts.TelemetryEnable {
		return nil, nil
	}

	rawService := strings.TrimSpace(opts.ServiceSelector)
	rawTelemetry := strings.TrimSpace(opts.TelemetryService)
	if rawService != "" && rawTelemetry != "" && rawService != rawTelemetry {
		return nil, fmt.Errorf("--service and --telemetry-service must match when both are set")
	}
	if rawTelemetry == "" {
		rawTelemetry = rawService
	}
	if rawTelemetry == "" {
		return nil, fmt.Errorf("--telemetry-service or --service is required when telemetry is enabled")
	}
	return parseServiceSelector(rawTelemetry)
}

func loadKubeConfig() (*rest.Config, error) {
	config, err := rest.InClusterConfig()
	if err == nil {
		return config, nil
	}

	loadingRules := clientcmd.NewDefaultClientConfigLoadingRules()
	overrides := &clientcmd.ConfigOverrides{}
	return clientcmd.NewNonInteractiveDeferredLoadingClientConfig(loadingRules, overrides).ClientConfig()
}

func openPinnedMaps(opts options) (*pinnedMaps, error) {
	m := &pinnedMaps{
		dryRun:        opts.DryRun,
		serviceMapPin: opts.ServiceMapPin,
		backendMapPin: opts.BackendMapPin,
		rrStateMapPin: opts.RRStateMapPin,
		configMapPin:  opts.ConfigMapPin,
		ctMapPin:      opts.CTMapPin,
		fwdCTMapPin:   opts.FwdCTMapPin,
		statsMapPin:   opts.StatsMapPin,
	}
	if opts.DryRun {
		return m, nil
	}

	var err error
	if m.serviceMap, err = ebpf.LoadPinnedMap(opts.ServiceMapPin, nil); err != nil {
		return nil, fmt.Errorf("open service map: %w", err)
	}
	if m.backendMap, err = ebpf.LoadPinnedMap(opts.BackendMapPin, nil); err != nil {
		m.Close()
		return nil, fmt.Errorf("open backend map: %w", err)
	}
	if m.rrStateMap, err = ebpf.LoadPinnedMap(opts.RRStateMapPin, nil); err != nil {
		m.Close()
		return nil, fmt.Errorf("open rr-state map: %w", err)
	}
	if m.configMap, err = ebpf.LoadPinnedMap(opts.ConfigMapPin, nil); err != nil {
		m.Close()
		return nil, fmt.Errorf("open config map: %w", err)
	}
	if m.ctMap, err = ebpf.LoadPinnedMap(opts.CTMapPin, nil); err != nil {
		m.Close()
		return nil, fmt.Errorf("open conntrack map: %w", err)
	}
	if m.fwdCTMap, err = ebpf.LoadPinnedMap(opts.FwdCTMapPin, nil); err != nil {
		m.Close()
		return nil, fmt.Errorf("open forward conntrack map: %w", err)
	}
	if m.statsMap, err = ebpf.LoadPinnedMap(opts.StatsMapPin, nil); err != nil {
		m.Close()
		return nil, fmt.Errorf("open stats map: %w", err)
	}
	if m.possibleCPUs, err = ebpf.PossibleCPU(); err != nil {
		m.Close()
		return nil, fmt.Errorf("detect possible cpus: %w", err)
	}
	return m, nil
}

func (m *pinnedMaps) Close() {
	if m.serviceMap != nil {
		_ = m.serviceMap.Close()
	}
	if m.backendMap != nil {
		_ = m.backendMap.Close()
	}
	if m.rrStateMap != nil {
		_ = m.rrStateMap.Close()
	}
	if m.configMap != nil {
		_ = m.configMap.Close()
	}
	if m.ctMap != nil {
		_ = m.ctMap.Close()
	}
	if m.fwdCTMap != nil {
		_ = m.fwdCTMap.Close()
	}
	if m.statsMap != nil {
		_ = m.statsMap.Close()
	}
}

func runSteadyState(ctx context.Context, ctrl *controller) error {
	switch ctrl.opts.SyncMode {
	case "oneshot":
		return nil
	case "poll", "watch":
	default:
		return fmt.Errorf("unsupported sync mode: %s", ctrl.opts.SyncMode)
	}

	workerCount := 1
	errCh := make(chan error, 2)
	go func() {
		switch ctrl.opts.SyncMode {
		case "poll":
			errCh <- ctrl.runPoll(ctx)
		case "watch":
			errCh <- ctrl.runWatch(ctx)
		}
	}()

	if ctrl.conntrackGCEnabled() {
		workerCount++
		go func() {
			errCh <- ctrl.runConntrackGC(ctx)
		}()
	}
	if ctrl.opts.TelemetryEnable {
		workerCount++
		go func() {
			errCh <- ctrl.runTelemetry(ctx)
		}()
	}

	for i := 0; i < workerCount; i++ {
		if err := <-errCh; err != nil {
			return err
		}
	}
	return nil
}

func (m *pinnedMaps) UpdateConfig(cfg deliveryConfig) error {
	log.Printf(
		"updating delivery config: external=%d local=%d remote=%d mode=%d",
		cfg.ExternalIfindex,
		cfg.LocalDeliveryIfindex,
		cfg.RemoteDeliveryIfindex,
		cfg.RoutingMode,
	)
	if m.dryRun {
		return nil
	}

	var key uint32
	return m.configMap.Update(key, cfg, ebpf.UpdateAny)
}

func (m *pinnedMaps) UpsertEntry(entry nodePortEntry) error {
	frontendKey, err := marshalFrontendKey(entry.NodeIP, entry.ID.NodePort)
	if err != nil {
		return err
	}
	serviceValue, err := marshalServiceValue(entry.SNATIP, len(entry.Backends))
	if err != nil {
		return err
	}

	log.Printf(
		"upsert service: %s/%s:%d frontend=%s:%d backends=%d",
		entry.ID.Namespace,
		entry.ID.Name,
		entry.ID.NodePort,
		entry.NodeIP,
		entry.ID.NodePort,
		len(entry.Backends),
	)

	if !m.dryRun {
		if err := m.serviceMap.Update(frontendKey, serviceValue, ebpf.UpdateAny); err != nil {
			return fmt.Errorf("update service map for %s/%s:%d: %w", entry.ID.Namespace, entry.ID.Name, entry.ID.NodePort, err)
		}
	}

	for index, backend := range entry.Backends {
		backendKey := nodePortBackendKey{
			Service: frontendKey,
			Slot:    uint32(index),
		}
		backendValue, err := marshalBackendValue(backend)
		if err != nil {
			return err
		}
		if !m.dryRun {
			if err := m.backendMap.Update(backendKey, backendValue, ebpf.UpdateAny); err != nil {
				return fmt.Errorf(
					"update backend map for %s/%s:%d slot=%d: %w",
					entry.ID.Namespace,
					entry.ID.Name,
					entry.ID.NodePort,
					index,
					err,
				)
			}
		}
	}
	return nil
}

func (m *pinnedMaps) DeleteEntry(entry nodePortEntry) error {
	frontendKey, err := marshalFrontendKey(entry.NodeIP, entry.ID.NodePort)
	if err != nil {
		return err
	}

	log.Printf(
		"delete service: %s/%s:%d frontend=%s:%d",
		entry.ID.Namespace,
		entry.ID.Name,
		entry.ID.NodePort,
		entry.NodeIP,
		entry.ID.NodePort,
	)

	if m.dryRun {
		return nil
	}

	for index := range entry.Backends {
		backendKey := nodePortBackendKey{
			Service: frontendKey,
			Slot:    uint32(index),
		}
		if err := deleteKey(m.backendMap, backendKey); err != nil {
			return fmt.Errorf(
				"delete backend map for %s/%s:%d slot=%d: %w",
				entry.ID.Namespace,
				entry.ID.Name,
				entry.ID.NodePort,
				index,
				err,
			)
		}
	}

	if err := deleteKey(m.serviceMap, frontendKey); err != nil {
		return fmt.Errorf("delete service map for %s/%s:%d: %w", entry.ID.Namespace, entry.ID.Name, entry.ID.NodePort, err)
	}
	if err := deleteKey(m.rrStateMap, frontendKey); err != nil {
		return fmt.Errorf("delete rr-state map for %s/%s:%d: %w", entry.ID.Namespace, entry.ID.Name, entry.ID.NodePort, err)
	}
	return nil
}

func deleteKey[T any](m *ebpf.Map, key T) error {
	err := m.Delete(key)
	if err == nil || errors.Is(err, ebpf.ErrKeyNotExist) {
		return nil
	}
	return err
}

func marshalFrontendKey(nodeIP string, nodePort int32) (nodePortKey, error) {
	ipBytes, err := ipv4Bytes(nodeIP)
	if err != nil {
		return nodePortKey{}, fmt.Errorf("marshal frontend ip %s: %w", nodeIP, err)
	}
	portBytes, err := portBytes(nodePort)
	if err != nil {
		return nodePortKey{}, fmt.Errorf("marshal frontend port %d: %w", nodePort, err)
	}
	return nodePortKey{
		Address: ipBytes,
		Port:    portBytes,
		Proto:   protoTCP,
	}, nil
}

func marshalServiceValue(snatIP string, backendCount int) (nodePortValue, error) {
	ipBytes, err := ipv4Bytes(snatIP)
	if err != nil {
		return nodePortValue{}, fmt.Errorf("marshal snat ip %s: %w", snatIP, err)
	}
	return nodePortValue{
		BackendCount: uint32(backendCount),
		SNATIP:       ipBytes,
	}, nil
}

func marshalBackendValue(backend backend) (nodePortBackendValue, error) {
	address, err := ipv4Bytes(backend.Address)
	if err != nil {
		return nodePortBackendValue{}, fmt.Errorf("marshal backend ip %s: %w", backend.Address, err)
	}
	nodeIP, err := ipv4Bytes(backend.NodeIP)
	if err != nil {
		return nodePortBackendValue{}, fmt.Errorf("marshal backend node ip %s: %w", backend.NodeIP, err)
	}
	port, err := portBytes(backend.Port)
	if err != nil {
		return nodePortBackendValue{}, fmt.Errorf("marshal backend port %d: %w", backend.Port, err)
	}
	return nodePortBackendValue{
		Address: address,
		Port:    port,
		NodeIP:  nodeIP,
	}, nil
}

func ipv4Bytes(value string) ([4]byte, error) {
	var result [4]byte
	ip := net.ParseIP(strings.TrimSpace(value)).To4()
	if ip == nil {
		return result, fmt.Errorf("not an IPv4 address")
	}
	copy(result[:], ip)
	return result, nil
}

func portBytes(value int32) ([2]byte, error) {
	var result [2]byte
	if value < 1 || value > 65535 {
		return result, fmt.Errorf("port out of range")
	}
	result[0] = byte((value >> 8) & 0xff)
	result[1] = byte(value & 0xff)
	return result, nil
}

func (c *controller) enqueue(reason string) {
	select {
	case c.triggerCh <- reason:
	default:
	}
}

func (c *controller) runPoll(ctx context.Context) error {
	ticker := time.NewTicker(c.opts.PollInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			if err := c.reconcile("poll reconcile"); err != nil {
				return err
			}
		}
	}
}

func (c *controller) runWatch(ctx context.Context) error {
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-c.triggerCh:
			eventCount := 1
			timer := time.NewTimer(c.opts.WatchDebounce)
		debounce:
			for {
				select {
				case <-ctx.Done():
					if !timer.Stop() {
						<-timer.C
					}
					return nil
				case <-timer.C:
					break debounce
				case <-c.triggerCh:
					eventCount++
				}
			}
			log.Printf("watch: received %d event(s); reconciling", eventCount)
			if err := c.reconcile("watch reconcile"); err != nil {
				return err
			}
		}
	}
}

func (c *controller) clearTargetState(reason string) error {
	if !c.opts.ClearTargetState {
		return nil
	}

	desiredEntries, err := c.desiredEntries()
	if err != nil {
		return err
	}
	if len(desiredEntries) == 0 {
		return fmt.Errorf("%s: no desired target entries found", reason)
	}

	ids := make([]serviceID, 0, len(desiredEntries))
	for id := range desiredEntries {
		ids = append(ids, id)
	}
	sortServiceIDs(ids)

	for _, id := range ids {
		if err := c.maps.DeleteEntry(desiredEntries[id]); err != nil {
			return err
		}
	}

	log.Printf("%s: cleared %d target entrie(s)", reason, len(ids))
	return nil
}

func (c *controller) reconcile(reason string) error {
	config, err := c.currentDeliveryConfig()
	if err != nil {
		return err
	}
	c.stateMu.RLock()
	appliedConfig := c.appliedConfig
	appliedEntries := cloneAppliedEntries(c.appliedEntries)
	c.stateMu.RUnlock()

	if appliedConfig == nil || *appliedConfig != config {
		log.Printf("%s: syncing delivery config", reason)
		if err := c.maps.UpdateConfig(config); err != nil {
			return err
		}
		cfg := config
		c.stateMu.Lock()
		c.appliedConfig = &cfg
		c.stateMu.Unlock()
	}

	desiredEntries, err := c.desiredEntries()
	if err != nil {
		return err
	}

	removedIDs := sortedRemovedIDs(appliedEntries, desiredEntries)
	upsertIDs := sortedChangedIDs(appliedEntries, desiredEntries)
	unchanged := 0
	for id, entry := range desiredEntries {
		if old, ok := appliedEntries[id]; ok && old.equal(entry) {
			unchanged++
		}
	}

	for _, id := range removedIDs {
		if err := c.maps.DeleteEntry(appliedEntries[id]); err != nil {
			return err
		}
	}

	for _, id := range upsertIDs {
		if old, ok := appliedEntries[id]; ok {
			if err := c.maps.DeleteEntry(old); err != nil {
				return err
			}
		}
		if err := c.maps.UpsertEntry(desiredEntries[id]); err != nil {
			return err
		}
	}

	totalBackends := 0
	for _, entry := range desiredEntries {
		totalBackends += len(entry.Backends)
	}

	log.Printf(
		"%s: services=%d backends=%d upserted=%d removed=%d unchanged=%d",
		reason,
		len(desiredEntries),
		totalBackends,
		len(upsertIDs),
		len(removedIDs),
		unchanged,
	)

	c.noteReconcile(len(upsertIDs), len(removedIDs))
	c.stateMu.Lock()
	c.appliedEntries = desiredEntries
	c.stateMu.Unlock()
	return nil
}

func sortedRemovedIDs(oldEntries, newEntries map[serviceID]nodePortEntry) []serviceID {
	var ids []serviceID
	for id := range oldEntries {
		if _, ok := newEntries[id]; !ok {
			ids = append(ids, id)
		}
	}
	sortServiceIDs(ids)
	return ids
}

func sortedChangedIDs(oldEntries, newEntries map[serviceID]nodePortEntry) []serviceID {
	var ids []serviceID
	for id, entry := range newEntries {
		oldEntry, ok := oldEntries[id]
		if !ok || !oldEntry.equal(entry) {
			ids = append(ids, id)
		}
	}
	sortServiceIDs(ids)
	return ids
}

func cloneAppliedEntries(entries map[serviceID]nodePortEntry) map[serviceID]nodePortEntry {
	cloned := make(map[serviceID]nodePortEntry, len(entries))
	for id, entry := range entries {
		backends := append([]backend(nil), entry.Backends...)
		entry.Backends = backends
		cloned[id] = entry
	}
	return cloned
}

func sortServiceIDs(ids []serviceID) {
	sort.Slice(ids, func(i, j int) bool {
		if ids[i].Namespace != ids[j].Namespace {
			return ids[i].Namespace < ids[j].Namespace
		}
		if ids[i].Name != ids[j].Name {
			return ids[i].Name < ids[j].Name
		}
		return ids[i].NodePort < ids[j].NodePort
	})
}

func (c *controller) currentDeliveryConfig() (deliveryConfig, error) {
	externalIfindex, err := ifaceIndex(c.opts.ExternalIface)
	if err != nil {
		return deliveryConfig{}, fmt.Errorf("resolve external iface %s: %w", c.opts.ExternalIface, err)
	}
	localIfindex, err := ifaceIndex(c.opts.LocalDeliveryIface)
	if err != nil {
		return deliveryConfig{}, fmt.Errorf("resolve local delivery iface %s: %w", c.opts.LocalDeliveryIface, err)
	}

	var remoteIfindex uint32
	if strings.TrimSpace(c.opts.RemoteDeliveryIface) != "" {
		remoteIfindex, err = ifaceIndex(c.opts.RemoteDeliveryIface)
		if err != nil {
			return deliveryConfig{}, fmt.Errorf("resolve remote delivery iface %s: %w", c.opts.RemoteDeliveryIface, err)
		}
	}

	mode := uint32(0)
	if c.opts.RoutingMode == routingEncap {
		mode = 1
	}

	return deliveryConfig{
		ExternalIfindex:       externalIfindex,
		LocalDeliveryIfindex:  localIfindex,
		RemoteDeliveryIfindex: remoteIfindex,
		RoutingMode:           mode,
	}, nil
}

func ifaceIndex(name string) (uint32, error) {
	iface, err := net.InterfaceByName(strings.TrimSpace(name))
	if err != nil {
		return 0, err
	}
	return uint32(iface.Index), nil
}

func detectIfaceIPv4(ifaceName string) (string, error) {
	iface, err := net.InterfaceByName(strings.TrimSpace(ifaceName))
	if err != nil {
		return "", err
	}
	addrs, err := iface.Addrs()
	if err != nil {
		return "", err
	}
	for _, addr := range addrs {
		var ip net.IP
		switch value := addr.(type) {
		case *net.IPNet:
			ip = value.IP
		case *net.IPAddr:
			ip = value.IP
		}
		if v4 := ip.To4(); v4 != nil {
			return v4.String(), nil
		}
	}
	return "", fmt.Errorf("unable to detect IPv4 address")
}

func (c *controller) desiredEntries() (map[serviceID]nodePortEntry, error) {
	services, err := c.serviceLister.List(labels.Everything())
	if err != nil {
		return nil, fmt.Errorf("list services: %w", err)
	}
	nodes, err := c.nodeLister.List(labels.Everything())
	if err != nil {
		return nil, fmt.Errorf("list nodes: %w", err)
	}
	slices, err := c.sliceLister.List(labels.Everything())
	if err != nil {
		return nil, fmt.Errorf("list endpointslices: %w", err)
	}

	c.stateMu.RLock()
	currentNodeName := c.nodeName
	c.stateMu.RUnlock()

	nodeName, err := resolveNodeName(currentNodeName, nodes)
	if err != nil {
		return nil, err
	}
	c.stateMu.Lock()
	c.nodeName = nodeName
	c.stateMu.Unlock()

	nodeIPs := buildNodeInternalIPs(nodes)
	nodeIP := nodeIPs[nodeName]
	if nodeIP == "" {
		return nil, fmt.Errorf("unable to find InternalIP for node %s", nodeName)
	}

	snatIP := c.opts.SNATIP
	if strings.TrimSpace(snatIP) == "" {
		snatIP, err = detectIfaceIPv4(c.opts.SNATIface)
		if err != nil {
			return nil, fmt.Errorf("detect snat ip from %s: %w", c.opts.SNATIface, err)
		}
	}

	sliceIndex := buildSliceIndex(slices)
	entries := make(map[serviceID]nodePortEntry)

	for _, service := range services {
		if c.selector != nil {
			if service.Namespace != c.selector.Namespace || service.Name != c.selector.Name {
				continue
			}
		}
		if service.Spec.Type != corev1.ServiceTypeNodePort {
			continue
		}
		if service.Spec.ExternalTrafficPolicy != "" && service.Spec.ExternalTrafficPolicy != corev1.ServiceExternalTrafficPolicyCluster {
			continue
		}

		ref := serviceRef{Namespace: service.Namespace, Name: service.Name}
		relatedSlices := sliceIndex[ref]
		if len(relatedSlices) == 0 {
			continue
		}

		for _, servicePort := range service.Spec.Ports {
			if servicePort.Protocol != "" && servicePort.Protocol != corev1.ProtocolTCP {
				continue
			}
			if servicePort.NodePort == 0 {
				continue
			}
			backends := collectBackends(servicePort, relatedSlices, nodeIPs)
			if len(backends) == 0 {
				continue
			}

			id := serviceID{
				Namespace: service.Namespace,
				Name:      service.Name,
				NodePort:  servicePort.NodePort,
			}
			entries[id] = nodePortEntry{
				ID:       id,
				NodeIP:   nodeIP,
				SNATIP:   snatIP,
				Backends: backends,
			}
		}
	}

	return entries, nil
}

type serviceRef struct {
	Namespace string
	Name      string
}

func resolveNodeName(current string, nodes []*corev1.Node) (string, error) {
	current = strings.TrimSpace(current)
	if current != "" {
		for _, node := range nodes {
			if node.Name == current {
				return current, nil
			}
		}
		return "", fmt.Errorf("unable to find node in cache: %s", current)
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

func buildNodeInternalIPs(nodes []*corev1.Node) map[string]string {
	result := make(map[string]string, len(nodes))
	for _, node := range nodes {
		for _, address := range node.Status.Addresses {
			if address.Type == corev1.NodeInternalIP && net.ParseIP(address.Address).To4() != nil {
				result[node.Name] = address.Address
				break
			}
		}
	}
	return result
}

func buildSliceIndex(slices []*discoveryv1.EndpointSlice) map[serviceRef][]*discoveryv1.EndpointSlice {
	index := make(map[serviceRef][]*discoveryv1.EndpointSlice)
	for _, slice := range slices {
		serviceName := slice.Labels[discoveryv1.LabelServiceName]
		if slice.Namespace == "" || serviceName == "" {
			continue
		}
		ref := serviceRef{Namespace: slice.Namespace, Name: serviceName}
		index[ref] = append(index[ref], slice)
	}
	return index
}

func collectBackends(
	servicePort corev1.ServicePort,
	slices []*discoveryv1.EndpointSlice,
	nodeIPs map[string]string,
) []backend {
	seen := make(map[string]struct{})
	var backends []backend

	for _, slice := range slices {
		ports := matchingSlicePorts(servicePort, slice)
		if len(ports) == 0 {
			continue
		}
		for _, endpoint := range slice.Endpoints {
			if !eligibleEndpoint(endpoint) {
				continue
			}
			address := firstIPv4Address(endpoint)
			if address == "" || endpoint.NodeName == nil || *endpoint.NodeName == "" {
				continue
			}
			nodeIP := nodeIPs[*endpoint.NodeName]
			if nodeIP == "" {
				continue
			}
			for _, port := range ports {
				key := fmt.Sprintf("%s:%d", address, *port.Port)
				if _, ok := seen[key]; ok {
					continue
				}
				seen[key] = struct{}{}
				backends = append(backends, backend{
					Address: address,
					Port:    *port.Port,
					NodeIP:  nodeIP,
				})
			}
		}
	}

	sort.Slice(backends, func(i, j int) bool {
		if backends[i].Address != backends[j].Address {
			return backends[i].Address < backends[j].Address
		}
		if backends[i].Port != backends[j].Port {
			return backends[i].Port < backends[j].Port
		}
		return backends[i].NodeIP < backends[j].NodeIP
	})
	return backends
}

func matchingSlicePorts(servicePort corev1.ServicePort, slice *discoveryv1.EndpointSlice) []*discoveryv1.EndpointPort {
	var tcpPorts []*discoveryv1.EndpointPort
	for i := range slice.Ports {
		port := &slice.Ports[i]
		proto := corev1.ProtocolTCP
		if port.Protocol != nil {
			proto = *port.Protocol
		}
		if proto != corev1.ProtocolTCP || port.Port == nil {
			continue
		}
		tcpPorts = append(tcpPorts, port)
	}
	if len(tcpPorts) == 0 {
		return nil
	}

	if servicePort.Name != "" {
		var named []*discoveryv1.EndpointPort
		for _, port := range tcpPorts {
			if port.Name != nil && *port.Name == servicePort.Name {
				named = append(named, port)
			}
		}
		if len(named) > 0 {
			return named
		}
	}

	if servicePort.TargetPort.Type == intstr.Int && servicePort.TargetPort.IntValue() != 0 {
		var matched []*discoveryv1.EndpointPort
		targetPort := int32(servicePort.TargetPort.IntValue())
		for _, port := range tcpPorts {
			if *port.Port == targetPort {
				matched = append(matched, port)
			}
		}
		if len(matched) > 0 {
			return matched
		}
	}

	if len(tcpPorts) == 1 {
		return tcpPorts
	}

	var matched []*discoveryv1.EndpointPort
	for _, port := range tcpPorts {
		if *port.Port == servicePort.Port {
			matched = append(matched, port)
		}
	}
	return matched
}

func eligibleEndpoint(endpoint discoveryv1.Endpoint) bool {
	if endpoint.Conditions.Ready != nil && !*endpoint.Conditions.Ready {
		return false
	}
	if endpoint.Conditions.Serving != nil && !*endpoint.Conditions.Serving {
		return false
	}
	if endpoint.Conditions.Terminating != nil && *endpoint.Conditions.Terminating {
		return false
	}
	if endpoint.TargetRef != nil && endpoint.TargetRef.Kind != "" && endpoint.TargetRef.Kind != "Pod" {
		return false
	}
	return endpoint.NodeName != nil && *endpoint.NodeName != ""
}

func firstIPv4Address(endpoint discoveryv1.Endpoint) string {
	for _, address := range endpoint.Addresses {
		if net.ParseIP(address).To4() != nil {
			return address
		}
	}
	return ""
}

func (e nodePortEntry) equal(other nodePortEntry) bool {
	if e.ID != other.ID || e.NodeIP != other.NodeIP || e.SNATIP != other.SNATIP {
		return false
	}
	if len(e.Backends) != len(other.Backends) {
		return false
	}
	for i := range e.Backends {
		if e.Backends[i] != other.Backends[i] {
			return false
		}
	}
	return true
}

func sameResourceVersion(oldObj, newObj interface{}) bool {
	oldMeta, err := metaAccessor(oldObj)
	if err != nil {
		return false
	}
	newMeta, err := metaAccessor(newObj)
	if err != nil {
		return false
	}
	return oldMeta.GetResourceVersion() == newMeta.GetResourceVersion()
}

func metaAccessor(obj interface{}) (metav1.Object, error) {
	switch value := obj.(type) {
	case metav1.Object:
		return value, nil
	case cache.DeletedFinalStateUnknown:
		if meta, ok := value.Obj.(metav1.Object); ok {
			return meta, nil
		}
		return nil, fmt.Errorf("unsupported tombstone payload")
	default:
		return nil, fmt.Errorf("unsupported object type")
	}
}

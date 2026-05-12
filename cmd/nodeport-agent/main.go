package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"ebpf_nodeport/internal/envdetect"
	"ebpf_nodeport/internal/tcctl"

	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
)

type agentOptions struct {
	NodeName            string
	ExternalIface       string
	LocalDeliveryIface  string
	RemoteDeliveryIface string
	RoutingMode         string
	AttachIface         string
	InnerIfaces         string
	AttachVETHs         bool
	VETHGlob            string
	SetAcceptLocal      bool
	SNATIface           string
	SNATIP              string
	ServiceSelector     string
	SyncMode            string
	SyncPollInterval    string
	ExtraArgs           []string
	BPFCFlags           []string
	PreCleanup          bool
	CleanupOnExit       bool
	SourcePath          string
	ObjectPath          string
	BPFFSRoot           string
	ProgramPinPath      string
	MapDir              string
	ClangPath           string
	SyncProgram         string
	DryRun              bool
}

func main() {
	log.SetFlags(log.LstdFlags)

	opts, err := loadOptions()
	if err != nil {
		log.Fatalf("error: %v", err)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	clientConfig, err := loadKubeConfig()
	if err != nil {
		log.Fatalf("error: %v", err)
	}
	clientset, err := kubernetes.NewForConfig(clientConfig)
	if err != nil {
		log.Fatalf("error: create kubernetes client: %v", err)
	}

	facts, err := envdetect.LoadClusterFacts(ctx, clientset, opts.NodeName, nil)
	if err != nil {
		log.Fatalf("error: %v", err)
	}

	profile, err := envdetect.Detect(envdetect.DetectOptions{
		ExternalIface:       opts.ExternalIface,
		LocalDeliveryIface:  opts.LocalDeliveryIface,
		RemoteDeliveryIface: opts.RemoteDeliveryIface,
		RoutingMode:         opts.RoutingMode,
		AttachIface:         opts.AttachIface,
		InnerIfaces:         opts.InnerIfaces,
		SNATIface:           opts.SNATIface,
	}, facts)
	if err != nil {
		log.Fatalf("error: %v", err)
	}

	log.Printf(
		"resolved delivery profile: external=%s local=%s remote=%s mode=%s",
		profile.ExternalIface,
		profile.LocalDeliveryIface,
		emptyAs(profile.RemoteDeliveryIface, "none"),
		profile.RoutingMode,
	)

	tcConfig := tcctl.Config{
		SourcePath:     opts.SourcePath,
		ObjectPath:     opts.ObjectPath,
		BPFFSRoot:      opts.BPFFSRoot,
		ProgramPinPath: opts.ProgramPinPath,
		MapDir:         opts.MapDir,
		OuterIface:     profile.ExternalIface,
		InnerIfaces:    profile.AttachInnerIfaces,
		AttachVETHs:    opts.AttachVETHs,
		VETHGlob:       opts.VETHGlob,
		SetAcceptLocal: opts.SetAcceptLocal,
		ClangPath:      opts.ClangPath,
		BPFCFlags:      opts.BPFCFlags,
		DryRun:         opts.DryRun,
	}

	cleanup := func() {
		if !opts.CleanupOnExit {
			return
		}
		log.Printf("cleaning up NodePort tc attachment")
		if err := tcctl.Cleanup(tcConfig); err != nil {
			log.Printf("cleanup failed: %v", err)
		}
	}
	defer cleanup()

	if opts.PreCleanup {
		log.Printf("pre-cleaning existing NodePort eBPF state")
		if err := tcctl.Cleanup(tcConfig); err != nil {
			log.Fatalf("error: %v", err)
		}
	}

	log.Printf(
		"attaching NodePort tc program on outer=%s inner=%s attach_veths=%t",
		profile.ExternalIface,
		strings.Join(profile.AttachInnerIfaces, ","),
		opts.AttachVETHs,
	)
	if err := tcctl.Attach(tcConfig); err != nil {
		log.Fatalf("error: %v", err)
	}

	syncArgs := buildSyncArgs(opts, facts.NodeName, profile)
	log.Printf("starting NodePort syncer with mode=%s", opts.SyncMode)
	if err := runSyncer(ctx, opts.SyncProgram, syncArgs); err != nil {
		log.Fatalf("error: %v", err)
	}
}

func loadOptions() (agentOptions, error) {
	var dryRun bool
	flag.BoolVar(&dryRun, "dry-run", envBool("DRY_RUN", false), "log operations without mutating the system")
	flag.Parse()

	execPath, err := os.Executable()
	if err != nil {
		return agentOptions{}, fmt.Errorf("resolve executable path: %w", err)
	}
	binDir := filepath.Dir(execPath)
	rootDir := filepath.Dir(binDir)
	bpffsRoot := envString("BPFFS_ROOT", "/sys/fs/bpf/nodeport_tc")

	return agentOptions{
		NodeName:            envString("NODE_NAME", ""),
		ExternalIface:       envString("NODEPORT_EXTERNAL_IFACE", ""),
		LocalDeliveryIface:  envString("NODEPORT_LOCAL_DELIVERY_IFACE", ""),
		RemoteDeliveryIface: envString("NODEPORT_REMOTE_DELIVERY_IFACE", ""),
		RoutingMode:         envString("NODEPORT_ROUTING_MODE", ""),
		AttachIface:         envString("NODEPORT_ATTACH_IFACE", ""),
		InnerIfaces:         envString("NODEPORT_INNER_IFACES", ""),
		AttachVETHs:         envBool("NODEPORT_ATTACH_VETHS", false),
		VETHGlob:            envString("NODEPORT_VETH_GLOB", "veth*"),
		SetAcceptLocal:      envBool("NODEPORT_SET_ACCEPT_LOCAL", true),
		SNATIface:           envString("NODEPORT_SNAT_IFACE", "cni0"),
		SNATIP:              envString("NODEPORT_SNAT_IP", ""),
		ServiceSelector:     envString("NODEPORT_SERVICE_SELECTOR", ""),
		SyncMode:            envString("NODEPORT_SYNC_MODE", "watch"),
		SyncPollInterval:    envString("NODEPORT_SYNC_POLL_INTERVAL", "5"),
		ExtraArgs:           strings.Fields(envString("NODEPORT_EXTRA_ARGS", "")),
		BPFCFlags:           strings.Fields(envString("BPF_CFLAGS", "")),
		PreCleanup:          envBool("PRE_CLEANUP", true),
		CleanupOnExit:       envBool("CLEANUP_ON_EXIT", true),
		SourcePath:          envString("SRC", filepath.Join(rootDir, "nodeport_tc.c")),
		ObjectPath:          envString("OBJ", "/tmp/nodeport_tc.o"),
		BPFFSRoot:           bpffsRoot,
		ProgramPinPath:      filepath.Join(bpffsRoot, "prog"),
		MapDir:              filepath.Join(bpffsRoot, "maps"),
		ClangPath:           envString("CLANG", "clang"),
		SyncProgram:         envString("SYNC_PROGRAM", filepath.Join(binDir, "nodeport-syncer")),
		DryRun:              dryRun,
	}, nil
}

func buildSyncArgs(opts agentOptions, nodeName string, profile envdetect.Environment) []string {
	args := []string{
		"--sync-mode", opts.SyncMode,
		"--node-name", nodeName,
		"--external-iface", profile.ExternalIface,
		"--local-delivery-iface", profile.LocalDeliveryIface,
		"--routing-mode", profile.RoutingMode,
	}
	if profile.RemoteDeliveryIface != "" {
		args = append(args, "--remote-delivery-iface", profile.RemoteDeliveryIface)
	}
	if opts.ServiceSelector != "" {
		args = append(args, "--service", opts.ServiceSelector)
	}
	if opts.SNATIP != "" {
		args = append(args, "--snat-ip", opts.SNATIP)
	} else {
		args = append(args, "--snat-iface", opts.SNATIface)
	}
	if opts.SyncMode == "poll" {
		args = append(args, "--poll-interval", opts.SyncPollInterval)
	}
	args = append(args, opts.ExtraArgs...)
	if opts.DryRun {
		args = append(args, "--dry-run")
	}
	return args
}

func runSyncer(ctx context.Context, program string, args []string) error {
	if _, err := os.Stat(program); err != nil {
		return fmt.Errorf("missing sync program %s: %w", program, err)
	}

	cmd := exec.CommandContext(ctx, program, args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return nil
		}
		return fmt.Errorf("syncer exited: %w", err)
	}
	return nil
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

func envString(name, fallback string) string {
	if value, ok := os.LookupEnv(name); ok {
		return strings.TrimSpace(value)
	}
	return fallback
}

func envBool(name string, fallback bool) bool {
	value, ok := os.LookupEnv(name)
	if !ok {
		return fallback
	}
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	default:
		return fallback
	}
}

func emptyAs(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

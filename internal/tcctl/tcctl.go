package tcctl

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
)

type Config struct {
	SourcePath     string
	ObjectPath     string
	BPFFSRoot      string
	ProgramPinPath string
	MapDir         string
	OuterIface     string
	InnerIfaces    []string
	AttachVETHs    bool
	VETHGlob       string
	SetAcceptLocal bool
	ClangPath      string
	BPFCFlags      []string
	DryRun         bool
}

func Attach(cfg Config) error {
	if err := requireRoot(cfg.DryRun); err != nil {
		return err
	}
	if cfg.SourcePath == "" {
		return fmt.Errorf("missing source path")
	}
	if _, err := os.Stat(cfg.SourcePath); err != nil {
		return fmt.Errorf("missing source file %s: %w", cfg.SourcePath, err)
	}
	if cfg.ClangPath == "" {
		cfg.ClangPath = "clang"
	}
	if cfg.BPFFSRoot == "" {
		cfg.BPFFSRoot = "/sys/fs/bpf/nodeport_tc"
	}
	if cfg.MapDir == "" {
		cfg.MapDir = filepath.Join(cfg.BPFFSRoot, "maps")
	}
	if cfg.ProgramPinPath == "" {
		cfg.ProgramPinPath = filepath.Join(cfg.BPFFSRoot, "prog")
	}

	if err := ensureBPFSMounted(cfg.DryRun); err != nil {
		return err
	}
	if err := run(cfg.DryRun, "mkdir", "-p", cfg.MapDir); err != nil {
		return err
	}
	if err := run(cfg.DryRun, "rm", "-f", cfg.ProgramPinPath); err != nil {
		return err
	}
	if err := clearMapDir(cfg.MapDir, cfg.DryRun); err != nil {
		return err
	}

	clangArgs := []string{
		"-O2",
		"-g",
		"-target", "bpf",
		"-D__TARGET_ARCH_x86",
		"-I/usr/include/x86_64-linux-gnu",
	}
	clangArgs = append(clangArgs, cfg.BPFCFlags...)
	clangArgs = append(clangArgs, "-c", cfg.SourcePath, "-o", cfg.ObjectPath)
	if err := run(cfg.DryRun, cfg.ClangPath, clangArgs...); err != nil {
		return err
	}
	if err := run(cfg.DryRun, "bpftool", "prog", "load", cfg.ObjectPath, cfg.ProgramPinPath, "type", "classifier", "pinmaps", cfg.MapDir); err != nil {
		return err
	}

	if err := attachIngress(cfg, cfg.OuterIface); err != nil {
		return err
	}
	if err := attachEgress(cfg, cfg.OuterIface); err != nil {
		return err
	}
	for _, iface := range cfg.InnerIfaces {
		if iface == "" {
			continue
		}
		if err := attachIngress(cfg, iface); err != nil {
			return err
		}
	}

	var vethItems []string
	if cfg.AttachVETHs {
		matches, err := filepath.Glob(filepath.Join("/sys/class/net", cfg.VETHGlob))
		if err != nil {
			return fmt.Errorf("glob veth interfaces: %w", err)
		}
		for _, match := range matches {
			iface := filepath.Base(match)
			vethItems = append(vethItems, iface)
			if err := attachIngress(cfg, iface); err != nil {
				return err
			}
		}
	}

	log.Printf("program pinned at: %s", cfg.ProgramPinPath)
	log.Printf("maps pinned under: %s", cfg.MapDir)
	log.Printf("outer iface: %s ingress+egress", cfg.OuterIface)
	log.Printf("inner ingress ifaces: %s", strings.Join(cfg.InnerIfaces, ","))
	if cfg.AttachVETHs {
		if len(vethItems) == 0 {
			log.Printf("veth ingress ifaces: (none)")
		} else {
			log.Printf("veth ingress ifaces: %s", strings.Join(vethItems, ","))
		}
	} else {
		log.Printf("veth ingress ifaces: disabled")
	}
	return nil
}

func Cleanup(cfg Config) error {
	if err := requireRoot(cfg.DryRun); err != nil {
		return err
	}
	if cfg.BPFFSRoot == "" {
		cfg.BPFFSRoot = "/sys/fs/bpf/nodeport_tc"
	}
	if cfg.MapDir == "" {
		cfg.MapDir = filepath.Join(cfg.BPFFSRoot, "maps")
	}
	if cfg.ProgramPinPath == "" {
		cfg.ProgramPinPath = filepath.Join(cfg.BPFFSRoot, "prog")
	}

	if err := deleteIngress(cfg, cfg.OuterIface); err != nil {
		return err
	}
	if err := deleteEgress(cfg, cfg.OuterIface); err != nil {
		return err
	}
	for _, iface := range cfg.InnerIfaces {
		if iface == "" {
			continue
		}
		if err := deleteIngress(cfg, iface); err != nil {
			return err
		}
	}
	if cfg.AttachVETHs {
		matches, err := filepath.Glob(filepath.Join("/sys/class/net", cfg.VETHGlob))
		if err != nil {
			return fmt.Errorf("glob veth interfaces: %w", err)
		}
		for _, match := range matches {
			if err := deleteIngress(cfg, filepath.Base(match)); err != nil {
				return err
			}
		}
	}

	if _, err := os.Stat(cfg.ProgramPinPath); err == nil {
		if err := run(cfg.DryRun, "rm", "-f", cfg.ProgramPinPath); err != nil {
			return err
		}
	}
	if err := clearMapDir(cfg.MapDir, cfg.DryRun); err != nil {
		return err
	}

	log.Printf("cleaned nodeport tc state under: %s", cfg.BPFFSRoot)
	return nil
}

func attachIngress(cfg Config, iface string) error {
	if !ifaceExists(iface) {
		log.Printf("skip missing ingress iface: %s", iface)
		return nil
	}
	if err := run(cfg.DryRun, "tc", "qdisc", "replace", "dev", iface, "clsact"); err != nil {
		return err
	}
	if err := run(cfg.DryRun, "tc", "filter", "replace", "dev", iface, "ingress", "pref", "10", "handle", "10", "bpf", "direct-action", "object-pinned", cfg.ProgramPinPath); err != nil {
		return err
	}
	return setAcceptLocal(iface, cfg)
}

func attachEgress(cfg Config, iface string) error {
	if !ifaceExists(iface) {
		log.Printf("skip missing egress iface: %s", iface)
		return nil
	}
	if err := run(cfg.DryRun, "tc", "qdisc", "replace", "dev", iface, "clsact"); err != nil {
		return err
	}
	if err := run(cfg.DryRun, "tc", "filter", "replace", "dev", iface, "egress", "pref", "10", "handle", "10", "bpf", "direct-action", "object-pinned", cfg.ProgramPinPath); err != nil {
		return err
	}
	return setAcceptLocal(iface, cfg)
}

func deleteIngress(cfg Config, iface string) error {
	if !ifaceExists(iface) {
		return nil
	}
	if err := runAllowError(cfg.DryRun, "tc", "filter", "del", "dev", iface, "ingress", "pref", "10", "handle", "10", "bpf", "direct-action"); err != nil {
		return err
	}
	return nil
}

func deleteEgress(cfg Config, iface string) error {
	if !ifaceExists(iface) {
		return nil
	}
	if err := runAllowError(cfg.DryRun, "tc", "filter", "del", "dev", iface, "egress", "pref", "10", "handle", "10", "bpf", "direct-action"); err != nil {
		return err
	}
	return nil
}

func ensureBPFSMounted(dryRun bool) error {
	mounted, err := isMountpoint("/sys/fs/bpf")
	if err != nil {
		return err
	}
	if mounted {
		return nil
	}
	log.Printf("+ mount -t bpf bpf /sys/fs/bpf")
	if dryRun {
		return nil
	}
	if err := syscall.Mount("bpf", "/sys/fs/bpf", "bpf", 0, ""); err != nil && err != syscall.EBUSY {
		return fmt.Errorf("mount bpffs: %w", err)
	}
	return nil
}

func isMountpoint(path string) (bool, error) {
	data, err := os.ReadFile("/proc/self/mountinfo")
	if err != nil {
		return false, fmt.Errorf("read mountinfo: %w", err)
	}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 5 && fields[4] == path {
			return true, nil
		}
	}
	return false, nil
}

func setAcceptLocal(iface string, cfg Config) error {
	if !cfg.SetAcceptLocal {
		return nil
	}
	procPath := filepath.Join("/proc/sys/net/ipv4/conf", iface, "accept_local")
	if _, err := os.Stat(procPath); err != nil {
		return nil
	}
	log.Printf("+ echo 1 > %s", procPath)
	if cfg.DryRun {
		return nil
	}
	return os.WriteFile(procPath, []byte("1\n"), 0o644)
}

func clearMapDir(dir string, dryRun bool) error {
	entries, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("read map dir %s: %w", dir, err)
	}
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		path := filepath.Join(dir, entry.Name())
		log.Printf("+ rm -f %s", path)
		if dryRun {
			continue
		}
		if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("remove %s: %w", path, err)
		}
	}
	return nil
}

func ifaceExists(name string) bool {
	if strings.TrimSpace(name) == "" {
		return false
	}
	_, err := os.Stat(filepath.Join("/sys/class/net", name))
	return err == nil
}

func requireRoot(dryRun bool) error {
	if dryRun {
		return nil
	}
	if os.Geteuid() != 0 {
		return fmt.Errorf("run as root")
	}
	return nil
}

func run(dryRun bool, name string, args ...string) error {
	log.Printf("+ %s", shellJoin(name, args...))
	if dryRun {
		return nil
	}
	cmd := exec.Command(name, args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func runAllowError(dryRun bool, name string, args ...string) error {
	log.Printf("+ %s", shellJoin(name, args...))
	if dryRun {
		return nil
	}
	cmd := exec.Command(name, args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	err := cmd.Run()
	if err == nil {
		return nil
	}
	var exitErr *exec.ExitError
	if errorsAs(err, &exitErr) {
		return nil
	}
	return err
}

func errorsAs(err error, target interface{}) bool {
	switch v := target.(type) {
	case **exec.ExitError:
		exitErr, ok := err.(*exec.ExitError)
		if !ok {
			return false
		}
		*v = exitErr
		return true
	default:
		return false
	}
}

func shellJoin(name string, args ...string) string {
	parts := make([]string, 0, len(args)+1)
	parts = append(parts, shellQuote(name))
	for _, arg := range args {
		parts = append(parts, shellQuote(arg))
	}
	return strings.Join(parts, " ")
}

func shellQuote(value string) string {
	if value == "" {
		return "''"
	}
	if !strings.ContainsAny(value, " \t\n'\"\\$`!&*()[]{}<>?|;") {
		return value
	}
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}

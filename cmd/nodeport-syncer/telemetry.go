package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/cilium/ebpf"
	corev1 "k8s.io/api/core/v1"
	discoveryv1 "k8s.io/api/discovery/v1"
	"k8s.io/client-go/tools/cache"
)

type telemetryWindowAccumulator struct {
	mu    sync.Mutex
	state telemetryWindowSummary
}

type telemetryWindowSummary struct {
	ServiceEventSeen    int
	SliceEventSeen      int
	NodeEventSeen       int
	SyncReconcileCount  int
	SyncUpsertedEntries int
	SyncRemovedEntries  int
	GCRunsInWindow      int
	GCDeletedCT         int
	GCDeletedFwdCT      int
}

type telemetryCollector struct {
	ctrl             *controller
	writer           *telemetryCSVWriter
	labeler          *telemetryLabeler
	target           string
	nodeName         string
	previousStats    statsSnapshot
	previousBackend  int
	baselineCaptured bool
}

type telemetryTargetState struct {
	NodeIP          string
	ServiceNodePort int32
	BackendTotal    int
	BackendLocal    int
	BackendRemote   int
}

func newTelemetryWindowAccumulator() *telemetryWindowAccumulator {
	return &telemetryWindowAccumulator{}
}

func (a *telemetryWindowAccumulator) noteServiceEvent() {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.state.ServiceEventSeen = 1
}

func (a *telemetryWindowAccumulator) noteSliceEvent() {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.state.SliceEventSeen = 1
}

func (a *telemetryWindowAccumulator) noteNodeEvent() {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.state.NodeEventSeen = 1
}

func (a *telemetryWindowAccumulator) noteReconcile(upserted, removed int) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.state.SyncReconcileCount++
	a.state.SyncUpsertedEntries += upserted
	a.state.SyncRemovedEntries += removed
}

func (a *telemetryWindowAccumulator) noteGC(result conntrackGCResult) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.state.GCRunsInWindow++
	a.state.GCDeletedCT += result.DeletedCT
	a.state.GCDeletedFwdCT += result.DeletedFwd
}

func (a *telemetryWindowAccumulator) consume() telemetryWindowSummary {
	a.mu.Lock()
	defer a.mu.Unlock()
	snapshot := a.state
	a.state = telemetryWindowSummary{}
	return snapshot
}

func (c *controller) noteServiceEvent(obj interface{}) {
	if c.telemetrySel == nil || c.telemetry == nil {
		return
	}
	service := extractServiceObject(obj)
	if service == nil {
		return
	}
	if service.Namespace == c.telemetrySel.Namespace && service.Name == c.telemetrySel.Name {
		c.telemetry.noteServiceEvent()
	}
}

func (c *controller) noteSliceEvent(obj interface{}) {
	if c.telemetrySel == nil || c.telemetry == nil {
		return
	}
	slice := extractEndpointSliceObject(obj)
	if slice == nil {
		return
	}
	if slice.Namespace != c.telemetrySel.Namespace {
		return
	}
	if slice.Labels[discoveryv1.LabelServiceName] != c.telemetrySel.Name {
		return
	}
	c.telemetry.noteSliceEvent()
}

func (c *controller) noteNodeEvent(obj interface{}) {
	if c.telemetry == nil {
		return
	}
	if extractNodeObject(obj) == nil {
		return
	}
	c.telemetry.noteNodeEvent()
}

func (c *controller) noteReconcile(upserted, removed int) {
	if c.telemetry == nil {
		return
	}
	c.telemetry.noteReconcile(upserted, removed)
}

func (c *controller) noteConntrackGC(result conntrackGCResult) {
	if c.telemetry == nil {
		return
	}
	c.telemetry.noteGC(result)
}

func (c *controller) runTelemetry(ctx context.Context) error {
	collector, err := newTelemetryCollector(c)
	if err != nil {
		return err
	}
	defer collector.writer.Close()

	if err := collector.captureBaseline(); err != nil {
		return err
	}

	windowStart := time.Now().UTC()
	ticker := time.NewTicker(c.opts.TelemetryWindow)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return nil
		case tick := <-ticker.C:
			windowEnd := tick.UTC()
			sample, err := collector.collectWindow(windowStart, windowEnd)
			if err != nil {
				failedWindowStart := windowStart
				windowStart = windowEnd
				log.Printf("telemetry: skip window %s-%s: %v", failedWindowStart.Format(time.RFC3339Nano), windowEnd.Format(time.RFC3339Nano), err)
				continue
			}
			if err := collector.writer.WriteSample(sample); err != nil {
				failedWindowStart := windowStart
				windowStart = windowEnd
				log.Printf("telemetry: drop sample for window %s-%s: %v", failedWindowStart.Format(time.RFC3339Nano), windowEnd.Format(time.RFC3339Nano), err)
				continue
			}
			windowStart = windowEnd
		}
	}
}

func newTelemetryCollector(ctrl *controller) (*telemetryCollector, error) {
	nodeName := ctrl.snapshotNodeName()
	if nodeName == "" {
		nodeName = strings.TrimSpace(ctrl.opts.NodeName)
	}
	if nodeName == "" {
		nodeName = "unknown-node"
	}

	outputPath := filepath.Join(ctrl.opts.TelemetryOutput, ctrl.opts.TelemetryExperiment, nodeName+".csv")
	writer, err := newTelemetryCSVWriter(outputPath)
	if err != nil {
		return nil, err
	}

	target := ""
	if ctrl.telemetrySel != nil {
		target = fmt.Sprintf("%s/%s", ctrl.telemetrySel.Namespace, ctrl.telemetrySel.Name)
	}

	return &telemetryCollector{
		ctrl:     ctrl,
		writer:   writer,
		labeler:  newTelemetryLabeler(ctrl.opts.TelemetryEventsFile, target),
		target:   target,
		nodeName: nodeName,
	}, nil
}

func (c *telemetryCollector) captureBaseline() error {
	stats, err := c.ctrl.maps.ReadStatsSnapshot()
	if err != nil {
		return err
	}
	c.previousStats = stats
	c.baselineCaptured = true
	return nil
}

func (c *telemetryCollector) collectWindow(start, end time.Time) (telemetrySample, error) {
	if !c.baselineCaptured {
		if err := c.captureBaseline(); err != nil {
			return telemetrySample{}, err
		}
	}

	stats, err := c.ctrl.maps.ReadStatsSnapshot()
	if err != nil {
		return telemetrySample{}, err
	}
	delta := stats.diff(c.previousStats)
	c.previousStats = stats

	ctCount, err := c.ctrl.maps.CountConntrackEntries()
	if err != nil {
		return telemetrySample{}, err
	}
	fwdCount, err := c.ctrl.maps.CountForwardConntrackEntries()
	if err != nil {
		return telemetrySample{}, err
	}

	windowSummary := c.ctrl.telemetry.consume()
	targetState, err := c.ctrl.currentTelemetryTargetState()
	if err != nil {
		return telemetrySample{}, err
	}
	label, err := c.labeler.LabelWindow(start, end)
	if err != nil {
		return telemetrySample{}, err
	}

	backendDelta := targetState.BackendTotal - c.previousBackend
	c.previousBackend = targetState.BackendTotal

	return telemetrySample{
		WindowStartUnixMS:      start.UnixMilli(),
		WindowEndUnixMS:        end.UnixMilli(),
		WindowSeconds:          c.ctrl.opts.TelemetryWindow.Seconds(),
		ExperimentID:           c.ctrl.opts.TelemetryExperiment,
		NodeName:               c.nodeName,
		NodeIP:                 targetState.NodeIP,
		ServiceNamespace:       c.ctrl.telemetrySel.Namespace,
		ServiceName:            c.ctrl.telemetrySel.Name,
		ServiceNodePort:        int(targetState.ServiceNodePort),
		RoutingMode:            c.ctrl.opts.RoutingMode,
		HasRemoteBackend:       boolToInt(targetState.BackendRemote > 0),
		DeltaTCPPackets:        int64(delta[statTCPPackets]),
		DeltaNodePortHit:       int64(delta[statNodePortHit]),
		DeltaBackendSelected:   int64(delta[statBackendSelected]),
		DeltaBackendLookupMiss: int64(delta[statBackendLookupMiss]),
		DeltaRRUpdate:          int64(delta[statRRUpdate]),
		DeltaSNATInstall:       int64(delta[statSNATInstall]),
		DeltaRequestRewrite:    int64(delta[statRequestRewrite]),
		DeltaRevNATHit:         int64(delta[statRevNATHit]),
		DeltaCTLookupMiss:      int64(delta[statCTLookupMiss]),
		DeltaResponseRewrite:   int64(delta[statResponseRewrite]),
		DeltaFwdCTHit:          int64(delta[statFwdCTHit]),
		DeltaNewConn:           int64(delta[statNewConn]),
		DeltaMapMiss:           int64(delta[statMapMiss]),
		DeltaRewriteFail:       int64(delta[statRewriteFail]),
		DeltaRedirectOK:        int64(delta[statRedirectOK]),
		DeltaRedirectFail:      int64(delta[statRedirectFail]),
		DeltaFallbackPass:      int64(delta[statFallbackPass]),
		CTActiveCount:          ctCount,
		FwdCTActiveCount:       fwdCount,
		GCRunsInWindow:         windowSummary.GCRunsInWindow,
		GCDeletedCT:            windowSummary.GCDeletedCT,
		GCDeletedFwdCT:         windowSummary.GCDeletedFwdCT,
		CTEntryTimeoutSeconds:  int(c.ctrl.opts.CTEntryTimeout.Seconds()),
		CTGCIntervalSeconds:    int(c.ctrl.opts.CTGCInterval.Seconds()),
		BackendTotal:           targetState.BackendTotal,
		BackendLocal:           targetState.BackendLocal,
		BackendRemote:          targetState.BackendRemote,
		BackendTotalDelta:      backendDelta,
		ServiceEventSeen:       windowSummary.ServiceEventSeen,
		SliceEventSeen:         windowSummary.SliceEventSeen,
		NodeEventSeen:          windowSummary.NodeEventSeen,
		SyncReconcileCount:     windowSummary.SyncReconcileCount,
		SyncUpsertedServices:   windowSummary.SyncUpsertedEntries,
		SyncRemovedServices:    windowSummary.SyncRemovedEntries,
		Label:                  label.Label,
		LabelSource:            label.LabelSource,
		AnomalyActive:          label.AnomalyActive,
		RecoveryActive:         label.RecoveryActive,
	}, nil
}

func (c *controller) currentTelemetryTargetState() (telemetryTargetState, error) {
	state := telemetryTargetState{}
	if c.telemetrySel == nil {
		return state, nil
	}

	svc, err := c.serviceLister.Services(c.telemetrySel.Namespace).Get(c.telemetrySel.Name)
	if err == nil {
		state.ServiceNodePort = firstTCPNodePort(svc)
	}

	c.stateMu.RLock()
	entries := cloneAppliedEntries(c.appliedEntries)
	nodeName := c.nodeName
	c.stateMu.RUnlock()

	var matches []nodePortEntry
	for _, entry := range entries {
		if entry.ID.Namespace == c.telemetrySel.Namespace && entry.ID.Name == c.telemetrySel.Name {
			matches = append(matches, entry)
		}
	}
	sort.Slice(matches, func(i, j int) bool {
		return matches[i].ID.NodePort < matches[j].ID.NodePort
	})

	chosen := nodePortEntry{}
	if state.ServiceNodePort != 0 {
		for _, entry := range matches {
			if entry.ID.NodePort == state.ServiceNodePort {
				chosen = entry
				break
			}
		}
	}
	if chosen.ID.NodePort == 0 && len(matches) > 0 {
		chosen = matches[0]
	}
	if chosen.ID.NodePort != 0 {
		state.NodeIP = chosen.NodeIP
		state.ServiceNodePort = chosen.ID.NodePort
		state.BackendTotal = len(chosen.Backends)
		for _, item := range chosen.Backends {
			if item.NodeIP == chosen.NodeIP {
				state.BackendLocal++
			} else {
				state.BackendRemote++
			}
		}
	}
	if state.NodeIP == "" && nodeName != "" {
		node, nodeErr := c.nodeLister.Get(nodeName)
		if nodeErr == nil {
			for _, address := range node.Status.Addresses {
				if address.Type == corev1.NodeInternalIP && net.ParseIP(address.Address).To4() != nil {
					state.NodeIP = address.Address
					break
				}
			}
		}
	}

	return state, nil
}

func (c *controller) snapshotNodeName() string {
	c.stateMu.RLock()
	defer c.stateMu.RUnlock()
	return c.nodeName
}

func firstTCPNodePort(service *corev1.Service) int32 {
	if service == nil {
		return 0
	}
	for _, port := range service.Spec.Ports {
		if port.NodePort == 0 {
			continue
		}
		if port.Protocol != "" && port.Protocol != corev1.ProtocolTCP {
			continue
		}
		return port.NodePort
	}
	return 0
}

func extractServiceObject(obj interface{}) *corev1.Service {
	switch value := obj.(type) {
	case *corev1.Service:
		return value
	case cache.DeletedFinalStateUnknown:
		if service, ok := value.Obj.(*corev1.Service); ok {
			return service
		}
	}
	return nil
}

func extractEndpointSliceObject(obj interface{}) *discoveryv1.EndpointSlice {
	switch value := obj.(type) {
	case *discoveryv1.EndpointSlice:
		return value
	case cache.DeletedFinalStateUnknown:
		if slice, ok := value.Obj.(*discoveryv1.EndpointSlice); ok {
			return slice
		}
	}
	return nil
}

func extractNodeObject(obj interface{}) *corev1.Node {
	switch value := obj.(type) {
	case *corev1.Node:
		return value
	case cache.DeletedFinalStateUnknown:
		if node, ok := value.Obj.(*corev1.Node); ok {
			return node
		}
	}
	return nil
}

func (m *pinnedMaps) ReadStatsSnapshot() (statsSnapshot, error) {
	var snapshot statsSnapshot
	if m.dryRun || m.statsMap == nil {
		return snapshot, nil
	}

	values := make([]uint64, m.possibleCPUs)
	for index := 0; index < statCount; index++ {
		key := uint32(index)
		for i := range values {
			values[i] = 0
		}
		if err := m.statsMap.Lookup(&key, &values); err != nil {
			if errors.Is(err, ebpf.ErrKeyNotExist) {
				continue
			}
			return snapshot, fmt.Errorf("lookup stats index %d: %w", index, err)
		}
		var total uint64
		for _, value := range values {
			total += value
		}
		snapshot[index] = total
	}

	return snapshot, nil
}

func (m *pinnedMaps) CountConntrackEntries() (int, error) {
	if m.dryRun || m.ctMap == nil {
		return 0, nil
	}

	count := 0
	iter := m.ctMap.Iterate()
	var key nodePortCTKey
	var value nodePortCTValue
	for iter.Next(&key, &value) {
		count++
	}
	if err := iter.Err(); err != nil {
		return 0, fmt.Errorf("iterate conntrack map: %w", err)
	}
	return count, nil
}

func (m *pinnedMaps) CountForwardConntrackEntries() (int, error) {
	if m.dryRun || m.fwdCTMap == nil {
		return 0, nil
	}

	count := 0
	iter := m.fwdCTMap.Iterate()
	var key nodePortFwdCTKey
	var value nodePortFwdCTValue
	for iter.Next(&key, &value) {
		count++
	}
	if err := iter.Err(); err != nil {
		return 0, fmt.Errorf("iterate forward conntrack map: %w", err)
	}
	return count, nil
}

func boolToInt(value bool) int {
	if value {
		return 1
	}
	return 0
}

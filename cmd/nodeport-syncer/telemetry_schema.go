package main

import "strconv"

const (
	statTCPPackets = iota
	statNodePortHit
	statBackendSelected
	statBackendLookupMiss
	statRRUpdate
	statSNATInstall
	statRequestRewrite
	statRevNATHit
	statCTLookupMiss
	statResponseRewrite
	statSameNodeSkip
	statFwdCTHit
	statNewConn
	statMapMiss
	statRewriteFail
	statRedirectOK
	statRedirectFail
	statFallbackPass
	statCount
)

type statsSnapshot [statCount]uint64

type telemetrySample struct {
	WindowStartUnixMS      int64
	WindowEndUnixMS        int64
	WindowSeconds          float64
	ExperimentID           string
	NodeName               string
	NodeIP                 string
	ServiceNamespace       string
	ServiceName            string
	ServiceNodePort        int
	RoutingMode            string
	HasRemoteBackend       int
	DeltaTCPPackets        int64
	DeltaNodePortHit       int64
	DeltaBackendSelected   int64
	DeltaBackendLookupMiss int64
	DeltaRRUpdate          int64
	DeltaSNATInstall       int64
	DeltaRequestRewrite    int64
	DeltaRevNATHit         int64
	DeltaCTLookupMiss      int64
	DeltaResponseRewrite   int64
	DeltaFwdCTHit          int64
	DeltaNewConn           int64
	DeltaMapMiss           int64
	DeltaRewriteFail       int64
	DeltaRedirectOK        int64
	DeltaRedirectFail      int64
	DeltaFallbackPass      int64
	CTActiveCount          int
	FwdCTActiveCount       int
	GCRunsInWindow         int
	GCDeletedCT            int
	GCDeletedFwdCT         int
	CTEntryTimeoutSeconds  int
	CTGCIntervalSeconds    int
	BackendTotal           int
	BackendLocal           int
	BackendRemote          int
	BackendTotalDelta      int
	ServiceEventSeen       int
	SliceEventSeen         int
	NodeEventSeen          int
	SyncReconcileCount     int
	SyncUpsertedServices   int
	SyncRemovedServices    int
	Label                  string
	LabelSource            string
	AnomalyActive          int
	RecoveryActive         int
}

func (s statsSnapshot) diff(previous statsSnapshot) statsSnapshot {
	var delta statsSnapshot
	for index := 0; index < statCount; index++ {
		current := s[index]
		prev := previous[index]
		if current >= prev {
			delta[index] = current - prev
			continue
		}
		delta[index] = current
	}
	return delta
}

func telemetryCSVHeader() []string {
	return []string{
		"window_start_unix_ms",
		"window_end_unix_ms",
		"window_seconds",
		"experiment_id",
		"node_name",
		"node_ip",
		"service_namespace",
		"service_name",
		"service_nodeport",
		"routing_mode",
		"has_remote_backend",
		"delta_tcp_packets",
		"delta_nodeport_hit",
		"delta_backend_selected",
		"delta_backend_lookup_miss",
		"delta_rr_update",
		"delta_snat_install",
		"delta_request_rewrite",
		"delta_revnat_hit",
		"delta_ct_lookup_miss",
		"delta_response_rewrite",
		"delta_fwd_ct_hit",
		"delta_new_conn",
		"delta_map_miss",
		"delta_rewrite_fail",
		"delta_redirect_ok",
		"delta_redirect_fail",
		"delta_fallback_pass",
		"ct_active_count",
		"fwd_ct_active_count",
		"gc_runs_in_window",
		"gc_deleted_ct",
		"gc_deleted_fwd_ct",
		"ct_entry_timeout_seconds",
		"ct_gc_interval_seconds",
		"backend_total",
		"backend_local",
		"backend_remote",
		"backend_total_delta",
		"service_event_seen",
		"slice_event_seen",
		"node_event_seen",
		"sync_reconcile_count",
		"sync_upserted_services",
		"sync_removed_services",
		"label",
		"label_source",
		"anomaly_active",
		"recovery_active",
	}
}

func (s telemetrySample) CSVRecord() []string {
	return []string{
		strconv.FormatInt(s.WindowStartUnixMS, 10),
		strconv.FormatInt(s.WindowEndUnixMS, 10),
		strconv.FormatFloat(s.WindowSeconds, 'f', 3, 64),
		s.ExperimentID,
		s.NodeName,
		s.NodeIP,
		s.ServiceNamespace,
		s.ServiceName,
		strconv.Itoa(s.ServiceNodePort),
		s.RoutingMode,
		strconv.Itoa(s.HasRemoteBackend),
		strconv.FormatInt(s.DeltaTCPPackets, 10),
		strconv.FormatInt(s.DeltaNodePortHit, 10),
		strconv.FormatInt(s.DeltaBackendSelected, 10),
		strconv.FormatInt(s.DeltaBackendLookupMiss, 10),
		strconv.FormatInt(s.DeltaRRUpdate, 10),
		strconv.FormatInt(s.DeltaSNATInstall, 10),
		strconv.FormatInt(s.DeltaRequestRewrite, 10),
		strconv.FormatInt(s.DeltaRevNATHit, 10),
		strconv.FormatInt(s.DeltaCTLookupMiss, 10),
		strconv.FormatInt(s.DeltaResponseRewrite, 10),
		strconv.FormatInt(s.DeltaFwdCTHit, 10),
		strconv.FormatInt(s.DeltaNewConn, 10),
		strconv.FormatInt(s.DeltaMapMiss, 10),
		strconv.FormatInt(s.DeltaRewriteFail, 10),
		strconv.FormatInt(s.DeltaRedirectOK, 10),
		strconv.FormatInt(s.DeltaRedirectFail, 10),
		strconv.FormatInt(s.DeltaFallbackPass, 10),
		strconv.Itoa(s.CTActiveCount),
		strconv.Itoa(s.FwdCTActiveCount),
		strconv.Itoa(s.GCRunsInWindow),
		strconv.Itoa(s.GCDeletedCT),
		strconv.Itoa(s.GCDeletedFwdCT),
		strconv.Itoa(s.CTEntryTimeoutSeconds),
		strconv.Itoa(s.CTGCIntervalSeconds),
		strconv.Itoa(s.BackendTotal),
		strconv.Itoa(s.BackendLocal),
		strconv.Itoa(s.BackendRemote),
		strconv.Itoa(s.BackendTotalDelta),
		strconv.Itoa(s.ServiceEventSeen),
		strconv.Itoa(s.SliceEventSeen),
		strconv.Itoa(s.NodeEventSeen),
		strconv.Itoa(s.SyncReconcileCount),
		strconv.Itoa(s.SyncUpsertedServices),
		strconv.Itoa(s.SyncRemovedServices),
		s.Label,
		s.LabelSource,
		strconv.Itoa(s.AnomalyActive),
		strconv.Itoa(s.RecoveryActive),
	}
}

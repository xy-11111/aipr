package main

import (
	"encoding/csv"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestStatsSnapshotDiffHandlesCounterReset(t *testing.T) {
	current := statsSnapshot{
		statNodePortHit: 8,
		statNewConn:     3,
	}
	previous := statsSnapshot{
		statNodePortHit: 5,
		statNewConn:     7,
	}

	delta := current.diff(previous)
	if got, want := delta[statNodePortHit], uint64(3); got != want {
		t.Fatalf("nodeport delta=%d, want %d", got, want)
	}
	if got, want := delta[statNewConn], uint64(3); got != want {
		t.Fatalf("new_conn delta=%d, want reset-to-current value %d", got, want)
	}
}

func TestTelemetryLabelerMapsAnomalyAndRecovery(t *testing.T) {
	dir := t.TempDir()
	eventsPath := filepath.Join(dir, "events.jsonl")
	payload := `{"ts_start_unix_ms":1000,"ts_end_unix_ms":4000,"label":"backend_churn","target":"demo/nodeport","recovery_tail_ms":2000}` + "\n"
	if err := os.WriteFile(eventsPath, []byte(payload), 0o644); err != nil {
		t.Fatalf("write events file: %v", err)
	}

	labeler := newTelemetryLabeler(eventsPath, "demo/nodeport")

	anomaly, err := labeler.LabelWindow(time.UnixMilli(1500), time.UnixMilli(2500))
	if err != nil {
		t.Fatalf("label anomaly window: %v", err)
	}
	if anomaly.Label != "backend_churn" || anomaly.AnomalyActive != 1 || anomaly.RecoveryActive != 0 {
		t.Fatalf("unexpected anomaly label: %#v", anomaly)
	}

	recovery, err := labeler.LabelWindow(time.UnixMilli(4500), time.UnixMilli(5000))
	if err != nil {
		t.Fatalf("label recovery window: %v", err)
	}
	if recovery.Label != "backend_churn" || recovery.AnomalyActive != 0 || recovery.RecoveryActive != 1 {
		t.Fatalf("unexpected recovery label: %#v", recovery)
	}

	normal, err := labeler.LabelWindow(time.UnixMilli(8000), time.UnixMilli(9000))
	if err != nil {
		t.Fatalf("label normal window: %v", err)
	}
	if normal.Label != "normal" || normal.LabelSource != "derived" {
		t.Fatalf("unexpected normal label: %#v", normal)
	}
}

func TestTelemetryCSVWriterWritesHeaderAndSample(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "node.csv")

	writer, err := newTelemetryCSVWriter(path)
	if err != nil {
		t.Fatalf("create writer: %v", err)
	}
	sample := telemetrySample{
		WindowStartUnixMS: 1000,
		WindowEndUnixMS:   2000,
		WindowSeconds:     1,
		ExperimentID:      "exp-1",
		NodeName:          "node-a",
		ServiceNamespace:  "demo",
		ServiceName:       "nodeport",
		ServiceNodePort:   30080,
		RoutingMode:       routingEncap,
		Label:             "normal",
		LabelSource:       "derived",
	}
	if err := writer.WriteSample(sample); err != nil {
		t.Fatalf("write sample: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("close writer: %v", err)
	}

	file, err := os.Open(path)
	if err != nil {
		t.Fatalf("open csv file: %v", err)
	}
	defer file.Close()

	records, err := csv.NewReader(file).ReadAll()
	if err != nil {
		t.Fatalf("read csv records: %v", err)
	}
	if len(records) != 2 {
		t.Fatalf("record count=%d, want 2", len(records))
	}
	if len(records[0]) != len(telemetryCSVHeader()) {
		t.Fatalf("header field count=%d, want %d", len(records[0]), len(telemetryCSVHeader()))
	}
	if len(records[1]) != len(telemetryCSVHeader()) {
		t.Fatalf("sample field count=%d, want %d", len(records[1]), len(telemetryCSVHeader()))
	}
	if got, want := records[1][3], "exp-1"; got != want {
		t.Fatalf("experiment_id=%q, want %q", got, want)
	}
}

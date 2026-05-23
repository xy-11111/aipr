package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"
)

type telemetryEventManifest struct {
	StartUnixMS    int64  `json:"ts_start_unix_ms"`
	EndUnixMS      int64  `json:"ts_end_unix_ms"`
	Label          string `json:"label"`
	Scope          string `json:"scope"`
	Target         string `json:"target"`
	RecoveryTailMS int64  `json:"recovery_tail_ms"`
}

type telemetryWindowLabel struct {
	Label          string
	LabelSource    string
	AnomalyActive  int
	RecoveryActive int
}

type telemetryLabeler struct {
	eventsFile string
	target     string
}

func newTelemetryLabeler(eventsFile, target string) *telemetryLabeler {
	return &telemetryLabeler{
		eventsFile: strings.TrimSpace(eventsFile),
		target:     strings.TrimSpace(target),
	}
}

func (l *telemetryLabeler) LabelWindow(start, end time.Time) (telemetryWindowLabel, error) {
	defaultLabel := telemetryWindowLabel{
		Label:       "normal",
		LabelSource: "derived",
	}
	if l.eventsFile == "" {
		return defaultLabel, nil
	}

	events, err := l.loadEvents()
	if err != nil {
		return telemetryWindowLabel{}, err
	}

	windowStart := start.UnixMilli()
	windowEnd := end.UnixMilli()
	for _, event := range events {
		if event.Target != "" && l.target != "" && event.Target != l.target {
			continue
		}
		if rangesOverlap(windowStart, windowEnd, event.StartUnixMS, event.EndUnixMS) {
			return telemetryWindowLabel{
				Label:         event.Label,
				LabelSource:   "event_manifest",
				AnomalyActive: 1,
			}, nil
		}
		recoveryEnd := event.EndUnixMS + event.RecoveryTailMS
		if event.RecoveryTailMS > 0 && rangesOverlap(windowStart, windowEnd, event.EndUnixMS, recoveryEnd) {
			return telemetryWindowLabel{
				Label:          event.Label,
				LabelSource:    "event_manifest",
				RecoveryActive: 1,
			}, nil
		}
	}

	return defaultLabel, nil
}

func (l *telemetryLabeler) loadEvents() ([]telemetryEventManifest, error) {
	file, err := os.Open(l.eventsFile)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("open telemetry events file %s: %w", l.eventsFile, err)
	}
	defer file.Close()

	var events []telemetryEventManifest
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var event telemetryEventManifest
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			continue
		}
		if event.Label == "" {
			continue
		}
		if event.EndUnixMS <= event.StartUnixMS {
			continue
		}
		events = append(events, event)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("scan telemetry events file %s: %w", l.eventsFile, err)
	}
	return events, nil
}

func rangesOverlap(startA, endA, startB, endB int64) bool {
	return startA < endB && endA > startB
}

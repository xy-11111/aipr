package main

import (
	"strings"
	"testing"
	"time"
)

func validOptionsForTest() options {
	return options{
		ServiceSelector:     "demo/nodeport",
		SNATIface:           "cni0",
		ExternalIface:       "eth0",
		LocalDeliveryIface:  "cni0",
		RemoteDeliveryIface: "flannel.1",
		RoutingMode:         routingEncap,
		SyncMode:            "watch",
		PollInterval:        5 * time.Second,
		WatchDebounce:       200 * time.Millisecond,
		CTEntryTimeout:      time.Minute,
		CTGCInterval:        30 * time.Second,
		TelemetryFormat:     defaultTelemetryFormat,
	}
}

func TestValidateOptionsClearTargetStateRequiresOneshot(t *testing.T) {
	opts := validOptionsForTest()
	opts.ClearTargetState = true

	err := validateOptions(&opts, 5, 0.2)
	if err == nil || !strings.Contains(err.Error(), "--sync-mode=oneshot") {
		t.Fatalf("validateOptions error=%v, want clear-target-state/oneshot failure", err)
	}
}

func TestValidateOptionsClearTargetStateRequiresService(t *testing.T) {
	opts := validOptionsForTest()
	opts.SyncMode = "oneshot"
	opts.ServiceSelector = ""
	opts.ClearTargetState = true

	err := validateOptions(&opts, 5, 0.2)
	if err == nil || !strings.Contains(err.Error(), "--service") {
		t.Fatalf("validateOptions error=%v, want clear-target-state/service failure", err)
	}
}

func TestValidateOptionsAllowsClearTargetStateForOneshotService(t *testing.T) {
	opts := validOptionsForTest()
	opts.SyncMode = "oneshot"
	opts.ClearTargetState = true

	if err := validateOptions(&opts, 5, 0.2); err != nil {
		t.Fatalf("validateOptions error=%v, want success", err)
	}
}

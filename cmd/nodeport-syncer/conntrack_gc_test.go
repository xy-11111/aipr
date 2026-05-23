package main

import (
	"testing"
	"time"
	"unsafe"
)

func TestBuildConntrackGCPlan(t *testing.T) {
	activeCTKey := nodePortCTKey{
		BackendIP:   [4]byte{10, 0, 0, 2},
		NodeIP:      [4]byte{10, 0, 0, 1},
		BackendPort: [2]byte{0x1f, 0x90},
		SNATPort:    [2]byte{0x9c, 0x40},
		Proto:       protoTCP,
	}
	activeCTValue := nodePortCTValue{
		ClientIP:     [4]byte{192, 168, 1, 10},
		FrontendIP:   [4]byte{192, 168, 1, 201},
		ClientPort:   [2]byte{0x30, 0x39},
		FrontendPort: [2]byte{0x75, 0x70},
		LastSeenNS:   95,
	}
	staleCTKey := nodePortCTKey{
		BackendIP:   [4]byte{10, 0, 0, 3},
		NodeIP:      [4]byte{10, 0, 0, 1},
		BackendPort: [2]byte{0x1f, 0x90},
		SNATPort:    [2]byte{0x9c, 0x41},
		Proto:       protoTCP,
	}
	staleCTValue := nodePortCTValue{
		ClientIP:     [4]byte{192, 168, 1, 11},
		FrontendIP:   [4]byte{192, 168, 1, 202},
		ClientPort:   [2]byte{0x30, 0x3a},
		FrontendPort: [2]byte{0x75, 0x70},
		LastSeenNS:   10,
	}
	orphanFwdKey := nodePortFwdCTKey{
		ClientIP:     [4]byte{192, 168, 1, 12},
		FrontendIP:   [4]byte{192, 168, 1, 203},
		ClientPort:   [2]byte{0x30, 0x3b},
		FrontendPort: [2]byte{0x75, 0x70},
		Proto:        protoTCP,
	}

	plan := buildConntrackGCPlan(
		100,
		30*time.Nanosecond,
		map[nodePortCTKey]nodePortCTValue{
			activeCTKey: activeCTValue,
			staleCTKey:  staleCTValue,
		},
		[]nodePortFwdCTKey{
			forwardKeyFromCT(activeCTKey, activeCTValue),
			forwardKeyFromCT(staleCTKey, staleCTValue),
			orphanFwdKey,
		},
	)

	if plan.activeCTCount != 1 {
		t.Fatalf("activeCTCount=%d, want 1", plan.activeCTCount)
	}
	if plan.activeFwdCount != 1 {
		t.Fatalf("activeFwdCount=%d, want 1", plan.activeFwdCount)
	}
	if len(plan.staleCT) != 1 || plan.staleCT[0] != staleCTKey {
		t.Fatalf("staleCT=%v, want [%v]", plan.staleCT, staleCTKey)
	}

	staleFwd := make(map[nodePortFwdCTKey]struct{}, len(plan.staleFwd))
	for _, key := range plan.staleFwd {
		staleFwd[key] = struct{}{}
	}
	for _, want := range []nodePortFwdCTKey{
		forwardKeyFromCT(staleCTKey, staleCTValue),
		orphanFwdKey,
	} {
		if _, ok := staleFwd[want]; !ok {
			t.Fatalf("missing stale fwd key: %#v", want)
		}
	}
	if len(staleFwd) != 2 {
		t.Fatalf("staleFwd count=%d, want 2", len(staleFwd))
	}
}

func TestConntrackExpired(t *testing.T) {
	if conntrackExpired(100, 90, 0) {
		t.Fatalf("timeout=0 should disable expiry")
	}
	if conntrackExpired(100, 100, 10) {
		t.Fatalf("same timestamp should not expire")
	}
	if conntrackExpired(100, 110, 10) {
		t.Fatalf("future last_seen should not expire")
	}
	if !conntrackExpired(100, 50, 10) {
		t.Fatalf("stale entry should expire")
	}
}

func TestNodePortCTValueSizeMatchesBPFMap(t *testing.T) {
	if got, want := unsafe.Sizeof(nodePortCTValue{}), uintptr(24); got != want {
		t.Fatalf("nodePortCTValue size=%d, want %d", got, want)
	}
}

package main

import (
	"context"
	"fmt"
	"log"
	"time"

	"golang.org/x/sys/unix"
)

type nodePortCTKey struct {
	BackendIP   [4]byte
	NodeIP      [4]byte
	BackendPort [2]byte
	SNATPort    [2]byte
	Proto       uint8
	Pad         [3]byte
}

type nodePortCTValue struct {
	ClientIP     [4]byte
	FrontendIP   [4]byte
	ClientPort   [2]byte
	FrontendPort [2]byte
	Pad          [4]byte
	LastSeenNS   uint64
}

type nodePortFwdCTKey struct {
	ClientIP     [4]byte
	FrontendIP   [4]byte
	ClientPort   [2]byte
	FrontendPort [2]byte
	Proto        uint8
	Pad          [3]byte
}

type nodePortFwdCTValue struct {
	BackendIP     [4]byte
	SNATIP        [4]byte
	BackendPort   [2]byte
	SNATPort      [2]byte
	EgressIfindex uint32
}

type conntrackGCPlan struct {
	activeCTCount  int
	activeFwdCount int
	staleCT        []nodePortCTKey
	staleFwd       []nodePortFwdCTKey
}

type conntrackGCResult struct {
	ActiveCT   int
	ActiveFwd  int
	DeletedCT  int
	DeletedFwd int
}

func (c *controller) conntrackGCEnabled() bool {
	return c.opts.CTGCInterval > 0 && c.opts.CTEntryTimeout > 0
}

func (c *controller) runConntrackGC(ctx context.Context) error {
	if !c.conntrackGCEnabled() {
		return nil
	}

	ticker := time.NewTicker(c.opts.CTGCInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			nowMonoNS, err := monotonicNowNS()
			if err != nil {
				return err
			}

			result, err := c.maps.ReapConntrack(nowMonoNS, c.opts.CTEntryTimeout)
			if err != nil {
				return err
			}
			c.noteConntrackGC(result)
			if result.DeletedCT > 0 || result.DeletedFwd > 0 {
				log.Printf(
					"conntrack gc: active_ct=%d active_fwd=%d deleted_ct=%d deleted_fwd=%d",
					result.ActiveCT,
					result.ActiveFwd,
					result.DeletedCT,
					result.DeletedFwd,
				)
			}
		}
	}
}

func (m *pinnedMaps) ReapConntrack(nowMonoNS uint64, timeout time.Duration) (conntrackGCResult, error) {
	var result conntrackGCResult
	if m.dryRun || timeout <= 0 {
		return result, nil
	}

	ctEntries := make(map[nodePortCTKey]nodePortCTValue)
	ctIter := m.ctMap.Iterate()
	var ctKey nodePortCTKey
	var ctValue nodePortCTValue
	for ctIter.Next(&ctKey, &ctValue) {
		ctEntries[ctKey] = ctValue
	}
	if err := ctIter.Err(); err != nil {
		return result, fmt.Errorf("iterate conntrack map: %w", err)
	}

	var fwdKeys []nodePortFwdCTKey
	fwdIter := m.fwdCTMap.Iterate()
	var fwdKey nodePortFwdCTKey
	var fwdValue nodePortFwdCTValue
	for fwdIter.Next(&fwdKey, &fwdValue) {
		fwdKeys = append(fwdKeys, fwdKey)
	}
	if err := fwdIter.Err(); err != nil {
		return result, fmt.Errorf("iterate forward conntrack map: %w", err)
	}

	plan := buildConntrackGCPlan(nowMonoNS, timeout, ctEntries, fwdKeys)
	for _, key := range plan.staleCT {
		if err := deleteKey(m.ctMap, key); err != nil {
			return result, fmt.Errorf("delete conntrack entry: %w", err)
		}
	}
	for _, key := range plan.staleFwd {
		if err := deleteKey(m.fwdCTMap, key); err != nil {
			return result, fmt.Errorf("delete forward conntrack entry: %w", err)
		}
	}

	result.ActiveCT = plan.activeCTCount
	result.ActiveFwd = plan.activeFwdCount
	result.DeletedCT = len(plan.staleCT)
	result.DeletedFwd = len(plan.staleFwd)
	return result, nil
}

func buildConntrackGCPlan(
	nowMonoNS uint64,
	timeout time.Duration,
	ctEntries map[nodePortCTKey]nodePortCTValue,
	fwdKeys []nodePortFwdCTKey,
) conntrackGCPlan {
	plan := conntrackGCPlan{}
	if timeout <= 0 {
		plan.activeCTCount = len(ctEntries)
		plan.activeFwdCount = len(fwdKeys)
		return plan
	}

	activeFwd := make(map[nodePortFwdCTKey]struct{}, len(ctEntries))
	staleFwd := make(map[nodePortFwdCTKey]struct{})
	timeoutNS := uint64(timeout.Nanoseconds())

	for key, value := range ctEntries {
		if conntrackExpired(nowMonoNS, value.LastSeenNS, timeoutNS) {
			plan.staleCT = append(plan.staleCT, key)
			staleFwd[forwardKeyFromCT(key, value)] = struct{}{}
			continue
		}
		plan.activeCTCount++
		activeFwd[forwardKeyFromCT(key, value)] = struct{}{}
	}

	for _, key := range fwdKeys {
		if _, ok := activeFwd[key]; ok {
			plan.activeFwdCount++
			continue
		}
		staleFwd[key] = struct{}{}
	}

	for key := range staleFwd {
		plan.staleFwd = append(plan.staleFwd, key)
	}
	return plan
}

func conntrackExpired(nowMonoNS, lastSeenNS, timeoutNS uint64) bool {
	if timeoutNS == 0 || nowMonoNS <= lastSeenNS {
		return false
	}
	return nowMonoNS-lastSeenNS > timeoutNS
}

func forwardKeyFromCT(key nodePortCTKey, value nodePortCTValue) nodePortFwdCTKey {
	return nodePortFwdCTKey{
		ClientIP:     value.ClientIP,
		FrontendIP:   value.FrontendIP,
		ClientPort:   value.ClientPort,
		FrontendPort: value.FrontendPort,
		Proto:        key.Proto,
	}
}

func monotonicNowNS() (uint64, error) {
	var ts unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &ts); err != nil {
		return 0, fmt.Errorf("read CLOCK_MONOTONIC: %w", err)
	}
	return uint64(ts.Sec)*uint64(time.Second) + uint64(ts.Nsec), nil
}

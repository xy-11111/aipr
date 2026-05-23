package main

import (
	"encoding/csv"
	"fmt"
	"os"
	"path/filepath"
)

type telemetryCSVWriter struct {
	file   *os.File
	writer *csv.Writer
}

func newTelemetryCSVWriter(path string) (*telemetryCSVWriter, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, fmt.Errorf("create telemetry output directory: %w", err)
	}

	file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, fmt.Errorf("open telemetry output %s: %w", path, err)
	}

	writer := csv.NewWriter(file)
	info, err := file.Stat()
	if err != nil {
		_ = file.Close()
		return nil, fmt.Errorf("stat telemetry output %s: %w", path, err)
	}
	if info.Size() == 0 {
		if err := writer.Write(telemetryCSVHeader()); err != nil {
			_ = file.Close()
			return nil, fmt.Errorf("write telemetry header: %w", err)
		}
		writer.Flush()
		if err := writer.Error(); err != nil {
			_ = file.Close()
			return nil, fmt.Errorf("flush telemetry header: %w", err)
		}
	}

	return &telemetryCSVWriter{
		file:   file,
		writer: writer,
	}, nil
}

func (w *telemetryCSVWriter) WriteSample(sample telemetrySample) error {
	if err := w.writer.Write(sample.CSVRecord()); err != nil {
		return fmt.Errorf("write telemetry sample: %w", err)
	}
	w.writer.Flush()
	if err := w.writer.Error(); err != nil {
		return fmt.Errorf("flush telemetry sample: %w", err)
	}
	return nil
}

func (w *telemetryCSVWriter) Close() error {
	if w == nil || w.file == nil {
		return nil
	}
	w.writer.Flush()
	if err := w.writer.Error(); err != nil {
		_ = w.file.Close()
		return err
	}
	return w.file.Close()
}

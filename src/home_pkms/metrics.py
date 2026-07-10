"""Prometheus metrics — scraped per the plan's telemetry section."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, start_http_server

notes_ingested_total = Counter("notes_ingested_total", "Notes successfully ingested")
htr_pages_processed_total = Counter("htr_pages_processed_total", "Pages run through HTR")
htr_low_confidence_flagged_total = Counter(
    "htr_low_confidence_flagged_total", "Entries flagged to Needs Review or as conflicts"
)
pipeline_errors_total = Counter("pipeline_errors_total", "Errors during ingestion (non-fatal, per item)")
last_successful_run_timestamp = Gauge(
    "last_successful_run_timestamp", "Unix timestamp of the last successful run_once() completion"
)


def start_metrics_server(port: int) -> None:
    start_http_server(port)

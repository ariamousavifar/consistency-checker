"""Per-stage timing (architecture: Reports > Timing log).

Lightweight wall-clock instrumentation with no dependencies. The pipeline
wraps each stage in `with timer.stage("name"):`; results go to the console,
to report.md, and to out/timing.json.
"""
from __future__ import annotations

import time
from contextlib import contextmanager


class StageTimer:
    def __init__(self) -> None:
        self.records: list[dict] = []

    @contextmanager
    def stage(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.records.append({"stage": name, "seconds": round(time.perf_counter() - t0, 4)})

    @property
    def total_seconds(self) -> float:
        return round(sum(r["seconds"] for r in self.records), 4)

    def as_table(self) -> str:
        lines = [f"{'stage':<16}{'seconds':>10}"]
        for r in self.records:
            lines.append(f"{r['stage']:<16}{r['seconds']:>10.4f}")
        lines.append(f"{'total':<16}{self.total_seconds:>10.4f}")
        return "\n".join(lines)

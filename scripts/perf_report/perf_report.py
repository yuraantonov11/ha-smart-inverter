#!/usr/bin/env python3
"""Smart Inverter performance analyzer.

Parses diagnostics snapshots and runtime logs to produce a markdown report with:
- chart timing latency stats (p50/p95/max)
- weather cache hit rate and avg latency
- detected economics/tariff anomalies

Uses only Python standard library.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import re
from pathlib import Path
from statistics import median
from typing import Iterable, List, Optional


CHART_RE = re.compile(
    r"\[chart\] timing .* total=(\d+)ms chart=(\d+)ms daily=(\d+)ms day=(\d+)ms"
)
WEATHER_RE = re.compile(
    r"Weather perf: local hit/miss (\d+)/(\d+) \(join (\d+)\) avg (\d+)ms \| "
    r"daily hit/miss (\d+)/(\d+) \(join (\d+)\) avg (\d+)ms"
)
GEN_RE = re.compile(r"Generated:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
MONTH_RE = re.compile(
    r"Month economics: load ([\d.]+) kWh \| grid ([\d.]+) kWh \| "
    r"self-consumed ([\d.]+) kWh"
)
TARIFF_RE = re.compile(
    r"Tariff forecast: .* cheap now (yes|no)(?: \| next (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}))?"
)
WINDOWS_RE = re.compile(r"HEMS windows: day (\d+):00 \| evening (\d+):00 \| night (\d+):00")


class Acc:
    def __init__(self) -> None:
        self.chart_total: List[int] = []
        self.chart_data: List[int] = []
        self.chart_daily: List[int] = []
        self.chart_day: List[int] = []

        self.local_hit = 0
        self.local_miss = 0
        self.local_join = 0
        self.local_avg_samples: List[int] = []
        self.daily_hit = 0
        self.daily_miss = 0
        self.daily_join = 0
        self.daily_avg_samples: List[int] = []

        self.findings: List[str] = []
        self.snapshot_count = 0


def pctl(values: List[int], q: float) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return s[lo]
    frac = pos - lo
    return round(s[lo] + (s[hi] - s[lo]) * frac)


def avg(values: List[int]) -> int:
    return round(sum(values) / len(values)) if values else 0


def parse_files(paths: Iterable[Path]) -> Acc:
    acc = Acc()

    snapshot_generated: Optional[dt.datetime] = None

    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            m = CHART_RE.search(line)
            if m:
                acc.chart_total.append(int(m.group(1)))
                acc.chart_data.append(int(m.group(2)))
                acc.chart_daily.append(int(m.group(3)))
                acc.chart_day.append(int(m.group(4)))
                continue

            m = WEATHER_RE.search(line)
            if m:
                acc.local_hit += int(m.group(1))
                acc.local_miss += int(m.group(2))
                acc.local_join += int(m.group(3))
                acc.local_avg_samples.append(int(m.group(4)))
                acc.daily_hit += int(m.group(5))
                acc.daily_miss += int(m.group(6))
                acc.daily_join += int(m.group(7))
                acc.daily_avg_samples.append(int(m.group(8)))
                continue

            if line.startswith("Smart Inverter diagnostics snapshot"):
                acc.snapshot_count += 1
                snapshot_generated = None
                continue

            m = GEN_RE.search(line)
            if m:
                snapshot_generated = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                continue

            m = MONTH_RE.search(line)
            if m:
                load = float(m.group(1))
                grid = float(m.group(2))
                self_kwh = float(m.group(3))
                if grid > load + 0.01:
                    acc.findings.append(
                        f"CRITICAL economics anomaly: grid ({grid:.1f} kWh) > load ({load:.1f} kWh)."
                    )
                if abs((grid + self_kwh) - load) > 0.2:
                    acc.findings.append(
                        f"WARN economics mismatch: load ({load:.1f}) != grid+self ({grid + self_kwh:.1f}) kWh."
                    )
                continue

            m = TARIFF_RE.search(line)
            if m:
                cheap_now = m.group(1) == "yes"
                next_ts = m.group(2)
                if (not cheap_now) and next_ts and snapshot_generated:
                    next_dt = dt.datetime.strptime(next_ts, "%Y-%m-%d %H:%M:%S")
                    if next_dt <= snapshot_generated:
                        acc.findings.append(
                            "CRITICAL tariff anomaly: cheap_now=no but next cheap window is in the past."
                        )
                continue

            m = WINDOWS_RE.search(line)
            if m:
                day = int(m.group(1))
                evening = int(m.group(2))
                night = int(m.group(3))
                if not (3 <= day <= 12 and 14 <= evening <= 22 and 17 <= night <= 23):
                    acc.findings.append(
                        f"WARN astronomical windows suspicious: day={day}, evening={evening}, night={night}."
                    )
                continue

    return acc


def markdown_report(acc: Acc, scanned: List[Path]) -> str:
    local_req = acc.local_hit + acc.local_miss
    daily_req = acc.daily_hit + acc.daily_miss
    local_hit_rate = (acc.local_hit / local_req * 100.0) if local_req else 0.0
    daily_hit_rate = (acc.daily_hit / daily_req * 100.0) if daily_req else 0.0

    lines: List[str] = []
    lines.append("# Smart Inverter Performance Report")
    lines.append("")
    lines.append(f"Generated at: `{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`")
    lines.append(f"Scanned files: **{len(scanned)}**")
    lines.append(f"Diagnostics snapshots found: **{acc.snapshot_count}**")
    lines.append("")

    lines.append("## Chart Timing")
    if acc.chart_total:
        lines.append(
            f"- total: avg `{avg(acc.chart_total)}ms`, p50 `{pctl(acc.chart_total, 0.50)}ms`, p95 `{pctl(acc.chart_total, 0.95)}ms`, max `{max(acc.chart_total)}ms`"
        )
        lines.append(
            f"- chart data: avg `{avg(acc.chart_data)}ms`; daily forecast: avg `{avg(acc.chart_daily)}ms`; day forecast: avg `{avg(acc.chart_day)}ms`"
        )
    else:
        lines.append("- no chart timing lines found")
    lines.append("")

    lines.append("## Weather Cache")
    lines.append(
        f"- local: hit `{acc.local_hit}` miss `{acc.local_miss}` join `{acc.local_join}` hit-rate `{local_hit_rate:.1f}%` avg-latency `{avg(acc.local_avg_samples)}ms`"
    )
    lines.append(
        f"- daily: hit `{acc.daily_hit}` miss `{acc.daily_miss}` join `{acc.daily_join}` hit-rate `{daily_hit_rate:.1f}%` avg-latency `{avg(acc.daily_avg_samples)}ms`"
    )
    lines.append("")

    lines.append("## Findings")
    if acc.findings:
        for finding in acc.findings:
            lines.append(f"- {finding}")
    else:
        lines.append("- no anomalies detected")

    lines.append("")
    lines.append("## Source Files")
    for p in scanned:
        lines.append(f"- `{p}`")

    return "\n".join(lines) + "\n"


def collect_input_files(input_path: Path) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        files = [
            p
            for p in input_path.rglob("*")
            if p.is_file() and p.suffix.lower() in {".log", ".txt", ".md"}
        ]
        return sorted(files)
    raise FileNotFoundError(f"Input path not found: {input_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Smart Inverter diagnostics logs.")
    parser.add_argument("--input", required=True, help="Input file or directory with logs")
    parser.add_argument("--output", required=True, help="Output markdown report file")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    files = collect_input_files(input_path)
    if not files:
        raise RuntimeError("No .log/.txt/.md files found in input path")

    acc = parse_files(files)
    report = markdown_report(acc, files)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Report written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


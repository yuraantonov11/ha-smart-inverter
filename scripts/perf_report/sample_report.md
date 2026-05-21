# Smart Inverter Performance Report

Generated at: `2026-05-15 13:22:35`
Scanned files: **1**
Diagnostics snapshots found: **1**

## Chart Timing
- total: avg `418ms`, p50 `418ms`, p95 `418ms`, max `418ms`
- chart data: avg `102ms`; daily forecast: avg `184ms`; day forecast: avg `128ms`

## Weather Cache
- local: hit `4` miss `2` join `0` hit-rate `66.7%` avg-latency `69ms`
- daily: hit `9` miss `1` join `0` hit-rate `90.0%` avg-latency `65ms`

## Findings
- CRITICAL economics anomaly: grid (118.5 kWh) > load (113.2 kWh).
- WARN economics mismatch: load (113.2) != grid+self (135.4) kWh.
- CRITICAL tariff anomaly: cheap_now=no but next cheap window is in the past.

## Source Files
- `C:\Users\yuraa\WebstormProjects\inverter_app\scripts\perf_report\sample_snapshot.log`

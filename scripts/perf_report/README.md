# Performance Report Tool

Parses Smart Inverter diagnostics/runtime logs and generates a Markdown report with:

- chart timing latency (avg/p50/p95/max)
- weather cache efficiency (hit/miss/join, avg latency)
- detected anomalies (economics mismatch, tariff next-window in past, suspicious windows)

## Input

`--input` can be:

- a single file (`.log`, `.txt`, `.md`)
- a directory (tool scans recursively for `.log`, `.txt`, `.md`)

## Output

`--output` is a `.md` report file path.

## Quick Run (PowerShell)

```powershell
Set-Location "c:\Users\yuraa\WebstormProjects\inverter_app"
python -u scripts\perf_report\perf_report.py --input . --output scripts\perf_report\last_report.md
```

## Recommended Workflow

1. Copy diagnostics logs from app documents folder to a workspace folder (for example `scripts\perf_report\logs\`).
2. Run the tool on that folder.
3. Review `last_report.md` and compare p50/p95 between runs.

## Notes

- Uses only Python standard library (`requirements.txt` intentionally empty).
- Safe to run multiple times; output file is overwritten.


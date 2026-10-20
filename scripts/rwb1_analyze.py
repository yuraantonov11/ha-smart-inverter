"""RWB1 stage-A corrected analyzer (round-7 review).

Юра's corrections (round 7):
  * Do not assume little-endian for the whole flow. Each
    register / block has its own byte order; show both
    interpretations and mark as "unknown" unless a profile is
    confirmed.
  * Exact-timestamp matching with explicit tolerance. Reject
    pairs that fall outside the tolerance and report the
    reason. Do not bucket-average across event boundaries.
  * Track three timestamps per observation: capture packet
    time (from pcap), payload ts (device clock, semantics
    unconfirmed), HA reception time.
  * Capture-side gap categorization: kernel drop, TCP
    reassembly gap, decoder reject.
  * TCP reassembly continues across pcap rotation boundaries
    (one TCP flow can span multiple files).

Usage::

    python3 scripts/rwb1_analyze.py \
        --pcap captures/canary_*.pcap \
        --ha-journal /path/to/events.jsonl \
        --adapter-ip 192.168.1.139 \
        --time-tolerance-s 2
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import math
import statistics
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

_SANITIZER = Path(__file__).with_name("inspect_rwb1_mqtt_capture.py")
_spec = importlib.util.spec_from_file_location("_sanitizer", _SANITIZER)
_san = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_san)


# ─────────────────────────────────────────────────────────────────────
# TCP reassembly across pcap files
# ─────────────────────────────────────────────────────────────────────

@dataclass
class TcpStream:
    """One TCP flow identified by 4-tuple.

    Segments may arrive across multiple pcap files (rotation).
    Reassembly is done by sequence number; gaps are recorded
    rather than silently skipped.
    """

    key: tuple
    src_port: int
    dst_port: int
    segments: list = field(default_factory=list)
    """List of (seq, payload_bytes, pcap_filename, packet_ts)."""
    first_ts: float = math.inf
    last_ts: float = -math.inf

    def add(self, seq: int, payload: bytes, fname: str, ts: float) -> None:
        if not payload:
            return
        self.segments.append((seq, payload, fname, ts))
        self.first_ts = min(self.first_ts, ts)
        self.last_ts = max(self.last_ts, ts)

    def reassemble(self) -> tuple[bytes, list[dict]]:
        """Return (reassembled_bytes, list_of_gap_records).

        Also records a byte-range → packet-timestamp map so callers
        can attribute the capture time of any offset in the blob.
        """
        if not self.segments:
            return b"", []
        # Sort by sequence; within same seq, keep the longest payload.
        # (tcpdump can deliver the same segment via duplicate paths.)
        segs = sorted(self.segments, key=lambda s: (s[0], -len(s[1])))
        out = bytearray()
        gaps = []
        spans: list[tuple[int, int, float]] = []
        next_seq = None  # next expected TCP sequence number
        for seq, payload, fname, ts in segs:
            if next_seq is None:
                out.extend(payload)
                spans.append((0, len(payload), ts))
                next_seq = seq + len(payload)
                continue
            # Fully inside the buffer: retransmission, skip.
            if seq + len(payload) <= next_seq:
                continue
            # Partial overlap: keep the tail.
            if seq < next_seq:
                payload = payload[next_seq - seq:]
            # Gap: missing bytes between next_seq and seq.
            if seq > next_seq:
                gaps.append({
                    "filename": fname,
                    "packet_ts": ts,
                    "missing_bytes": seq - next_seq,
                })
            start = len(out)
            out.extend(payload)
            spans.append((start, len(out), ts))
            next_seq = seq + len(payload)
        self.spans = spans
        return bytes(out), gaps

    def ts_at(self, offset: int) -> float | None:
        """Capture timestamp of the TCP segment containing `offset`."""
        for start, end, ts in getattr(self, "spans", []):
            if start <= offset < end:
                return ts
        return None


def reassemble_all(pcap_files: list[Path], adapter_ip: str) -> tuple[
    dict[tuple, TcpStream], dict[str, dict]
]:
    """Reassemble every TCP flow across all pcap files in time order.

    Returns (streams_by_direction, capture_stats). The key is
    (direction, src_port, dst_port) where ports are recovered
    from the first packet of each direction.
    """
    # Aggregate segments across files, keyed by direction, preserving
    # the file each segment came from.
    aggregated: dict[tuple, list] = {}
    ports: dict[tuple, tuple[int, int]] = {}
    for fpath in pcap_files:
        with fpath.open("rb") as f:
            data = f.read()
        bio = io.BytesIO(data)
        flows = _san.parse_pcap(bio, adapter_ip=adapter_ip)
        for direction, chunks in flows.items():
            aggregated.setdefault(direction, []).extend(chunks)
            if direction not in ports:
                # Recover the (src_port, dst_port) from the first chunk.
                # parse_pcap does not return them; we use a placeholder
                # for now and refine if the sanitizer exposes them.
                ports[direction] = (0, 0)
    streams: dict[tuple, TcpStream] = {}
    for direction, chunks in aggregated.items():
        key = (direction, *ports[direction])
        stream = TcpStream(key=key, src_port=ports[direction][0], dst_port=ports[direction][1])
        for seq, payload, ptime in chunks:
            stream.add(seq, payload, "<merged>", ptime)
        streams[key] = stream
    total_gaps = 0
    for stream in streams.values():
        _, gaps = stream.reassemble()
        total_gaps += len(gaps)
    return streams, {"total_gaps": total_gaps}


# ─────────────────────────────────────────────────────────────────────
# MQTT decode (reused from inspector)
# ─────────────────────────────────────────────────────────────────────

def mqtt_publishes(blob: bytes, ts_lookup=None):
    """Yield (topic, payload, offset, capture_ts) for every PUBLISH.

    `offset` is the byte index of the PUBLISH header inside `blob`.
    `capture_ts` is the pcap timestamp of the TCP segment containing
    that header (NOT the payload `ts` field, whose semantics are
    unconfirmed).
    """
    pos = 0
    while pos < len(blob):
        first = blob[pos]
        ptype = first >> 4
        if ptype < 1 or ptype > 15:
            pos += 1
            continue
        rem = 0
        mult = 1
        cur = pos + 1
        ok = False
        for _ in range(4):
            if cur >= len(blob):
                break
            d = blob[cur]
            cur += 1
            rem += (d & 127) * mult
            if not d & 128:
                ok = True
                break
            mult *= 128
        if not ok:
            pos += 1
            continue
        end = cur + rem
        if end > len(blob):
            break
        body = blob[cur:end]
        if ptype == 3 and len(body) >= 2:
            tl = int.from_bytes(body[:2], "big")
            if tl + 2 <= len(body):
                off = 2 + tl
                if (first >> 1) & 3:
                    off += 2
                topic = body[2:2 + tl].decode("utf-8", "replace")
                payload = body[off:] if off <= len(body) else b""
                cap_ts = ts_lookup(pos) if ts_lookup else None
                yield topic, payload, pos, cap_ts
        pos = end


def parse_rwb_ts(ts: str):
    """Parse an RWB1 payload `ts` into an aware UTC datetime.

    The RWB1 emits `2026-10-20T10:45:07.000+08:00` — the offset is
    the device's declared timezone. Whether the wall-clock was
    actually correct (NTP sync) is UNKNOWN; this only applies the
    declared offset.
    """
    from datetime import datetime, timezone as _tz
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    return dt.astimezone(_tz.utc)


# ─────────────────────────────────────────────────────────────────────
# Decoder outcome categorization
# ─────────────────────────────────────────────────────────────────────

@dataclass
class DecodeStats:
    kernel_drops: int = 0
    """Best-effort: from tcpdump stderr; we do not parse it here."""
    reassembly_gaps: int = 0
    """TCP gaps observed while reassembling across pcap files."""
    decoder_rejects: dict = field(default_factory=dict)
    """Counts per rejection reason: crc, length, shape, no_match."""


# ─────────────────────────────────────────────────────────────────────
# Per-register table (no endianness assumption)
# ─────────────────────────────────────────────────────────────────────

def _split_value(data: bytes):
    """Return (hex_bytes, big_endian_int, little_endian_int)."""
    if len(data) != 2:
        return None
    return (
        data.hex(),
        int.from_bytes(data, "big"),
        int.from_bytes(data, "little"),
    )


@dataclass
class RegSample:
    register: int
    hex_bytes: str
    big_endian: int | None
    little_endian: int | None
    crc_ok: bool
    capture_ts: float
    payload_ts: str | None
    """RWB1 device ts; semantics unconfirmed (measurement vs publish)."""


def collect_rpc_samples(pcap_files, adapter_ip):
    """Walk every pcap, every PUBLISH, extract RPC req/rep pairs
    correlated by transaction token."""
    streams, capture_meta = reassemble_all(pcap_files, adapter_ip)
    out: dict[str, dict] = {}
    for key, stream in streams.items():
        blob = stream.reassemble()[0]
        for topic, payload, _off, cap_ts in mqtt_publishes(blob, stream.ts_at):
            try:
                j = json.loads(payload.decode("utf-8", "replace").lstrip("\x00"))
            except Exception:
                continue
            t = j.get("t")
            code = j.get("i")
            b = j.get("b") or {}
            if not t:
                continue
            entry = out.setdefault(t, {"code": code, "topic": topic})
            if isinstance(b.get("ci"), str):
                try:
                    raw = base64.b64decode(b["ci"] + "==")
                    entry["req_reads"] = _san.find_read_requests(raw)
                except Exception:
                    pass
            if isinstance(b.get("co"), str):
                try:
                    entry["co_raw"] = base64.b64decode(b["co"] + "==")
                except Exception:
                    pass
            entry.setdefault("capture_ts", cap_ts)
            entry.setdefault("payload_ts", b.get("ts"))
    return out, capture_meta


def build_register_table(rpc_index: dict) -> dict[int, list[RegSample]]:
    """Correlate request/reply by transaction token; emit per-register
    samples with both byte-order interpretations.
    """
    out: dict[int, list[RegSample]] = {}
    decode_rejects: dict[str, int] = {}
    for t, info in rpc_index.items():
        reqs = info.get("req_reads") or []
        co_raw = info.get("co_raw")
        if not reqs or not co_raw:
            continue
        # Validate the reply shape (Modbus FC03, 1-word reply).
        if len(co_raw) != 7 or co_raw[0] != 5 or co_raw[1] != 3 or co_raw[2] != 2:
            decode_rejects["bad_shape"] = decode_rejects.get("bad_shape", 0) + 1
            continue
        if _san.crc16(co_raw[:-2]) != int.from_bytes(co_raw[-2:], "little"):
            decode_rejects["crc"] = decode_rejects.get("crc", 0) + 1
            continue
        split = _split_value(co_raw[3:5])
        if split is None:
            decode_rejects["length"] = decode_rejects.get("length", 0) + 1
            continue
        for reg, _cnt in reqs:
            sample = RegSample(
                register=reg,
                hex_bytes=split[0],
                big_endian=split[1],
                little_endian=split[2],
                crc_ok=True,
                capture_ts=info.get("capture_ts", 0.0),
                payload_ts=info.get("payload_ts"),
            )
            out.setdefault(reg, []).append(sample)
    return out, decode_rejects


# ─────────────────────────────────────────────────────────────────────
# Time matching (exact-second, reject otherwise)
# ─────────────────────────────────────────────────────────────────────

def parse_ha_journal(path: Path):
    """Return by_second: HA cycle observations keyed by epoch second.

    HA journal `event_timestamp` is UTC (`+00:00`). Keying on the
    epoch second makes it directly comparable with the RWB1 ts
    after offset normalisation.
    """
    from datetime import datetime, timezone as _tz
    by_second: dict[int, tuple] = {}
    with path.open() as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("event_type") != "cycle":
                continue
            s = d.get("state") or {}
            ts = d.get("event_timestamp", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_tz.utc)
            sec = int(dt.timestamp())
            by_second[sec] = (
                s.get("load_power_w"),
                s.get("grid_voltage_v"),
                s.get("battery_voltage_v"),
                s.get("corrected_soc"),
                ts,
            )
    return by_second


@dataclass
class TimeMatch:
    device_ts: str
    capture_ts: float
    matched: bool
    delta_s: int | None
    reason: str
    ha_ts: str | None
    ha_load: float | None
    ha_grid_v: float | None
    ha_batt_v: float | None
    ha_soc: float | None


def match_rwb_to_ha(
    rwb_device_ts: str,
    capture_ts: float,
    by_second: dict[int, tuple],
    tolerance_s: int,
):
    """Exact-second match within ±tolerance_s, after normalising the
    RWB1 declared-offset timestamp to UTC.

    Reject if no exact match within tolerance. NEVER fall back to
    the closest cycle: a +9 s nearest-match is a different
    observation, not a paired one.
    """
    dt = parse_rwb_ts(rwb_device_ts)
    if dt is None:
        return TimeMatch(
            device_ts=rwb_device_ts,
            capture_ts=capture_ts,
            matched=False,
            delta_s=None,
            reason=f"unparseable payload ts {rwb_device_ts!r}",
            ha_ts=None,
            ha_load=None,
            ha_grid_v=None,
            ha_batt_v=None,
            ha_soc=None,
        )
    target = int(dt.timestamp())
    for delta in range(0, tolerance_s + 1):
        for sign in ((0,) if delta == 0 else (-1, 1)):
            sec = target + sign * delta
            if sec not in by_second:
                continue
            load, gv, bv, soc, ha_ts = by_second[sec]
            return TimeMatch(
                device_ts=rwb_device_ts,
                capture_ts=capture_ts,
                matched=True,
                delta_s=sign * delta,
                reason=f"HA cycle {ha_ts} (Δ={sign * delta:+d}s)",
                ha_ts=ha_ts,
                ha_load=load,
                ha_grid_v=gv,
                ha_batt_v=bv,
                ha_soc=soc,
            )
    return TimeMatch(
        device_ts=rwb_device_ts,
        capture_ts=capture_ts,
        matched=False,
        delta_s=None,
        reason=(
            f"no HA cycle within ±{tolerance_s}s of "
            f"{dt.isoformat()} (RWB1 ts normalised to UTC; "
            f"declared offset applied, clock correctness unverified)"
        ),
        ha_ts=None,
        ha_load=None,
        ha_grid_v=None,
        ha_batt_v=None,
        ha_soc=None,
    )


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap", nargs="+", required=True)
    parser.add_argument("--ha-journal", required=True)
    parser.add_argument("--adapter-ip", required=True)
    parser.add_argument("--time-tolerance-s", type=int, default=2)
    args = parser.parse_args()

    pcaps = [Path(p) for p in args.pcap]
    ha = parse_ha_journal(Path(args.ha_journal))

    # 1. Reassemble TCP across pcap files, count gaps.
    streams, capture_meta = reassemble_all(pcaps, args.adapter_ip)
    print("=== capture-side gap categorization")
    print(f"total_reassembly_gaps = {capture_meta['total_gaps']}")

    # 2. RPC samples.
    rpc_index, _ = collect_rpc_samples(pcaps, args.adapter_ip)
    reg_table, decode_rejects = build_register_table(rpc_index)
    print()
    print("=== per-register RPC samples (both byte orders, no assumed mapping)")
    print(f"decode_rejects: {decode_rejects}")
    for reg in sorted(reg_table):
        samples = reg_table[reg]
        be_vals = sorted({s.big_endian for s in samples})
        le_vals = sorted({s.little_endian for s in samples})
        first = samples[0]
        crc_all = all(s.crc_ok for s in samples)
        print(
            f"reg {reg:>5}  n={len(samples):>3}  distinct_be={len(be_vals):>2} {be_vals!s:<24}"
            f"  distinct_le={len(le_vals):>2} {le_vals!s:<24}  crc_ok={crc_all}"
        )

    # 3. Time matching for the 21-word (PS4Z) push observations.
    # Extract from the same MQTT blob as the RPC, but filter to
    # adapter->broker direction.
    push_obs: list[tuple[str, float]] = []
    for key, stream in streams.items():
        if not key[0].startswith("adapter->broker"):
            continue
        blob = stream.reassemble()[0]
        for topic, payload, _off, cap_ts in mqtt_publishes(blob, stream.ts_at):
            try:
                j = json.loads(payload.decode("utf-8", "replace").lstrip("\x00"))
            except Exception:
                continue
            b = j.get("b") or {}
            if not isinstance(b.get("ct"), list):
                continue
            for item in b["ct"]:
                if item.get("cn") != "PS4Z":
                    continue
                push_obs.append((b.get("ts"), cap_ts))
    print()
    print("=== PS4Z push time matching (exact second, no averaging)")
    print("3 separate clocks kept: payload_ts (device, semantics "
          "unconfirmed) | cap_ts (pcap packet) | ha_ts (HA cycle)")
    print(f"push observations: {len(push_obs)}")
    matched = 0
    rejected = 0
    for ts, cts in sorted(push_obs):
        m = match_rwb_to_ha(ts, cts, ha, args.time_tolerance_s)
        if m.matched:
            matched += 1
            cap_s = (
                f"{cts:.3f}" if cts is not None else "n/a"
            )
            print(
                f"  payload_ts={ts}  cap_ts={cap_s}  Δ={m.delta_s:+d}s  "
                f"HA_load={m.ha_load}  HA_gV={m.ha_grid_v}  "
                f"HA_bV={m.ha_batt_v}  HA_soc={m.ha_soc}"
            )
        else:
            rejected += 1
            cap_s = f"{cts:.3f}" if cts is not None else "n/a"
            print(f"  payload_ts={ts}  cap_ts={cap_s}  REJECTED: {m.reason}")
    print()
    print(f"matched={matched}  rejected={rejected}")


if __name__ == "__main__":
    main()
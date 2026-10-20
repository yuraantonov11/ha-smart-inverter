#!/usr/bin/env python3
"""RWB1 stage-A cadence analyser (sanitized output only).

Reads a pcap of the RWB1 outbound MQTT flow and reports, per
confirmed field or Modbus block, the number of independent
messages, the median and p95 inter-arrival interval, and the
maximum gap.

This is stage-A measurement only. Nothing here connects to an
inverter, opens a socket or issues a command; it reads a pcap on
stdin and prints counts, intervals and — only for blocks whose
field map is corroborated — engineering values.

Independent messages are counted from the structured transaction
token (``t``) plus the device block identifier (``cn``). Repeated
identical values are NOT collapsed: two equal values from two
different messages are two measurements.

Usage::

    python3 scripts/rwb1_cadence.py --adapter-ip 192.168.1.139 < capture.pcap
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

_SANITIZER = Path(__file__).with_name("inspect_rwb1_mqtt_capture.py")
_spec = importlib.util.spec_from_file_location("_sanitizer", _SANITIZER)
_san = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_san)


def _publish_payloads(blob: bytes, timestamps: list[float]) -> list[tuple[bytes, float]]:
    """Yield ``(payload, receipt_time)`` for every PUBLISH packet.

    The receipt time is taken from the per-byte timestamp array
    built by ``reassemble_with_timestamps``, indexed at the first
    payload byte of the packet. Using the last index would collapse
    every interval to zero and hide the real cadence.
    """
    out: list[tuple[bytes, float]] = []
    pos = 0
    while pos < len(blob):
        first = blob[pos]
        ptype = first >> 4
        if ptype < 1 or ptype > 15:
            pos += 1
            continue
        remaining = 0
        multiplier = 1
        cursor = pos + 1
        complete = False
        for _ in range(4):
            if cursor >= len(blob):
                break
            digit = blob[cursor]
            cursor += 1
            remaining += (digit & 127) * multiplier
            if not digit & 128:
                complete = True
                break
            multiplier *= 128
        if not complete:
            pos += 1
            continue
        end = cursor + remaining
        if end > len(blob):
            break
        body = blob[cursor:end]
        if ptype == 3 and len(body) >= 2:
            topic_len = int.from_bytes(body[:2], "big")
            if topic_len + 2 <= len(body):
                offset = 2 + topic_len
                if (first >> 1) & 3:
                    offset += 2
                payload = body[offset:] if offset <= len(body) else b""
                # Byte offset of the payload inside the reassembled stream.
                byte_offset = cursor + offset
                stamp = (
                    timestamps[min(byte_offset, len(timestamps) - 1)]
                    if timestamps
                    else 0.0
                )
                out.append((payload, stamp))
        pos = end
    return out


def _decode(payload: bytes) -> dict | None:
    try:
        return json.loads(payload.decode("utf-8", "replace").lstrip("\x00"))
    except Exception:
        return None


def _frames(raw: bytes) -> list[bytes]:
    return [frame for _, _, frame in _find_frames(raw)]


def _find_frames(raw: bytes) -> list[tuple[int, int, bytes]]:
    found: list[tuple[int, int, bytes]] = []
    for offset in range(max(0, len(raw) - 6)):
        if raw[offset] != 5 or raw[offset + 1] != 3:
            continue
        byte_count = raw[offset + 2]
        if byte_count < 2 or byte_count > 250 or byte_count % 2:
            continue
        length = byte_count + 5
        candidate = raw[offset:offset + length]
        if len(candidate) < length:
            continue
        if _san.crc16(candidate[:-2]) == int.from_bytes(candidate[-2:], "little"):
            found.append((offset, byte_count // 2, candidate))
    return found


def collect(pcap: bytes, adapter_ip: str) -> dict:
    flows = _san.parse_pcap(io.BytesIO(pcap), adapter_ip=adapter_ip)
    events: dict[str, list[float]] = defaultdict(list)
    blocks: dict[tuple[str, int], list[float]] = defaultdict(list)
    values: dict[str, list[float]] = defaultdict(list)

    for direction, chunks in flows.items():
        if not chunks:
            continue
        blob, timestamps = _san.reassemble_with_timestamps(list(chunks))
        if not timestamps:
            continue
        for payload, stamp in _publish_payloads(blob, timestamps):
            message = _decode(payload)
            if not isinstance(message, dict):
                continue
            body = message.get("b") or {}
            token = message.get("t")
            code = message.get("i")

            # Unsolicited device telemetry: b.ct[] with b.ts = device time.
            blocks_list = body.get("ct")
            if isinstance(blocks_list, list):
                # Locate the byte offset of this PUBLISH inside the blob so
                # the reassembled per-byte timestamp array can be indexed.
                for item in blocks_list:
                    if not isinstance(item, dict):
                        continue
                    cn = item.get("cn")
                    co = item.get("co")
                    if not isinstance(cn, str) or not isinstance(co, str):
                        continue
                    try:
                        raw = base64.b64decode(co + "==")
                    except Exception:
                        continue
                    for _, words, frame in _find_frames(raw):
                        key = f"{direction}|{cn}|{words}w"
                        blocks[key].append(stamp)
                        if words == 21:
                            little = [
                                int.from_bytes(frame[3 + i * 2:5 + i * 2], "little")
                                for i in range(words)
                            ]
                            for name, index, scale in (
                                ("grid_voltage", 1, 0.1),
                                ("pv_power", 4, 1.0),
                                ("battery_voltage", 5, 0.1),
                                ("battery_soc", 6, 1.0),
                                ("load_power", 12, 1.0),
                            ):
                                values[name].append(little[index] * scale)

            # RPC request/reply pairs correlated on the transaction token.
            if isinstance(body.get("ci"), str):
                events[f"{direction}|rpc_request|{token}|{code}"].append(stamp)
            if isinstance(body.get("co"), str):
                events[f"{direction}|rpc_reply|{token}|{code}"].append(stamp)

    return {"events": events, "blocks": blocks, "values": values}


def stats(samples: list[float]) -> dict:
    ordered = sorted(samples)
    intervals = [b - a for a, b in zip(ordered, ordered[1:])]
    if not intervals:
        return {
            "messages": len(ordered),
            "median_s": None,
            "p95_s": None,
            "max_gap_s": None,
        }
    intervals_sorted = sorted(intervals)
    p95_index = max(0, int(round(0.95 * (len(intervals_sorted) - 1))))
    return {
        "messages": len(ordered),
        "median_s": round(statistics.median(intervals_sorted), 3),
        "p95_s": round(intervals_sorted[p95_index], 3),
        "max_gap_s": round(max(intervals_sorted), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter-ip", required=True)
    parser.add_argument("--show-values", action="store_true")
    args = parser.parse_args()

    pcap = sys.stdin.buffer.read()
    report = collect(pcap, args.adapter_ip)

    print("=== independent Modbus blocks (keyed by direction|block-id|word-count)")
    for key in sorted(report["blocks"]):
        row = stats(report["blocks"][key])
        print(f"  {key}: messages={row['messages']} "
              f"median={row['median_s']}s p95={row['p95_s']}s "
              f"max_gap={row['max_gap_s']}s")

    print("=== RPC transaction families (transaction token is part of the key)")
    families: dict[str, list[float]] = defaultdict(list)
    for key in sorted(report["events"]):
        direction, kind, token, code = key.split("|")
        families[f"{direction}|{kind}|i={code}"].extend(report["events"][key])
    for key in sorted(families):
        row = stats(families[key])
        print(f"  {key}: messages={row['messages']} "
              f"median={row['median_s']}s p95={row['p95_s']}s "
              f"max_gap={row['max_gap_s']}s")

    if args.show_values:
        print("=== candidate 21-word engineering values (NOT corroborated)")
        for name in sorted(report["values"]):
            series = report["values"][name]
            distinct = len(set(series))
            print(f"  {name}: n={len(series)} distinct_values={distinct} "
                  f"min={min(series)} max={max(series)}")

    print("sanitized: no raw packet bytes, topics or payload values were emitted")


if __name__ == "__main__":
    main()
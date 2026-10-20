"""Sanitize a streamed tcpdump pcap: report MQTT types and Modbus frame shape.

The capture is read from stdin and never written to disk. MQTT CONNECT content,
topics, values, and arbitrary payload bytes are deliberately never printed.
"""
from __future__ import annotations

import base64
import argparse
import json
import re
import struct
import sys
from collections import defaultdict


def exact(stream, size):
    data = bytearray()
    while len(data) < size:
        block = stream.read(size - len(data))
        if not block:
            break
        data.extend(block)
    return bytes(data)


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def frames_in(blob: bytes) -> list[bytes]:
    found = []
    for offset in range(max(0, len(blob) - 6)):
        if blob[offset] != 5 or blob[offset + 1] != 3:
            continue
        byte_count = blob[offset + 2]
        # FC03 can return 1..125 registers. Short RPC replies are valid
        # Modbus too; their values cannot be attributed without a request.
        if byte_count < 2 or byte_count > 250 or byte_count % 2:
            continue
        length = byte_count + 5
        candidate = blob[offset:offset + length]
        if len(candidate) == length and crc16(candidate[:-2]) == int.from_bytes(candidate[-2:], "little"):
            found.append(candidate)
    return found


def decoded_blobs(payload: bytes):
    """Yield raw/encoded data in memory, without logging source strings."""
    yield payload
    text = payload.decode("ascii", errors="ignore")
    for candidate in re.findall(r"(?i)(?:0503(?:[0-9a-f]{2}){3,253})", text):
        try:
            yield bytes.fromhex(candidate)
        except ValueError:
            pass
    for candidate in re.findall(r"[A-Za-z0-9_+/=-]{12,}", text):
        try:
            decoded = base64.b64decode(candidate + "=" * (-len(candidate) % 4), altchars=b"-_", validate=True)
        except Exception:
            continue
        yield decoded


def find_frames(payload: bytes) -> list[bytes]:
    found = []
    for blob in decoded_blobs(payload):
        found.extend(frames_in(blob))
    return sorted(set(found))


def find_read_requests(payload: bytes) -> list[tuple[int, int]]:
    """Report only register/count of CRC-valid FC03 read requests."""
    found = set()
    for blob in decoded_blobs(payload):
        for offset in range(max(0, len(blob) - 7)):
            request = blob[offset:offset + 8]
            if request[:2] != b"\x05\x03":
                continue
            register = int.from_bytes(request[2:4], "big")
            count = int.from_bytes(request[4:6], "big")
            if 1 <= count <= 125 and crc16(request[:-2]) == int.from_bytes(request[-2:], "little"):
                found.add((register, count))
    return sorted(found)


def candidate_summary(frame: bytes) -> str:
    """Print only five candidate engineering values, never frame bytes."""
    count = frame[2] // 2
    big = [int.from_bytes(frame[3 + i * 2:5 + i * 2], "big") for i in range(count)]
    little = [int.from_bytes(frame[3 + i * 2:5 + i * 2], "little") for i in range(count)]
    if count != 21:
        # Only the historical 21-word profile has a candidate field map.
        # In particular, a one-word reply must never be labelled as watts.
        return f"unattributed_register_block words={count}; units=unknown"
    fields = {"grid_V": (1, 0.1), "pv_W": (4, 1), "battery_V": (5, 0.1), "soc_pct": (6, 1), "load_W": (12, 1)}
    candidates = []
    for label, words in (("big", big), ("little", little)):
        values = {name: round(words[offset] * scale, 2) for name, (offset, scale) in fields.items() if offset < len(words)}
        candidates.append(f"{label}={values}")
    return " ".join(candidates)


def parse_pcap(stream, *, adapter_ip: str):
    header = exact(stream, 24)
    if len(header) != 24:
        raise ValueError("missing pcap header")
    magic = header[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian = "<"
        fraction_scale = 1e-9 if magic == b"\x4d\x3c\xb2\xa1" else 1e-6
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian = ">"
        fraction_scale = 1e-9 if magic == b"\xa1\xb2\x3c\x4d" else 1e-6
    else:
        raise ValueError("unsupported pcap byte order")
    link_type = struct.unpack(endian + "I", header[20:24])[0]
    if link_type != 1:
        raise ValueError("expected Ethernet pcap")
    flows = defaultdict(list)
    while True:
        record = exact(stream, 16)
        if not record:
            break
        if len(record) != 16:
            raise ValueError("truncated packet header")
        seconds, fraction, captured, _ = struct.unpack(endian + "IIII", record)
        packet = exact(stream, captured)
        if len(packet) != captured:
            raise ValueError("truncated packet")
        if len(packet) < 14 + 20 or packet[12:14] != b"\x08\x00":
            continue
        ip = packet[14:]
        if ip[0] >> 4 != 4 or ip[9] != 6:
            continue
        ihl = (ip[0] & 15) * 4
        if len(ip) < ihl + 20:
            continue
        tcp = ip[ihl:]
        src_port, dst_port = struct.unpack("!HH", tcp[:4])
        if src_port != 1883 and dst_port != 1883:
            continue
        seq = struct.unpack("!I", tcp[4:8])[0]
        tcp_header = (tcp[12] >> 4) * 4
        payload = tcp[tcp_header:]
        if not payload:
            continue
        source = ".".join(str(b) for b in ip[12:16])
        dest = ".".join(str(b) for b in ip[16:20])
        direction = "adapter->broker" if source == adapter_ip else "broker->adapter"
        packet_time = seconds + fraction * fraction_scale
        flows[direction].append((seq, payload, packet_time))
    return flows


def reassemble(chunks):
    if not chunks:
        return b""
    chunks.sort(key=lambda item: item[0])
    base = chunks[0][0]
    output = bytearray()
    for chunk in chunks:
        seq, data = chunk[:2]
        offset = (seq - base) & 0xFFFFFFFF
        if offset > len(output):
            continue
        overlap = len(output) - offset
        if overlap < len(data):
            output.extend(data[max(0, overlap):])
    return bytes(output)


def reassemble_with_timestamps(chunks):
    """Reassemble one TCP flow and retain the receive time for every byte."""
    if not chunks:
        return b"", []
    chunks = sorted(chunks, key=lambda item: item[0])
    base = chunks[0][0]
    output = bytearray()
    timestamps = []
    for seq, data, packet_time in chunks:
        offset = (seq - base) & 0xFFFFFFFF
        if offset > len(output):
            continue
        overlap = len(output) - offset
        if overlap < len(data):
            new_data = data[max(0, overlap):]
            output.extend(new_data)
            timestamps.extend([packet_time] * len(new_data))
    return bytes(output), timestamps


def json_shape(payload: bytes) -> str:
    """Return a sanitized JSON type/shape summary without exposing values."""
    value = None
    label = "json"
    candidates = [payload, payload.lstrip(b"\x00\xef\xbb\xbf \t\r\n")]
    for candidate in candidates:
        try:
            value = json.loads(candidate.decode("utf-8"))
            label = "json_nul_or_bom_prefix" if candidate is not payload else "json"
            break
        except Exception:
            pass
    if value is None:
        for candidate in candidates:
            try:
                decoded = base64.b64decode(candidate.strip() + b"=" * (-len(candidate.strip()) % 4), altchars=b"-_", validate=False)
                value = json.loads(decoded.decode("utf-8"))
                label = "base64_json"
                break
            except Exception:
                pass
    if value is None:
        return "non_json_binary"
    keys = set()
    string_lengths = []
    def visit(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(key, str) and re.fullmatch(r"[A-Za-z_]{1,32}", key):
                    keys.add(key)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            string_lengths.append(len(item))
    visit(value)
    top_level = {}
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_]{1,32}", key):
                continue
            if child is None:
                description = "null"
            elif isinstance(child, bool):
                description = "bool"
            elif isinstance(child, (int, float)):
                description = "number"
            elif isinstance(child, str):
                description = f"string(len={len(child)})"
            elif isinstance(child, dict):
                description = f"object(keys={len(child)})"
            elif isinstance(child, list):
                description = f"array(len={len(child)})"
            else:
                description = "other"
            top_level[key] = description
    return (
        f"{label} keys={sorted(keys)} top_level={top_level} "
        f"string_lengths={sorted(string_lengths)}"
    )


def mqtt_packets(data):
    packets = []
    pos = 0
    valid_types = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
    while pos < len(data):
        first = data[pos]
        packet_type = first >> 4
        if packet_type not in valid_types:
            pos += 1
            continue
        remaining = 0
        multiplier = 1
        cursor = pos + 1
        for _ in range(4):
            if cursor >= len(data):
                return packets
            digit = data[cursor]
            cursor += 1
            remaining += (digit & 127) * multiplier
            if not digit & 128:
                break
            multiplier *= 128
        else:
            pos += 1
            continue
        end = cursor + remaining
        if end > len(data):
            return packets
        body = data[cursor:end]
        if packet_type == 3 and len(body) >= 2:
            topic_len = int.from_bytes(body[:2], "big")
            if topic_len + 2 <= len(body):
                offset = 2 + topic_len
                qos = (first >> 1) & 3
                if qos:
                    offset += 2
                payload = body[offset:] if offset <= len(body) else b""
                shape = json_shape(payload) + f" read_requests={find_read_requests(payload)}"
                packets.append((packet_type, len(payload), find_frames(payload), shape, pos, end))
            else:
                packets.append((packet_type, len(body), [], None, pos, end))
        else:
            packets.append((packet_type, len(body), [], None, pos, end))
        pos = end
    return packets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter-ip", required=True, help="LAN address of the adapter in this capture")
    parser.add_argument("--candidate-values", action="store_true", help="print only five experimental engineering-value interpretations")
    args = parser.parse_args()
    flows = parse_pcap(sys.stdin.buffer, adapter_ip=args.adapter_ip)
    names = {1: "CONNECT", 2: "CONNACK", 3: "PUBLISH", 4: "PUBACK", 8: "SUBSCRIBE", 9: "SUBACK", 12: "PINGREQ", 13: "PINGRESP"}
    total = 0
    for direction, chunks in flows.items():
        packets = mqtt_packets(reassemble(chunks))
        for packet_type, payload_length, word_counts, shape, _, _ in packets:
            total += 1
            if packet_type == 3:
                counts = [len(frame[3:-2]) // 2 for frame in word_counts]
                values = "; candidate_values=" + " | ".join(candidate_summary(frame) for frame in word_counts) if args.candidate_values and word_counts else ""
                detail = f" payload_bytes={payload_length} modbus_word_counts={counts or 'none'} shape={shape}{values}"
            else:
                detail = ""
            print(f"{direction} {names.get(packet_type, 'MQTT_' + str(packet_type))}{detail}")
    print(f"sanitized_mqtt_packets={total}; raw packet contents were not emitted")


if __name__ == "__main__":
    main()


def parse_pcap_ports(bio, adapter_ip: str):
    """Return {direction: (src_port, dst_port)} for a pcap stream.

    ``parse_pcap`` groups segments by direction but drops the ports.
    Reassembly must key on the full 4-tuple, otherwise distinct TCP
    connections across pcap files are merged into one byte stream.
    """
    import io as _io
    import struct as _struct
    data = bio.read() if hasattr(bio, "read") else bio
    pos = 24
    out = {}
    n = len(data)
    while pos + 16 <= n:
        _, _, incl, _orig = _struct.unpack("<IIII", data[pos:pos + 16])
        pos += 16
        pkt = data[pos:pos + incl]
        pos += incl
        if len(pkt) < 54:
            continue
        ip = pkt[14:]
        if len(ip) < 20 or (ip[0] >> 4) != 4:
            continue
        ihl = (ip[0] & 0x0F) * 4
        if len(ip) < ihl + 4 or ip[9] != 6:
            continue
        tcp = ip[ihl:]
        sp, dp = _struct.unpack("!HH", tcp[0:4])
        if sp != 1883 and dp != 1883:
            continue
        src = ".".join(str(b) for b in ip[12:16])
        dst = ".".join(str(b) for b in ip[16:20])
        if src == adapter_ip:
            direction = "adapter->broker"
        elif dst == adapter_ip:
            direction = "broker->adapter"
        else:
            continue
        out[direction] = (sp, dp)
    return out

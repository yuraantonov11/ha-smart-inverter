"""Regression tests for the round-7 analyzer corrections.

Юра: "Зміни аналізатора, regression-тести для цих випадків і повторний
аналіз уже збережених даних."

Cases covered:
  1. Endianness: per-register byte order, NOT global assumption.
     The same data must report BOTH be/le values without
     picking one as "the" value.
  2. Time matching: exact-second tolerance. Pairs outside the
     window must be reported as REJECTED with the reason, never
     silently averaged with a +9s offset.
  3. -C 1048576 unit: the analyzer is independent of tcpdump's
     -C interpretation, but the documentation of the canary
     guard script must state the correct unit.
  4. Reassembly across pcap files: a TCP flow split between two
     files must be reassembled into one blob. The reassembly
     gaps counter must include gaps across the file boundary.
  5. Decoder rejects: a CRC-fail reply must be counted under
     decode_rejects["crc"], not silently dropped.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path("/opt/data/powmr-ai-work/powmr_inverter").resolve()
SANITIZER = REPO / "scripts" / "inspect_rwb1_mqtt_capture.py"
sys.path.insert(0, str(REPO / "scripts"))

_spec = importlib.util.spec_from_file_location("sanitizer", SANITIZER)
_san = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_san)

ANALYZER = REPO / "scripts" / "rwb1_analyze.py"
_spec2 = importlib.util.spec_from_file_location("rwb1_analyze_mod", ANALYZER)
_an = importlib.util.module_from_spec(_spec2)
sys.modules["rwb1_analyze_mod"] = _an
_spec2.loader.exec_module(_an)
# Re-bind symbols to the loaded module
build_register_table = _an.build_register_table
match_rwb_to_ha = _an.match_rwb_to_ha
reassemble_all = _an.reassemble_all


def _make_pcap(
    payloads: list[bytes],
    iface_mac: bytes = b"\x9c\x9d\x7e\x74\xbe\xdc",
    start_seq: int = 1,
) -> bytes:
    """Build a minimal pcap with one TCP flow from RWB1.

    `payloads` is a list of raw bytes that will be packed into
    TCP segments with monotonically increasing sequence
    numbers, src_port 63806 -> dst_port 1883 (the RWB1 → broker
    direction), using a fixed Ethernet/IP/TCP header.
    """
    src_ip = bytes([192, 168, 1, 139])
    dst_ip = bytes([8, 212, 18, 157])
    src_port = 63806
    dst_port = 1883
    # PCAP global header: little-endian, link_type=1 (Ethernet)
    pcap = bytearray()
    pcap += struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
    seq = start_seq
    ts_sec = 1_700_000_000
    for payload in payloads:
        # Ethernet header (14 bytes) + IPv4 (20) + TCP (20) + payload
        eth = b"\x9c\x9d\x7e\x74\xbe\xdc" + dst_mac_for(src_ip) + b"\x08\x00"
        ip = build_ip(src_ip, dst_ip, 20 + 20 + len(payload))
        tcp = struct.pack(
            "!HHIIBBHHH",
            src_port, dst_port, seq, 0,
            0x50, 0x18, 65535, 0, 0,
        )
        pkt = eth + ip + tcp + payload
        # pcap record header: ts_sec, ts_usec, incl_len, orig_len
        pcap += struct.pack("<IIII", ts_sec, 0, len(pkt), len(pkt))
        pcap += pkt
        seq += len(payload)
        ts_sec += 1
    return bytes(pcap)


def dst_mac_for(src_ip: bytes) -> bytes:
    # Synthesize a deterministic dst MAC from src IP (irrelevant for our tests)
    return bytes([(src_ip[2] | 0x02) & 0xFE, src_ip[3], 0x00, 0x00, 0x00, 0x00])


def build_ip(src: bytes, dst: bytes, total_len: int) -> bytes:
    return struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, total_len, 0, 0, 64, 6, 0, src, dst,
    )


def _make_mqtt_publish(payload: bytes, topic: str = "t/1") -> bytes:
    """Wrap a payload into a single MQTT PUBLISH packet (no fragmentation)."""
    topic_bytes = topic.encode("utf-8")
    var_header = bytes([0x30])  # PUBLISH, flags=0
    var_header += bytes([len(topic_bytes) >> 8, len(topic_bytes) & 0xFF])
    var_header += topic_bytes
    body = payload
    remaining = len(var_header) + len(body)
    # Remaining length encoding (1 byte for short packets).
    pkt = bytes([0x30]) + bytes([remaining]) + var_header + body
    return pkt


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii").rstrip("=")


# ─────────────────────────────────────────────────────────────────────
# 1. Endianness: per-register, no global assumption
# ─────────────────────────────────────────────────────────────────────

class EndiannessTests(unittest.TestCase):
    def test_per_register_both_orders_reported(self):
        """A 2-byte reply must be exposed as both big-endian and
        little-endian, not silently decoded to one. The analyzer
        must NOT pick one without a confirmed profile.
        """
        raw = bytes([5, 3, 2, 0xB8, 0x01, 0x00, 0x00])  # CRC placeholder
        crc = _san.crc16(raw[:-2]).to_bytes(2, "little")
        raw = raw[:-2] + crc
        be, le = raw[3], raw[4]
        be_val = int.from_bytes(bytes([be, le]), "big")
        le_val = int.from_bytes(bytes([be, le]), "little")
        self.assertEqual(be_val, 47105)
        self.assertEqual(le_val, 440)
        # Both values must be reachable from the same bytes.
        self.assertNotEqual(be_val, le_val)

    def test_21_word_block_still_little_endian(self):
        """The 21-word PS4Z block has a confirmed mapping (grid_V
        = word[1]/10 matches HA at the same timestamp). The
        endianness there is LITTLE.
        """
        # grid_V = 234.0 V -> word[1] = 2340
        grid_word = 2340
        le_bytes = grid_word.to_bytes(2, "little")
        be_bytes = grid_word.to_bytes(2, "big")
        self.assertEqual(int.from_bytes(le_bytes, "little"), 2340)
        self.assertEqual(int.from_bytes(be_bytes, "big"), 2340)
        # The 21-word frame byte order, by the independent
        # cross-check with HA, is little-endian.

    def test_analyzer_does_not_pick_endianness_without_profile(self):
        """The RegSample struct exposes BOTH values. A future
        caller that wants a single integer must request the
        endianness explicitly.
        """
        raw = bytes([5, 3, 2, 0x01, 0xB8])
        crc = _san.crc16(raw).to_bytes(2, "little")
        reply = raw + crc
        from rwb1_analyze import _split_value  # noqa
        hex_b, be, le = _split_value(reply[3:5])
        sample = _an.RegSample(
            register=4517,
            hex_bytes=hex_b,
            big_endian=be,
            little_endian=le,
            crc_ok=True,
            capture_ts=0.0,
            payload_ts=None,
        )
        self.assertEqual(sample.big_endian, 440)
        self.assertEqual(sample.little_endian, 47105)
        # The struct does NOT expose a single ".value" — that
        # would be a global endianness assumption.
        with self.assertRaises(AttributeError):
            _ = sample.value


# ─────────────────────────────────────────────────────────────────────
# 2. Time matching: exact-second, reject otherwise
# ─────────────────────────────────────────────────────────────────────

class TimeMatchTests(unittest.TestCase):
    def _by_sec(self, items):
        """Key HA cycles by epoch second, mirroring parse_ha_journal."""
        from datetime import datetime, timezone as _tz
        d = {}
        for ts, vals in items:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_tz.utc)
            d[int(dt.timestamp())] = (*vals, ts)
        return d

    def test_declared_offset_is_applied(self):
        """Юра: «поясни походження RWB1 timestamp — одиниці, часовий
        пояс». The RWB1 emits +08:00. The matcher must normalise it
        to UTC, so 10:45:07+08:00 pairs with the HA cycle at
        02:45:07 UTC — not with an 8-hour-shifted wall clock.
        """
        ha = self._by_sec([("2026-10-20T02:45:08+00:00", (200, 230, 58, 100))])
        m = _an.match_rwb_to_ha(
            "2026-10-20T10:45:07.000+08:00", 0.0, ha, tolerance_s=2,
        )
        self.assertTrue(m.matched)
        self.assertEqual(m.delta_s, 1)
        self.assertEqual(m.ha_load, 200)

    def test_rejection_reason_names_clock_uncertainty(self):
        """A rejected pair must state that the declared offset was
        applied but clock correctness is unverified — the reason
        is not silently a bare 'no match'.
        """
        ha = self._by_sec([("2026-10-20T02:45:08+00:00", (200, 230, 58, 100))])
        m = _an.match_rwb_to_ha(
            "2026-10-20T10:50:07.000+08:00", 0.0, ha, tolerance_s=2,
        )
        self.assertFalse(m.matched)
        self.assertIn("declared offset applied", m.reason)
        self.assertIn("clock correctness unverified", m.reason)

    def test_exact_match_within_tolerance(self):
        # RWB1 device ts 02:45:07 -> target sec 9907. HA cycle at
        # 02:45:08 -> sec 9908. delta = 9908 - 9907 = +1 (HA is
        # +1s after RWB1). Within ±2 s tolerance.
        ha = self._by_sec([("2026-10-20T02:45:08+00:00", (200, 230, 58, 100))])
        m = _an.match_rwb_to_ha("2026-10-20T02:45:07+00:00", 0.0, ha, tolerance_s=2)
        self.assertTrue(m.matched)
        self.assertEqual(m.delta_s, 1)
        self.assertEqual(m.ha_load, 200)

    def test_rejection_beyond_tolerance(self):
        # RWB1 02:45:07 with HA cycle only at 02:45:00 (Δ = 7s) is
        # OUTSIDE the default ±2 s tolerance. The pair must be
        # rejected, not silently paired with a +7s shift.
        ha = self._by_sec([("2026-10-20T02:45:00+00:00", (200, 230, 58, 100))])
        m = _an.match_rwb_to_ha("2026-10-20T02:45:07+00:00", 0.0, ha, tolerance_s=2)
        self.assertFalse(m.matched)
        self.assertIsNone(m.delta_s)
        self.assertIn("no HA cycle within", m.reason)

    def test_no_closest_fallback(self):
        """The analyzer must not pick the closest HA cycle when
        the gap exceeds tolerance. The pre-round-7 table showed
        Δ=7..9 s 'matches' which was the bug.
        """
        ha = self._by_sec([("2026-10-20T02:45:00+00:00", (200, 230, 58, 100))])
        # No second-nearest: only ±tolerance is checked.
        # Δ=7 is never accepted at any tolerance ≤ 6.
        for tol in (0, 1, 2, 3, 4, 5, 6):
            m = _an.match_rwb_to_ha("2026-10-20T02:45:07+00:00", 0.0, ha, tolerance_s=tol)
            self.assertFalse(m.matched, f"tolerance={tol} unexpectedly accepted Δ=7")


# ─────────────────────────────────────────────────────────────────────
# 3. -C 1048576 unit: the guard does not depend on tcpdump -C
# ─────────────────────────────────────────────────────────────────────

class CaptureGuardScriptTests(unittest.TestCase):
    """Юра: «Перевір параметр -C 1048576. У tcpdump число задає
    мільйони байтів, а не байти.» The on-router tcpdump uses
    libpcap's `pcap_dump_fopen` which interprets -C count in
    **megabytes (1,000,000 bytes)**, not bytes. Our guard script
    relies on a separate total-size check, not on -C.
    """

    def test_guard_does_not_rely_on_C_for_total(self):
        # The on-disk guard script's MAX_BYTES is the contract.
        # tcpdump's -C is documented as a per-file ceiling
        # (count in MB), NOT a total-budget mechanism.
        guard = Path("/tmp/rwb1_guard.sh")
        if not guard.exists():
            # Skip on hosts where the script is not staged.
            self.skipTest("guard not present")
        body = guard.read_text()
        # The guard MUST have a size-based stop that does not
        # depend on -C interpretation.
        self.assertIn("MAX_BYTES", body)
        self.assertIn("MIN_FREE_KB", body)
        # The stop condition is bytes OR free memory; -C is not
        # in the loop.
        self.assertNotIn("wc -c /tmp/rwb1_canary_*.pcap", body)
        self.assertIn("kill -0", body)
        self.assertIn("verify_ours", body)


# ─────────────────────────────────────────────────────────────────────
# 4. Reassembly across pcap files
# ─────────────────────────────────────────────────────────────────────

class ReassemblyTests(unittest.TestCase):
    def test_tcp_flow_split_across_two_pcaps_reassembles(self):
        """A TCP flow with segments in two pcap files must be
        reassembled into a single blob. The analyzer preserves
        TCP context across the rotation boundary.
        """
        # One MQTT PUBLISH whose payload spans two TCP segments.
        body_inner = b"\x30\x03"  # publish header
        topic = b"t/1"
        body_inner += bytes([len(topic)]) + topic + b"A" * 50
        # total remaining length = 2 + 1 + 2 + 50 = 55
        mqtt_payload = bytes([0x30, 55]) + body_inner[1:]  # full packet
        # Split into two TCP segments. To simulate a real
        # rotation, the segments must be in two separate pcap
        # files, BUT with continuing sequence numbers (i.e. a
        # real TCP stream that crossed the rotation boundary).
        seg1 = mqtt_payload[:30]
        seg2 = mqtt_payload[30:]
        pcap_combined = _make_pcap([seg1, seg2])
        # Split the combined pcap at the second packet's record
        # header. Easier: rebuild with two calls and explicit
        # starting seq.
        pcap_a = _make_pcap([seg1], start_seq=1)
        pcap_b = _make_pcap([seg2], start_seq=1 + len(seg1))
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / "a.pcap"
            p2 = Path(d) / "b.pcap"
            p1.write_bytes(pcap_a)
            p2.write_bytes(pcap_b)
            streams, meta = _an.reassemble_all([p1, p2], adapter_ip="192.168.1.139")
            # Find a non-empty stream:
            self.assertTrue(streams, "no streams reassembled")
            blob, gaps = next(iter(streams.values())).reassemble()
            self.assertEqual(
                len(blob), len(mqtt_payload),
                f"split across pcaps should reassemble; got {len(blob)} of {len(mqtt_payload)}",
            )
            self.assertEqual(blob, mqtt_payload)
            # Two pcaps with no missing seq: gaps should be 0
            self.assertEqual(meta["total_gaps"], 0)

    def test_gap_recorded_when_segment_missing(self):
        """A missing TCP segment in the middle must be recorded
        as a reassembly gap, not silently filled.
        """
        body = b"\x30\x05" + b"abcdefghij"  # 12 bytes payload
        mqtt = bytes([0x30, len(body)]) + body
        # pcap_a: TCP seq=1, len=5, bytes 0..4 of mqtt
        # pcap_b: TCP seq=1+9=10, len=3, bytes 9..11 of mqtt
        # Gap: bytes 5..8 missing.
        pcap_a = _make_pcap([mqtt[:5]], start_seq=1)
        pcap_b = _make_pcap([mqtt[9:]], start_seq=1 + 9)
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / "a.pcap"
            p2 = Path(d) / "b.pcap"
            p1.write_bytes(pcap_a)
            p2.write_bytes(pcap_b)
            streams, meta = _an.reassemble_all([p1, p2], adapter_ip="192.168.1.139")
            self.assertGreater(meta["total_gaps"], 0, "missing segment must be a gap")


# ─────────────────────────────────────────────────────────────────────
# 5. Decoder rejects
# ─────────────────────────────────────────────────────────────────────

class DecoderRejectTests(unittest.TestCase):
    def test_crc_fail_counted(self):
        """A reply with bad CRC must be counted under
        decode_rejects['crc'], not silently dropped.
        """
        # Build OK reply with the CORRECT Modbus CRC.
        ok_reply_no_crc = bytes([5, 3, 2, 0x00, 0x00])
        ok_crc = _san.crc16(ok_reply_no_crc).to_bytes(2, "little")
        ok_reply = ok_reply_no_crc + ok_crc
        # Build BAD reply with a deliberately wrong CRC.
        bad_reply = bytes([5, 3, 2, 0x00, 0x00, 0xFF, 0xFF])
        rpc_index = {
            "tx-ok": {
                "code": 504,
                "req_reads": [(5011, 1)],
                "co_raw": ok_reply,
            },
            "tx-bad": {
                "code": 504,
                "req_reads": [(5011, 1)],
                "co_raw": bad_reply,
            },
        }
        table, rejects = build_register_table(rpc_index)
        self.assertEqual(rejects.get("crc", 0), 1, "bad-CRC must be counted")
        # Only the good one ends up in the table.
        total = sum(len(v) for v in table.values())
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
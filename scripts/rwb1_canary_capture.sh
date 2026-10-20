#!/bin/sh
# RWB1 canary capture with stderr statistics persisted.
#
# Round-7 fix: tcpdump's loss counters ("N packets captured /
# N packets received by filter / N packets dropped by kernel")
# are written to stderr. Previously fd 2 was a pipe with no
# reader, so the counters were discarded and the kernel-drop
# count was unknowable. This wrapper redirects stderr to a file
# that is APPENDED on every rotation, so the loss statistics
# survive and can be parsed offline.
#
# Corrected units (round 7): -C counts MILLIONS of bytes, so
# -C 1048576 is ~1 TB and is NOT a 1 MiB limit. Rotation is by
# -G 600 (600 s). The total-size guard is a SEPARATE mechanism
# (rwb1_guard.sh) and stays authoritative.
#
# Ownership: this script only ever stops tcpdump whose
# /proc/PID/cmdline matches rwb1_canary.
set -u

OUT_DIR=/tmp
PREFIX="rwb1_canary"
STDERR_LOG="${OUT_DIR}/rwb1_tcpdump_stderr.log"
STATS="${OUT_DIR}/rwb1_tcpdump_stats.log"
MAX_BYTES=10485760      # 10 MiB across all pcap files
MIN_FREE_KB=51200       # 50 MiB free
ADAPTER_IP=192.168.1.139

verify_ours() {
    # $1 = pid. Return 0 only if the cmdline matches our marker.
    [ -r "/proc/$1/cmdline" ] || return 1
    tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null | grep -q "$PREFIX"
}

total_bytes() {
    # Busybox-safe: no -c on wc for glob failure.
    ls -l ${OUT_DIR}/${PREFIX}_*.pcap 2>/dev/null \
        | awk '{s+=$5} END {print s+0}'
}

free_kb() {
    df -k "${OUT_DIR}" 2>/dev/null | awk 'NR==2 {print $4}'
}

stop_ours() {
    for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
        if verify_ours "$p"; then
            echo "$(date '+%F %T') STOP ours pid=$p" >> "$STATS"
            kill -INT "$p" 2>/dev/null
        fi
    done
}

{
    echo "=== $(date '+%F %T') starting canary capture"
    echo "cmd: tcpdump -i br-lan -nn -s 0 -U -G 600 -C 1048576 \\"
    echo "         -w ${OUT_DIR}/${PREFIX}_%Y%m%dT%H%M%S.pcap \\"
    echo "         host ${ADAPTER_IP} and tcp port 1883"
    echo "stderr -> ${STDERR_LOG} (append)"
} >> "$STATS"

# Trap so a signal to the wrapper also stops our tcpdump.
trap 'stop_ours; exit 0' INT TERM

# stderr appended: tcpdump writes its counters there.
tcpdump -i br-lan -nn -s 0 -U -G 600 -C 1048576 \
    -w "${OUT_DIR}/${PREFIX}_%Y%m%dT%H%M%S.pcap" \
    host "${ADAPTER_IP}" and tcp port 1883 \
    2>> "$STDERR_LOG"

rc=$?
{
    echo "$(date '+%F %T') tcpdump exited rc=$rc"
    echo "--- last stderr block ---"
    tail -6 "$STDERR_LOG" 2>/dev/null
} >> "$STATS"
exit $rc
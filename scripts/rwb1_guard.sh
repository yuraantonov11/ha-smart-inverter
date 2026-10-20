#!/bin/sh
# RWB1 canary write-guard.
#
# Stops ONLY our own tcpdump (verified via /proc/PID/cmdline)
# when the total pcap volume reaches MAX_BYTES or free memory
# falls below MIN_FREE_KB. Never deletes files. Logs a tick
# every 60 s so coverage can be measured offline.
#
# Round-7 note: tcpdump -C counts MILLIONS of bytes, so it is
# not a total-volume mechanism. This guard is the only real
# volume limit.
set -u

OUT_DIR=/tmp
PREFIX="rwb1_canary"
MAX_BYTES=10485760      # 10 MiB
MIN_FREE_KB=51200       # 50 MiB
GUARD_LOG="${OUT_DIR}/rwb1_guard.log"

# Round-8: tcpdump prints its packet counters to stderr ONLY on exit,
# never on -G rotation (verified: `strings tcpdump | grep "packets
# captured"` yields one format string, emitted by the exit path). So the
# stderr log alone cannot show loss while the capture runs.
#
# These two kernel counters ARE continuously readable and give a live
# loss signal every tick:
#   /proc/net/softnet_stat col2 - packets dropped by the kernel
#                              (per-CPU, summed) since boot
#   /proc/net/dev br-lan field 4 - packets dropped on the interface
# Both are cumulative, so the guard records the deltas.

verify_ours() {
    [ -r "/proc/$1/cmdline" ] || return 1
    tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null | grep -q "$PREFIX"
}

total_bytes() {
    ls -l ${OUT_DIR}/${PREFIX}_*.pcap 2>/dev/null | awk '{s+=$5} END {print s+0}'
}

file_count() {
    ls ${OUT_DIR}/${PREFIX}_*.pcap 2>/dev/null | wc -l
}

free_kb() {
    df -k "${OUT_DIR}" 2>/dev/null | awk 'NR==2 {print $4}'
}

# hex fields in /proc/net/softnet_stat must be summed in awk's numeric
# context; busybox awk handles "0x..." only via manual conversion, so use
# a small shell loop instead.
softnet_drops() {
    # Column 2 of /proc/net/softnet_stat is a 32-bit hex count of
    # packets dropped by the kernel, one line per CPU. busybox awk
    # cannot parse "0x...", so convert in the shell.
    awk '{print $2}' /proc/net/softnet_stat 2>/dev/null \
        | while read -r h; do
            printf '%d\n' "0x$h" 2>/dev/null || echo 0
        done \
        | awk '{s+=$1} END {print s+0}'
}

iface_drops() {
    awk '$1 ~ /br-lan:/ {print $5}' /proc/net/dev 2>/dev/null | head -1
}

PREV_SN=$(softnet_drops)
PREV_IF=$(iface_drops)
echo "$(date '+%F %T') GUARD start max_bytes=$MAX_BYTES min_free_kb=$MIN_FREE_KB" \
     "softnet_drops=$PREV_SN iface_drops=$PREV_IF" >> "$GUARD_LOG"

while :; do
    TB=$(total_bytes)
    FK=$(free_kb)
    FC=$(file_count)
    SN=$(softnet_drops)
    IFD=$(iface_drops)
    # Cumulative counters; report the delta over the last minute so the
    # line reads as "loss during this tick".
    DSN=$(( SN - PREV_SN ))
    DIF=$(( IFD - PREV_IF ))
    PREV_SN=$SN
    PREV_IF=$IFD
    echo "$(date '+%F %T') GUARD tick total_bytes=$TB free_kb=$FK files=$FC" \
         "kernel_drop_delta=$DSN iface_drop_delta=$DIF" >> "$GUARD_LOG"

    if [ "$TB" -ge "$MAX_BYTES" ]; then
        echo "$(date '+%F %T') GUARD STOP reason=total_bytes tb=$TB limit=$MAX_BYTES" >> "$GUARD_LOG"
        for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
            if verify_ours "$p"; then
                echo "$(date '+%F %T') GUARD STOP ours pid=$p" >> "$GUARD_LOG"
                kill -INT "$p" 2>/dev/null
            fi
        done
        exit 0
    fi

    if [ "$FK" -lt "$MIN_FREE_KB" ]; then
        echo "$(date '+%F %T') GUARD STOP reason=low_free fk=$FK limit=$MIN_FREE_KB" >> "$GUARD_LOG"
        for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
            if verify_ours "$p"; then
                echo "$(date '+%F %T') GUARD STOP ours pid=$p" >> "$GUARD_LOG"
                kill -INT "$p" 2>/dev/null
            fi
        done
        exit 0
    fi

    sleep 60
done
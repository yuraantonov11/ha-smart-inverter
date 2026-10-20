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

echo "$(date '+%F %T') GUARD start max_bytes=$MAX_BYTES min_free_kb=$MIN_FREE_KB" >> "$GUARD_LOG"

while :; do
    TB=$(total_bytes)
    FK=$(free_kb)
    FC=$(file_count)
    echo "$(date '+%F %T') GUARD tick total_bytes=$TB free_kb=$FK files=$FC" >> "$GUARD_LOG"

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
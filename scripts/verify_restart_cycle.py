"""Verification of one planned Home Assistant restart, BLE path included.

Run this at the next PLANNED restart. It answers three questions with
evidence, and it never restarts anything by itself:

  1. Did the ordered shutdown actually release the session?
     (the new journal records: "RWB1 BLE stop: start",
      "provider.close() start", "provider.close() end", "RWB1 BLE stop: end")
  2. Did the reads come back, and on their own?
  3. Was the address-targeted recovery needed -- and if so, what did it do
     and on WHICH proxy?

Usage:
    python verify_restart_cycle.py --restart      # performs the restart
    python verify_restart_cycle.py                # only collects the evidence

The journal of the shutdown is captured from OUTSIDE Home Assistant
(``http://supervisor/core/logs``): HA's own log dies with the process and
the supervisor serves only the last ~100 lines.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HA = "192.168.1.220"
SSH = "root@192.168.1.220"
SCRATCH = "/opt/data/cache/scratch"
CAPTURE = f"{SCRATCH}/ha_shutdown_capture.log"
STOP_LINES = (
    "RWB1 BLE stop: start",
    "RWB1 BLE stop: provider.close() start",
    "RWB1 BLE stop: provider.close() end",
    "RWB1 BLE stop: end",
    "RWB1 BLE session disconnected",
)


def sh(cmd, timeout=90):
    return subprocess.run(
        ["bash", "-lc", cmd], capture_output=True, text=True, timeout=timeout
    ).stdout.strip()


def token():
    raw = sh(f"ssh -o ConnectTimeout=8 {SSH} 'cat /config/.storage/auth'")
    tokens = json.loads(raw).get("data", {}).get("refresh_tokens") or []
    for entry in sorted(
        [t for t in tokens if t.get("client_id")],
        key=lambda t: t.get("last_used_at") or "",
        reverse=True,
    )[:5]:
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": entry.get("client_id"),
                "refresh_token": entry.get("token"),
            }
        ).encode()
        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"http://{HA}:8123/auth/token", data=body),
                timeout=20,
            ) as resp:
                tok = json.loads(resp.read().decode()).get("access_token")
        except Exception:  # noqa: BLE001
            continue
        if tok:
            return tok
    raise SystemExit("no access token")


def state(tok, entity):
    req = urllib.request.Request(
        f"http://{HA}:8123/api/states/{entity}",
        headers={"Authorization": f"Bearer {tok}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def pull_db(tag, retries=6):
    """A consistent recorder snapshot.

    The DB and its WAL are copied one after the other, so the pair can be
    inconsistent: the WAL then does not match the DB and SQLite reports
    "database disk image is malformed", or it silently ignores the WAL and
    the rows come back without their attributes. Every snapshot is
    therefore validated -- the states are readable AND the attributes of
    the BLE status are present -- and a bad one is pulled again.
    """
    for attempt in range(retries):
        work = f"{SCRATCH}/{tag}"
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work)
        for name in ("home-assistant_v2.db", "home-assistant_v2.db-wal"):
            dest = f"{work}/{name}"
            subprocess.run(
                [
                    "bash",
                    "-lc",
                    f"ssh -o ConnectTimeout=10 {SSH} 'cat /config/{name} 2>/dev/null | gzip -1' > {dest}.gz",
                ],
                capture_output=True,
                timeout=500,
            )
            with open(f"{dest}.gz", "rb") as src, open(dest, "wb") as dst:
                subprocess.run(["bash", "-lc", f"gzip -dc {dest}.gz > {dest}"], check=True)
            os.remove(f"{dest}.gz")
        try:
            con = sqlite3.connect(f"file:{work}/home-assistant_v2.db?mode=ro", uri=True)
            cur = con.cursor()
            cur.execute("SELECT count(*) FROM states").fetchone()
            # The attributes have to be there: without them the WAL was
            # ignored and the snapshot says nothing about the reads.
            cur.execute(
                """SELECT s.attributes_id FROM states s
                   JOIN states_meta m ON s.metadata_id = m.metadata_id
                   WHERE m.entity_id = 'sensor.rwb1_ble_read_status'
                   ORDER BY s.last_updated_ts DESC LIMIT 1"""
            ).fetchone()
            row = cur.execute(
                """SELECT a.shared_attrs FROM states s
                   JOIN states_meta m ON s.metadata_id = m.metadata_id
                   JOIN state_attributes a ON s.attributes_id = a.attributes_id
                   WHERE m.entity_id = 'sensor.rwb1_ble_read_status'
                   ORDER BY s.last_updated_ts DESC LIMIT 1"""
            ).fetchone()
            if row and row[0]:
                return con
            con.close()
        except sqlite3.DatabaseError:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
        print(f"  знімок {attempt + 1}/{retries} неузгоджений -- повторюю")
        time.sleep(5)
    return None


def capture_running():
    return int(sh("ps aux | grep -c '[c]apture_ha_log'") or 0) > 0


def start_capture():
    subprocess.Popen(
        ["bash", "-lc", f"cd {SCRATCH} && nohup ./capture_ha_log.sh 400 >/dev/null 2>&1 &"]
    )
    time.sleep(8)


def do_restart():
    print("=== рестарт HA (плановий) ===")
    print("  команда:", sh("date -u +%H:%M:%S"), "UTC")
    subprocess.run(
        ["bash", "-lc", f"ssh -o ConnectTimeout=8 -n {SSH} 'ha core restart >/dev/null 2>&1 &'"],
        timeout=40,
    )
    t0 = time.time()
    down = False
    while time.time() - t0 < 300:
        code = sh(
            f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 6 http://{HA}:8123/",
            timeout=20,
        )
        if code != "200" and not down:
            down = True
            print(f"  HA впав через {time.time() - t0:.0f} с")
        if code == "200" and down:
            print(f"  HA піднявся через {time.time() - t0:.0f} с")
            return True
        time.sleep(3)
    print("  HA НЕ піднявся за 300 с")
    return False


def report_shutdown_lines(before_offset):
    print("\n=== 1. журнал зупинки (нові записи) ===")
    try:
        text = open(CAPTURE, errors="replace").read()
    except OSError:
        print("  файл захвату відсутній")
        return
    fresh = text[before_offset:]
    found = False
    for line in fresh.split("\n"):
        if any(marker in line for marker in STOP_LINES):
            print("  ", line[:190])
            found = True
    if not found:
        print("  записів зупинки НЕ знайдено — close не встиг або не виконувався")


def report_teardown_record():
    print("\n=== 2. запис teardown на HA ===")
    raw = sh(
        f"ssh -o ConnectTimeout=8 {SSH} 'cat /config/powmr_ble_teardown.json 2>&1'"
    )
    if raw.startswith("cat:"):
        print("  файлу немає — запис не створився")
        return
    try:
        record = json.loads(raw)
    except ValueError:
        print("  файл пошкоджений:", raw[:200])
        return
    disconnect = record.get("last_disconnect") or {}
    print(f"  ok={disconnect.get('ok')} teardown_incomplete={disconnect.get('teardown_incomplete')}")
    print(f"  reason={disconnect.get('teardown_reason')}")
    print(f"  seconds={disconnect.get('seconds')} at={disconnect.get('at')}")


def report_reads(tok, since):
    print("\n=== 3. читання після рестарту ===")
    con = pull_db("verify_restart")
    if con is None:
        print("  БД не прочитана")
        return
    cur = con.cursor()
    rows = cur.execute(
        """SELECT s.last_updated_ts, s.attributes_id FROM states s
           JOIN states_meta m ON s.metadata_id = m.metadata_id
           WHERE m.entity_id = 'sensor.rwb1_ble_read_status'
             AND s.last_updated_ts >= ?
           ORDER BY s.last_updated_ts""",
        (since,),
    ).fetchall()
    events = []
    seen = set()
    for ts, aid in rows:
        got = cur.execute(
            "SELECT shared_attrs FROM state_attributes WHERE attributes_id = ?", (aid,)
        ).fetchone()
        try:
            attrs = json.loads(got[0]) if got else {}
        except Exception:  # noqa: BLE001
            continue
        attempt = attrs.get("last_attempt")
        if not attempt or attempt in seen:
            continue
        seen.add(attempt)
        events.append((datetime.datetime.fromtimestamp(ts, datetime.timezone.utc), attrs))
    ok = [(t, a) for t, a in events if not a.get("last_error")]
    err = [(t, a) for t, a in events if a.get("last_error")]
    print(f"  спроб {len(events)} | успішних {len(ok)} | помилок {len(err)}")
    for t, a in err[:12]:
        print(f"    ПОМИЛКА {t:%H:%M:%S} kind={a.get('error_kind')} {str(a.get('last_error'))[:70]}")
    if ok:
        first = ok[0][0]
        print(f"  перший успіх: {first:%H:%M:%S} UTC")
    recovery = (events[-1][1].get("recovery") or {}) if events else {}
    # ``last_result`` is None when no release was ever attempted in this
    # episode -- a missing result is not a missing attribute.
    last_result = recovery.get("last_result") or {}
    print(f"  recovery: state={recovery.get('state')} attempts={recovery.get('attempts')}")
    print(f"    last_result: {json.dumps(recovery.get('last_result'), ensure_ascii=False)}")
    print(f"    proxy: {json.dumps(last_result.get('proxy'), ensure_ascii=False)}")
    print(f"    releases_total: {recovery.get('releases_total')}")
    if not events:
        print("  ВИСНОВОК: недостатньо даних (рестарту ще не було)")
    elif (recovery.get("attempts") or 0) > 0:
        print(
            "  ВИСНОВОК: recovery ЗНАДОБИВСЯ -- "
            f"{recovery.get('attempts')} спроб(а), проксі: "
            f"{last_result.get('proxy_description')}"
        )
    else:
        print(
            "  ВИСНОВОК: recovery НЕ знадобився -- читання повернулися самі"
        )
    con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true", help="perform the restart")
    args = parser.parse_args()

    if not capture_running():
        print("=== журнал зупинки: запускаю поллер ===")
        start_capture()
    else:
        print("=== журнал зупинки: поллер уже працює ===")

    offset = os.path.getsize(CAPTURE) if os.path.exists(CAPTURE) else 0
    started = time.time() - 60
    if args.restart:
        started = time.time()
        do_restart()
        time.sleep(30)
    report_shutdown_lines(offset)
    report_teardown_record()
    tok = token()
    report_reads(tok, started)


if __name__ == "__main__":
    sys.exit(main())

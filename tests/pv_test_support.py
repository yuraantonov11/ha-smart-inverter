"""Stdlib-only fixture timezone; no Windows tzdata dependency."""
import io
import struct
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

class Checks:
    def __init__(self):
        self._P = self._F = 0
    def __call__(self, condition, message):
        if condition:
            self._P += 1
            print("PASS", message)
        else:
            self._F += 1
            print("FAIL", message)
    def finish(self):
        print(f"{self._P} passed, {self._F} failed")
        raise SystemExit(1 if self._F else 0)

def kyiv_2026():
    times = [int(datetime(2026,3,29,1,tzinfo=timezone.utc).timestamp()),
             int(datetime(2026,10,25,1,tzinfo=timezone.utc).timestamp())]
    header = b'TZif\0' + b'\0'*15 + struct.pack('>6l',0,0,0,2,2,9)
    data = header + struct.pack('>2l',*times) + bytes([1,0])
    data += struct.pack('>lbb',7200,0,0) + struct.pack('>lbb',10800,1,4) + b'EET\0EEST\0'
    return ZoneInfo.from_file(io.BytesIO(data),key='Test/Kyiv2026')

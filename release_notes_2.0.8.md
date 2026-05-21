# Smart Inverter App v2.0.8

## Bug Fixes

### 🔇 Fixed: EMERGENCY_CHARGE log spam in Storm mode
- `EMERGENCY_CHARGE RECOVERY COMPLETE` was being logged every minute (~60 times/hour) when HEMS was in **Storm / Reserve** mode with a full battery at night.
- **Root cause**: Storm mode intentionally keeps charger=SNU; the emergency recovery check was firing every tick trying to reset it to OSO, then Storm mode would set it back to SNU, creating an infinite loop.
- **Fix**: Emergency charge logic is now entirely skipped when HEMS mode is Storm/Reserve (smartMode=2). Added state flag `_emergencyChargeActive` so recovery is logged only once per actual recovery transition.

## Windows Installation

Download **`smart_inverter_setup_v2.0.8.exe`** and run it — no certificate error, standard Windows installer.

> If you see a SmartScreen warning: click **"More info" → "Run anyway"**. This is a standard warning for unsigned executables.

## Artifacts

| File | Platform |
|------|----------|
| `smart_inverter_setup_v2.0.8.exe` | Windows installer (recommended) |


# HEMS Modes — Detailed Reference

> [English](HEMS_MODES.md) | [Українська](HEMS_MODES_UA.md)  
> **Smart Inverter App** — Home Energy Management System  
> Algorithm file: `lib/services/hems_algorithm.dart`  
> Tuning constants: `HemsTunables` (all adjustable)

---

## Table of Contents

1. [Architecture overview](#1-architecture-overview)
2. [Shared safety rules (always active)](#2-shared-safety-rules-always-active)
3. [Mode A — Adaptive (Smart)](#3-mode-a--adaptive-smart)
4. [Mode B — Night Arbitrage](#4-mode-b--night-arbitrage)
5. [Mode C — Storm / Reserve](#5-mode-c--storm--reserve)
6. [Cross-cutting: Battery Keepalive](#6-cross-cutting-battery-keepalive)
7. [Cross-cutting: Acoustic Comfort (Night Silence)](#7-cross-cutting-acoustic-comfort-night-silence)
8. [Anti-flapping & Manual Override](#8-anti-flapping--manual-override)
9. [Tuning constants quick reference](#9-tuning-constants-quick-reference)
10. [Decision flow diagrams](#10-decision-flow-diagrams)
11. [Real-world examples (with numbers)](#11-real-world-examples-with-numbers)
12. [FAQ / Troubleshooting](#12-faq--troubleshooting)

---

## 1. Architecture overview

Each HEMS tick (called periodically from `AppStateProvider`) goes through this priority chain:

```
[Safety floor: SOC ≤ 22%?]
       ↓ NO
[Manual override hold active?]
       ↓ NO
[Battery keepalive triggered?]
       ↓ NO
[Night tariff window (23:00–07:00)?]
       ↓ NO
[Realtime PV surplus ≥ 250W and SOC ≥ 30%?]
       ↓ NO
[Forecast simulation → deficit?]
       ↓
[Decision: SBU or USB]
```

**Output priorities (inverter API)**

| Value | Name | Meaning |
|-------|------|---------|
| `'0'` | USB  | Grid First — load fed from grid; PV → battery charge |
| `'2'` | SBU  | Solar/Battery First — load fed from PV/battery; grid only for backup |

**Charger priorities**

| Value | Name | Meaning |
|-------|------|---------|
| `'1'` | SNU  | Solar + Utility — both PV and grid can charge battery |
| `'2'` | OSO  | Solar Only — only PV charges battery, no grid charge |

---

## 2. Shared safety rules (always active)

These rules fire **before** any mode logic and cannot be overridden by dwell or manual override.

### 2.1 Hard SOC floor

**Trigger:** `batterySoc ≤ reserveSoc + 2` (default: `SOC ≤ 22%`)

**Action:**
- Output → `USB` (grid feeds load, battery not discharged further)
- Charger → `SNU` (both PV and grid can replenish battery)
- Log: `🔀 HEMS: output → USB (reason=safety_low_soc)`

**Example:**
```
SOC = 21%  PV = 1500W  Hour = 13:00
→ Safety fires: USB + SNU
→ Grid feeds house; PV charges battery
→ Reason: even with strong sun, battery must recover first
```

**Why not SBU here?** In SBU with very low SOC, the inverter starts drawing from battery rapidly. On a 48V LiFePO4 pack, going below ~20% repeatedly degrades cells. The 2% hysteresis (`22%`) prevents oscillation right at the boundary.

---

## 3. Mode A — Adaptive (Smart)

The default mode for users who want maximum automation. Combines:
- **Realtime layer** — reacts to current PV vs load balance (seconds-level)
- **Forecast simulation** — looks ahead to 23:00 using hourly solar forecast and historical consumption stats

### 3.1 Night window (23:00 – 07:00)

**Always:** Output → `USB`  
Reason: night tariff is cheapest, no sun available, battery should not discharge overnight.

**Charger decision** (based on tomorrow's simulation):

```
simulateEnergyDeficit(07:00 → 23:00, startBattery=currentWh)
```

| Simulation result | Action | Log example |
|---|---|---|
| `deficit > 0` (sun won't cover tomorrow's need) | Charger → `SNU` (charge from grid now, while tariff is low) | `🌙 Ніч: дефіцит 3200 Вт·год → SNU` |
| `deficit == 0` (tomorrow's sun is enough) | Charger → `OSO` (save grid energy, solar only) | `🌙 Ніч: сонця вистачить → OSO` |

**Example — charge night (cloudy forecast):**
```
02:00, SOC=65%, forecast shows only 800Wh tomorrow (cloudy)
avg consumption stats: 600Wh/h × 16h = 9600Wh
battery capacity: 230Ah × 51.2V × 0.8 usable = ~9420Wh
deficit = 9600 - 800*0.85 - 9420*0.65 = big positive
→ Charger = SNU (grid charges at night rate)
```

**Example — skip night charge (sunny forecast):**
```
02:00, SOC=70%, forecast shows 18000Wh tomorrow (clear sky)
avg consumption: 450Wh/h × 16h = 7200Wh
deficit = 7200 - 18000*0.85 - 9420*0.70 = deeply negative
→ Charger = OSO (no grid charge, solar handles it)
```

---

### 3.2 Daytime (07:00 – 17:00)

Charger always → `OSO` (solar only, no grid wasted on charging during expensive daytime tariff).

Decision tree (in priority order):

#### Step 1: Realtime PV surplus check (NEW — key fix)

```
surplus = pvPower - loadPower
```

| Condition | Action |
|---|---|
| `pvActive (PV > 80W)` AND `surplus ≥ pvSurplusEnterW (250W)` AND `SOC ≥ minOperatingSoc (30%)` | → `SBU` immediately |

**This is the primary fix for "sun is shining but USB is on".**

Before this logic existed, the algorithm could ignore a 2000W PV surplus and stay in USB because a weak forecast simulation predicted an evening deficit. Now, if the panels are producing significantly more than consumption, SBU is forced regardless of forecast.

**Example — sunny noon:**
```
13:00, PV=2500W, Load=900W, SOC=72%
surplus = 1600W ≥ 250W threshold
pvActive = true (2500W > 80W)
SOC OK = true (72% ≥ 30%)
→ Output = SBU (reason=pv_surplus_1600W_soc_72)
→ House runs on solar, zero grid import
```

**Example — barely producing:**
```
13:00, PV=200W, Load=900W, SOC=72%
surplus = -700W < 250W threshold
→ Realtime layer does NOT force SBU
→ Falls through to forecast simulation
```

#### Step 2: Forecast simulation fallback

```
deficitTillNight = simulateEnergyDeficit(currentHour → 23:00)
```

| Condition | Action | Meaning |
|---|---|---|
| `deficit == 0` | → `SBU` | Forecast guarantees enough energy till 23:00; use solar/battery now |
| `deficit > 0` AND `surplus ≤ pvSurplusExitW (50W)` AND `SOC < midSoc (50%)` | → `USB` | Cloudy + low battery: feed house from grid now, save all PV for battery |
| Everything else | → **hold current mode** | Ambiguous state; avoid unnecessary switching |

**Example — forecast deficit, low SOC:**
```
14:00, PV=350W, Load=900W, SOC=38%
surplus = -550W (well below exit threshold 50W)
SOC = 38% < midSoc 50%
simulation: with avg 600Wh consumption and only 300Wh/h solar,
            battery will hit reserve by ~18:00
deficit = 2100Wh
→ Output = USB (reason=day_forecast_deficit_2100Wh_low_soc)
→ Grid feeds house; all PV goes to battery; battery recovers for evening
```

**Example — small surplus, hold state:**
```
14:00, PV=1000W, Load=900W, SOC=38%
surplus = 100W (between pvSurplusExitW=50W and pvSurplusEnterW=250W)
deficit = 2000Wh
→ surplus > exit threshold, but SOC < midSoc
→ "hold" branch: algorithm does not switch, logs current state
→ Avoids flapping between USB/SBU while conditions are borderline
```

---

### 3.3 Evening (17:00 – 23:00)

Evening is the most conservative window — primary goal is to **protect the battery reserve for the night**.

Charger always → `OSO`.

```
deficitTillNight = simulateEnergyDeficit(currentHour → 23:00)
availableEnergyWh = max(0, currentWh - reserveWh)
eveningSafetyWh = maxCapacity × 1%
```

| Condition | Action | Log |
|---|---|---|
| `SOC ≤ reserveSoc + 2` OR `available ≤ 1%` OR `deficit > 0` | → `USB` | `⚠️ evening_reserve_def_NNNWh_soc_NN` |
| `SOC ≥ reserveSoc + 5` AND `available > 1%` AND `deficit == 0` | → `SBU` | `🌆 evening_battery_avail_NNNNWh` |
| Neither condition | No change | Hold current mode |

**Example — enough battery for evening:**
```
19:00, SOC=65%, avg consumption till 23:00 = 300Wh/h × 4h = 1200Wh
battery available above reserve = 230Ah × 51.2 × (0.65-0.20) = 5299Wh
5299Wh >> 1200Wh → deficit = 0
SOC 65% ≥ 25% (reserveSoc+5)
→ Output = SBU (house runs from battery till midnight, no grid)
```

**Example — borderline evening:**
```
21:00, SOC=32%, avg consumption till 23:00 = 500Wh/h × 2h = 1000Wh
available = 230*51.2*(0.32-0.20) = 1415Wh
simulation: 1415 - 1000 = 415Wh buffer (small but positive, deficit=0)
SOC 32% ≥ 25% → batteryCanBeUsed = true
→ Output = SBU (but will quickly hit reserve and return to USB at 23:00)
```

**Example — must protect reserve:**
```
20:00, SOC=28%, high consumption evening (TV, AC, etc.), no solar
simulation shows deficit 800Wh by 22:00
reserveProtectionActive = true (deficit > 0)
→ Output = USB (grid takes over, battery preserved)
→ Log: evening_reserve_def_800Wh_soc_28
```

---

## 4. Mode B — Night Arbitrage

Simplified mode for users on **time-of-use tariffs** (cheap night, expensive day).  
Logic: buy cheap electricity at night, sell solar energy (or avoid buying) during day.

### 4.1 Night (23:00 – 07:00)

```
Output → USB   (grid feeds load, no battery discharge)
Charger → SNU  (charge battery from both solar (0) and grid)
```

**Example:**
```
03:00, SOC=50%
→ Charger = SNU: grid charges battery to 100% at cheap rate
→ Output = USB: grid feeds any night loads (minimal)
→ By 07:00, battery is full and ready for the day
```

### 4.2 Daytime (07:00 – 23:00)

```
Charger → OSO  (only solar can charge — no expensive grid charge)

If pvPower > 80W AND surplus ≥ 250W AND SOC ≥ 30%:
    Output → SBU  (realtime surplus: use solar first)
Else:
    Output: no forced switch (hold current state)
```

**Example — day with good sun:**
```
10:00, PV=2800W, Load=1000W, SOC=100%
surplus = 1800W ≥ 250W
→ Output = SBU
→ House runs entirely on solar; battery surplus might go to heat pump/boiler
```

**Example — cloudy day:**
```
15:00, PV=150W, Load=1000W, SOC=80%
surplus = -850W < 250W → no forced SBU
→ Hold current mode (probably USB from night, so stays USB)
→ Grid feeds house during cloudy period; battery preserved
→ When cloud clears: PV rises → surplus ≥ 250W → SBU auto-switches
```

> **Key difference vs Adaptive mode:** Night Arbitrage does NOT simulate the evening energy balance.  
> It's simpler and deterministic: night = charge, day = use solar when available.  
> Use this mode if your consumption patterns are irregular or you don't trust the weather forecast.

---

## 5. Mode C — Storm / Reserve

Designed for **grid outage preparation**: storms, scheduled blackouts, known instability.

```
Output → USB  (force, ignores dwell)
Charger → SNU (force, both PV and grid charge battery as fast as possible)
```

**Always runs with `force=true`** — overrides dwell and dedup timers.

**Goal:** reach 100% SOC as quickly as possible using every available energy source.

**Example:**
```
Storm forecast, current SOC=55%, PV=1200W, Grid=available
→ Output = USB: load from grid (no battery discharge)
→ Charger = SNU: PV charges battery at full rate + grid tops up simultaneously
→ Battery charges at max rate (e.g. 80A × 51.2V = 4096W theoretical)
→ ~30-40 min to 100% SOC if PV + grid both available
```

**When to use:**
- Evening before a predicted outage
- Winter storms where grid reliability is low
- Pre-charging before extended travel away from home

**When NOT to use:**
- During normal sunny days (you'll waste money buying grid energy to charge unnecessarily)
- When SOC is already ≥ 95% (no practical benefit)

---

## 6. Cross-cutting: Battery Keepalive

**Problem it solves:** Some lithium battery BMS units go into a "sleep" or "standby" state if there is no current flow for an extended period. In USB mode with full battery and low load, this can happen.

**Trigger:**
- Battery inactive (|batteryPower| ≤ 50W) for ≥ 2 hours
- `SOC > 22%` (above keepalive minimum)
- Current output is not already SBU

**Action:**
1. Switch to `SBU` for **90 seconds** — battery starts discharging slightly, BMS wakes up
2. After 90s, automatically return to `USB`
3. Reset the 2-hour inactivity timer

**Log sequence:**
```
🔋 Keepalive: battery idle 127m. Briefly switching to SBU.
[90 seconds later]
🔋 Keepalive done. Returned to USB.
```

**Example:**
```
14:00, SOC=85%, USB mode all morning, battery has been idle for 2h10m
→ Keepalive: switch to SBU
→ Battery starts drawing ~200W for 90s (load × efficiency factor)
→ Return to USB; BMS active again
→ Next keepalive scheduled in another 2h
```

**Storm mode exception:** keepalive is NOT triggered in Storm mode (battery is always actively charging).

---

## 7. Cross-cutting: Acoustic Comfort (Night Silence)

**Purpose:** Most inverters beep/alarm when switching modes or under certain conditions. Silences buzzer at night to avoid disturbing sleep.

| Time | Buzzer state | Action if different |
|------|-------------|---------------------|
| 22:00 – 07:00 | `OFF` (0) | `changeSetting('buzzerAlarmSetting', '0')` |
| 07:00 – 22:00 | `ON` (1)  | `changeSetting('buzzerAlarmSetting', '1')` |

**Dedup:** Uses `_lastAppliedBuzzer` cache to avoid repeating the API call every tick.

**Example:**
```
22:01 → buzzer was ON → set to OFF → "🤫 Buzzer: night silence on"
07:00 → buzzer was OFF → set to ON → "🔊 Buzzer: daytime sound on"
```

---

## 8. Anti-flapping & Manual Override

### 8.1 Anti-flapping (dwell timer)

**Problem:** Without this, a 5-minute cloud can cause 10+ mode switches, stressing inverter relays and creating API spam.

**`minModeHold = 20 minutes`** (default)

Any switch from USB→SBU or SBU→USB starts a 20-minute dwell timer.  
During this window, a conflicting switch request is **logged but ignored**.

```
Log: ⏳ HEMS: skip switch to USB (reason=day_forecast_deficit_800Wh_low_soc) — dwell active
```

**Exception:** `force=true` calls (safety floor, storm mode) bypass the dwell.

**Example:**
```
12:00 → PV surplus 1600W → switch to SBU ← switch recorded
12:05 → Cloud, PV=100W, forecast deficit → wants USB
        → Dwell active (only 5min elapsed, need 20min)
        → SKIP: stays SBU
12:20 → Cloud still, forecast deficit, SOC=45%
        → Dwell expired (20min passed)
        → Switches to USB (if deficit condition still met)
```

### 8.2 Command deduplication

**`commandDedupWindow = 30 seconds`**

If the exact same command (e.g. "set output to SBU") was sent less than 30 seconds ago, it is silently skipped.  
Prevents hammering the inverter API when the periodic tick fires faster than the inverter responds.

### 8.3 Manual override

**Triggered two ways:**

**A) Automatic detection:**  
If the inverter's reported mode differs from the last command the algorithm sent, and more than 30s have elapsed → the algorithm concludes the user (or an external app) changed the mode manually.

```
Log: ✋ HEMS: detected manual override (mode=2, was cmd=0). Holding 30m.
```

**B) Explicit arm from UI:**  
Tapping SBU or USB buttons in the app calls `armManualOverride()` before sending the command.

**Effect of override hold (30 minutes):**
- Algorithm will NOT change `outputSourcePriority`
- Charger priority (`OSO`/`SNU`) can still be managed if safe
- After 30 minutes, algorithm resumes normal operation

**Example — user overrides algorithm at noon:**
```
12:30 → Algorithm in USB (forecast deficit)
12:31 → User taps SBU button in app
         → armManualOverride() called → hold until 13:01
         → setMode(2) called → inverter switches to SBU
12:32 → Next algorithm tick: manual hold active → skips output decision
13:01 → Hold expires → algorithm resumes
         → If PV surplus still ≥ 250W: stays SBU (realtime layer keeps it)
         → If cloud: may switch to USB if deficit + low SOC
```

---

## 9. Tuning constants quick reference

All constants are in `HemsTunables` class. To override, pass a custom instance:

```dart
HemsAlgorithmService(provider, tun: HemsTunables(
  pvSurplusEnterW: 150.0,   // more aggressive SBU entry
  minModeHold: Duration(minutes: 15),
))
```

| Constant | Default | Effect of increasing | Effect of decreasing |
|----------|---------|---------------------|---------------------|
| `reserveSoc` | 20% | More battery safety | Less reserve, more usable capacity |
| `minOperatingSoc` | 30% | Prefer USB more often | Use SBU at lower SOC |
| `midSoc` | 50% | Hold USB longer in forecast-deficit cases | Switch to USB less often |
| `pvSurplusEnterW` | 250W | Need stronger sun to enter SBU | Enter SBU with less sun |
| `pvSurplusExitW` | 50W | Stay in SBU even when PV barely covers load | Exit SBU sooner on clouds |
| `minModeHold` | 20 min | Less frequent switching, less relay wear | Faster response to conditions |
| `manualOverrideHold` | 30 min | Algorithm stays hands-off longer after user action | Algorithm resumes sooner |
| `commandDedupWindow` | 30 sec | Less API chatter | More responsive to rapid changes |

---

## 10. Decision flow diagrams

### Adaptive mode daytime (07:00–17:00)

```
START tick
    │
    ▼
SOC ≤ 22%? ──YES──→ USB + SNU (FORCE) → END
    │ NO
    ▼
Manual hold active? ──YES──→ manage charger only → END
    │ NO
    ▼
Hour ≥ 23 or < 7? ──YES──→ USB + (SNU or OSO per forecast) → END
    │ NO
    ▼
Set charger = OSO
    │
    ▼
PV > 80W AND surplus ≥ 250W AND SOC ≥ 30%?
──YES──→ SBU (pv_surplus) → END
    │ NO
    ▼
Run forecast simulation (currentHour → 23:00)
    │
    ├─ deficit == 0 ──────────────────→ SBU (day_forecast_ok) → END
    │
    ├─ deficit > 0 AND surplus ≤ 50W
    │  AND SOC < 50% ──────────────→ USB (day_forecast_deficit) → END
    │
    └─ else ──────────────────────→ HOLD current mode (log only) → END
```

### Evening (17:00–23:00)

```
[after charger = OSO and optional SBU from realtime surplus]
    │
    ▼
Run forecast simulation (currentHour → 23:00)
    │
    ├─ reserveProtectionActive
    │  (SOC ≤ 22% OR avail ≤ 1% OR deficit > 0)
    │  ──────────────────────────────→ USB (evening_reserve) → END
    │
    ├─ batteryCanBeUsed
    │  (SOC ≥ 25% AND avail > 1% AND deficit == 0)
    │  ──────────────────────────────→ SBU (evening_battery_avail) → END
    │
    └─ else ─────────────────────────→ HOLD current mode → END
```

---

## 11. Real-world examples (with numbers)

### Example 1: Perfect summer day

| Time | PV | Load | SOC | Mode | Reason |
|------|----|------|-----|------|--------|
| 02:00 | 0W | 80W | 85% | USB+OSO | Night tariff, sunny forecast tomorrow |
| 07:00 | 200W | 400W | 83% | USB (hold) | Surplus only 200W < 250W threshold, hold from night |
| 08:00 | 800W | 500W | 85% | **SBU** | surplus=300W ≥ 250W, SOC OK |
| 13:00 | 3200W | 900W | 100% | SBU | surplus=2300W |
| 17:30 | 1800W | 700W | 100% | SBU | Evening: deficit=0, battery full |
| 20:00 | 50W | 600W | 88% | SBU | Evening: avail=7900Wh >> 600Wh needed |
| 22:30 | 0W | 200W | 75% | SBU (if avail>0) | Depends on simulation |
| 23:00 | 0W | 150W | 72% | **USB+OSO** | Night tariff starts |

### Example 2: Cloudy day with manual override

| Time | Event | Mode | Reason |
|------|-------|------|--------|
| 09:00 | PV=150W, load=800W, SOC=60% | USB | Day forecast deficit, surplus negative |
| 10:30 | User taps SBU button | **SBU** | Manual override armed 30min |
| 10:31–11:00 | Algorithm tick fires | SBU (hold) | Manual hold active |
| 11:00 | Hold expires, PV still 150W, SOC=55% | **USB** | Forecast deficit, low surplus |
| 14:00 | Sun breaks, PV=2400W | **SBU** | Realtime surplus 1600W |

### Example 3: Storm mode before blackout

```
User activates Storm mode at 18:00
SOC: 60%, PV: 400W (evening sun), Grid: available

18:00  → Output=USB (force), Charger=SNU (force)
         Grid feeds load + charges battery
         PV also charges battery simultaneously
18:40  → SOC: 85% (approx, depends on battery size and charger limit)
19:20  → SOC: 100%
20:00  → Grid blackout
         Inverter auto-switches to battery backup
         Battery: 100% → runs house 6–10 hours depending on load
```

### Example 4: Low SOC rainy day

```
08:00  SOC=22.5% (just above safety floor), PV=80W, Load=700W

Safety floor check: 22.5% ≤ 22% → NO (just above floor)
Realtime: surplus = 80-700 = -620W < 250W → no SBU from realtime
Forecast: big deficit (cloudy all day, low SOC) → USB
→ Output=USB, Charger=OSO (solar only, tiny bit charges)

10:00  SOC=19.5% (dropped below 22%)
Safety floor fires!
→ Output=USB (force), Charger=SNU (force)
→ Log: safety_low_soc
→ Grid now actively charges battery at max rate
```

---

## 12. FAQ / Troubleshooting

### Q: "It's sunny but the app shows USB. Why?"

Check the log for the `reason=` code:

| Reason code | Meaning | Fix |
|---|---|---|
| `safety_low_soc` | SOC ≤ 22% | Wait for battery to charge above 22% + hysteresis |
| `day_forecast_deficit_NNNWh_low_soc` | Forecast predicts energy shortage by evening AND current SOC < 50% | If sun forecast is wrong, increase `productionCoefficient` or wait for SOC to rise |
| `dwell active` | A switch happened less than 20 min ago | Wait up to 20 min; or reduce `minModeHold` |
| `manual hold active` | User recently changed mode manually (or app detected external change) | Wait 30 min or restart the automation |
| `pv_surplus_NNW_soc_NN` in previous line | Already switched to SBU | Refresh UI |

### Q: "Why does it switch to USB right after I set SBU?"

The manual override hold is 30 minutes. If the app did NOT log `✋ HEMS: detected manual override` or you did not tap the button in the app (changed mode via web/other means), the algorithm may not have detected your override.

**Fix:** Tap the SBU button in the app (not the inverter's web UI) — this explicitly calls `armManualOverride()` for 30 minutes.

### Q: "Night arbitrage vs Adaptive — which should I use?"

| Situation | Recommended |
|---|---|
| Predictable schedule, trust weather forecast | **Adaptive** |
| Variable consumption (work from home some days, not others) | **Adaptive** (uses live load as fallback) |
| Time-of-use tariff, want simplicity, no interest in forecast | **Night Arbitrage** |
| Multi-day storm or planned outage | **Storm** temporarily |

### Q: "Keepalive is interfering with my logs"

Normal — it fires every 2 hours when battery is idle. You cannot disable it via UI currently, but you can extend `_keepaliveInterval` in the source (default 2h).  
Keepalive is harmless: 90-second SBU at partial load barely moves the SOC.

### Q: "pvSurplusEnterW = 250W seems high for winter"

It is conservative by design — prevents SBU on weak winter sun that can't sustain the load.  
For winter, try `pvSurplusEnterW: 100.0` in `HemsTunables`. Monitor for a few days; if SOC drains faster than expected on cloudy days, increase it back.

---

*Last updated: 2026-04-25 | Version: 1.3.2*

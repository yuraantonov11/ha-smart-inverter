"""План виправлень після Code Review 654a227.

Юра (2026-10-19) виявив:
1. HEMS engine має unit-mismatch bug — `_adaptive_evening_protection`
   condition 3 порівнює `|surplus_w|` з `available_soc_pct * 0.3 * 100`.
   Це призводить до команди SNU (мережева зарядка) ввечері.
2. `_compute_attainable_target_soc_pct` clamp 100% ховає реальний дефіцит.
3. `_compute_pv_contribution_kwh` рівномірно розподіляє добовий PV по
   годинах, включно з нічними (0.46 kWh для 00:18-02:18).
4. `print_outage_plan.py` hard-coded, не live snapshot.
5. `compute_outage_plan` service i runtime wiring не пройдені
   behavioral acceptance.

Live evidence (journal
`/config/powmr_inverter_incidents/01M3XWJ8DRYDQC8A0NCPRVB53N/events.jsonl`):
- 16:54:31 UTC (19:54 EEST) HEMS decision target_ch_mode=1 (SNU)
- 16:54:32 command api_accepted
- 16:55:04 manual_override_hold begins
- 17:00:07 HEMS decision target_ch_mode=1 (SNU)
- 17:00:08 command api_accepted
- 17:00:20 battery starts charging: 39A / 2168W (5 хв реальної зарядки)
- 17:05:20 HEMS decision target_ch_mode=2 (OSO)
- 17:05:22 command api_accepted
- 17:20:34 command_readback unconfirmed
- 17:32:51 current state: SOC=100, bat_I=0, cur_ch=OSO

Юра's verdict: "Відклич висновок «HEMS не винен»."

План:
A. Fix engine bug (`_adaptive_evening_protection`).
B. Fix planner `_compute_attainable_target_soc_pct`: separate
   absolute / attainable / charge / residual, never hide residual
   above 100%.
C. Fix planner `_compute_pv_contribution_kwh`: use hourly forecast
   when available; never fabricate nighttime PV.
D. Replace `print_outage_plan.py` with snapshot reader (HA states API)
   or DEMO mode label. UTC+3 → Europe/Kyiv.
E. Add behavioral acceptance for `compute_outage_plan` service +
   runtime wiring.
"""
from __future__ import annotations

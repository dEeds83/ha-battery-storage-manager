# Änderungen v2.49.0 – v2.51.0 (Mai-Juni 2026)

Zweck dieser Datei: Konsolidierte Doku der letzten fünf Releases zur externen Überprüfung. Fokus auf **Discharge-Optimierung bei Solar-Überfluss** und die daraus folgenden Bugfixes.

---

## Motivation (Ausgangsproblem)

An sonnigen Tagen wurde der Akku oft nicht aggressiv genug entladen → stundenlang bei 100 % SOC, eingehende Solar-Energie wurde als ungenutzter Export verschenkt. Morgens wurden häufig `hold`-Slots gewählt, obwohl mehr Discharge möglich/wirtschaftlich gewesen wäre.

Ursprüngliche Analyse identifizierte **fünf Konservativitäts-Quellen**:

1. **Statischer Solar-Headroom-Floor** – reservierte 50 % der Tages-Solarsumme als Akku-Reserve, ohne Berücksichtigung des aktuellen SOC oder historischer Curtailment-Daten.
2. **Asymmetrisches Voting** – Discharge brauchte 2-von-3-Mehrheit der Szenarien (expected/pessimistic/optimistic). Bei sonnigen Tagen verhinderte das pessimistische Szenario (Solar × 0,6) häufig den Discharge.
3. **Pre-Solar-Discharge fehlte** – Plan markierte zwar Slots als „pre-solar" für die Reason-Anzeige, erzeugte aber keine zusätzlichen Discharges.
4. **Per-Slot-Discharge-Cap auf 0** – bei Solar ≥ Hausverbrauch wurde `discharge_kwh = 0` gesetzt, DP konnte den Slot nicht als Discharge wählen.
5. **DP-Filter `new_si < si`** – verwarf Discharge wenn paralleler Solar-Surplus den SOC im selben Slot wieder hochzog, auch wenn Revenue positiv war.

---

## v2.49.0 — Aggressivere Discharge bei Solar-Überfluss

**Commit:** [276c5b4](https://github.com/dEeds83/ha-battery-storage-manager/commit/276c5b4)
**Datum:** 2026-05-28

Vier kombinierte Hebel:

### 1. Dynamischer Solar-Headroom-Floor
[coordinator.py:1505-1573](custom_components/battery_storage_manager/coordinator.py#L1505-L1573)

Floor wird zur Laufzeit nach unten korrigiert (nie nach oben):

```python
base_floor = self._solar_headroom_floor  # User-Setting, z. B. 0,5
floor_factor = base_floor

# SOC-Pressure: wie viel passt noch rein vs. wie viel kommt rein
pressure = solar_total_kwh / max(0.1, free_space_kwh)
if pressure >= 1.5:   floor_factor -= 0.30
elif pressure >= 1.0: floor_factor -= 0.15
elif pressure >= 0.7: floor_factor -= 0.05

# Curtailment-Trend (24h aus interner History)
if curtail_h >= 4.0:   floor_factor -= 0.25
elif curtail_h >= 2.0: floor_factor -= 0.15
elif curtail_h >= 1.0: floor_factor -= 0.08

# Curtailment-Trend (7d via HA-Statistics)
if curtail_7d >= 3.0:   floor_factor -= 0.15
elif curtail_7d >= 1.5: floor_factor -= 0.08
elif curtail_7d >= 0.5: floor_factor -= 0.04
```

### 2. Aktive Pre-Solar-Discharge
[optimizer.py:`force_pre_solar_discharge`](custom_components/battery_storage_manager/optimizer.py)

Läuft nach `smooth_plan`:
- Simuliert SOC-Verlauf gegen `max_soc` (echtes Limit, nicht `dp_max_soc`)
- Findet ersten Slot mit Overflow (Solar-Surplus passt nicht mehr rein)
- Promotet die **teuersten** `idle`/`hold`-Slots vor dem Overflow zu `discharge`
- Filter: Slots mit eigenem Solar-Surplus > 0,05 kWh werden **ausgeschlossen** (zero-export-Regelung würde Discharge blocken)
- Re-simuliert nach jeder Konversion, stoppt wenn Overflow gelöst oder kein Kandidat mehr

### 3. Solar-Curtailment-Tracker (Hybrid)
[history.py](custom_components/battery_storage_manager/history.py)

- **24h-Wert:** Aus interner `_action_history` (10-Min-Snapshots, 48h Retention). Zählt Slots mit `SOC ≥ 99 %` UND `Solar > 200 W`.
- **7-Tage-Wert:** Via `homeassistant.components.recorder.statistics.statistics_during_period`. Holt stündliche Mittelwerte von `battery_soc_entity` und `solar_power_entity`. Refresh-Cache: 1 Stunde.
- Beide Werte füttern Floor-Adaption.
- Neuer Diagnose-Sensor `sensor.solar_curtailment_24h` mit Attributen `lost_kwh_24h`, `avg_hours_per_day_7d`, `headroom_floor_base`/`effective`, `pre_solar_forced_slots`.

### 4. Symmetrisches Szenario-Voting
[coordinator.py:1595-1626](custom_components/battery_storage_manager/coordinator.py#L1595-L1626)

```python
# Vorher: Discharge brauchte 2-von-3-Mehrheit
votes = [sa[t] for sa in scenario_actions]
if votes.count("discharge") >= 2:
    actions.append("discharge")
else:
    actions.append("idle")

# Neu: Discharge folgt expected, echtes Veto nur durch aktives charge
if exp_act == "discharge":
    if pessimistic[t] == "charge":
        actions.append("idle")   # echtes Veto
    else:
        actions.append("discharge")
```

---

## v2.50.0 — Discharge in Solar-Slots ermöglichen

**Commit:** [b30fcbf](https://github.com/dEeds83/ha-battery-storage-manager/commit/b30fcbf)
**Datum:** 2026-05-28

Trotz v2.49.0 blieben Abendstunden (17:00–19:00) auf `hold`, obwohl Preise hoch (25–37 ct) und Solar bereits absteigend war. Zwei zusätzliche Bremsen identifiziert:

### Fix 1: Soft-Floor für `discharge_kwh`
[coordinator.py:1494-1502](custom_components/battery_storage_manager/coordinator.py#L1494-L1502)

Vorher (v2.46.0):
```python
house_minus_solar_w = max(0.0, h["house_w"] - solar_w)
real_discharge_w = min(discharge_power_w, house_minus_solar_w)
# Bei Solar > Haus → discharge_kwh = 0, DP konnte Slot nicht waehlen
```

Neu:
```python
soft_floor_w = discharge_power_w * 0.20  # 20% WR-Nennleistung
real_discharge_w = min(discharge_power_w, max(soft_floor_w, house_minus_solar_w))
```

Begründung: Solar-Forecast ist nie exakt. Zero-Export-Regelung im Coordinator regelt in Echtzeit ohnehin runter, falls Solar überwiegt. Der DP-Plan muss diesen Spielraum aber sehen, um teure Slots überhaupt einplanen zu können.

### Fix 2: DP-Filter durch Net-Export-Check ersetzen
[optimizer.py:158-178](custom_components/battery_storage_manager/optimizer.py#L158-L178)

Vorher (v2.47.0):
```python
new_soc = soc_after_dis + solar_to_batt_d / cap * 100
new_si = soc_to_idx(new_soc)
if new_si < si:                # SOC-Index muss echt sinken
    revenue = delivered * price - delta * half_cycle_eur
    val = revenue + dp[t + 1][new_si]
```

Problem: In Slots mit moderatem Solar konnte parallele Absorption (seit v2.47.0) den SOC im selben Slot wieder so hochziehen, dass `new_si == si` → Discharge verworfen, obwohl Revenue positiv.

Neu:
```python
net_export_kwh = delta - solar_to_batt_d
if net_export_kwh > 0.001:     # echte Energie geht netto raus
    revenue = delivered * price - delta * half_cycle_eur
    val = revenue + dp[t + 1][new_si]
```

---

## v2.50.1 — Isolierte Discharge-Slots durch DP-Artefakte entfernen

**Commit:** [cdd324c](https://github.com/dEeds83/ha-battery-storage-manager/commit/cdd324c)
**Datum:** 2026-05-28

Beobachtung: Einzelner `discharge`-Slot mitten zwischen lauter `hold`-Slots (z. B. 13:00) mit Reason *„Spread 0,0 ct, netto −10,7 ct"* — Verlust-Discharge unter Break-Even.

### Ursachen-Analyse

1. **Pipeline-Reihenfolge:** Pass 1 (Enclave-Removal in `smooth_plan`) lief **vor** `force_pre_solar_discharge`. Nach dem Pre-Solar-Pass kein Cleanup mehr.
2. **DP-Diskretisierungs-Artefakt:** Bei flacher Preiskurve (4× exakt 17,71 ct) und SOC nahe `dp_max_soc` konnte der DP-Solver in Float-Rundungen einen Single-Slot-Discharge als minimal profitabel auswählen.
3. **Reason-Anzeige:** Falls der Slot vom Pre-Solar-Pass kam, war er nicht in `presolar_discharge_hours` markiert → Reason zeigte fälschlich Verlust-Spread.

### Fixes

1. `force_pre_solar_discharge` Signatur erweitert: `tuple[int, float, set[int]]` (forced_count, kwh, indices)
2. Coordinator merged `forced_indices` in `presolar_discharge_hours` → korrekte Reason „Platz für Solar schaffen"
3. Neuer Pass `remove_dp_discharge_enclaves` in [optimizer.py:1063-1117](custom_components/battery_storage_manager/optimizer.py#L1063-L1117)

```python
def remove_dp_discharge_enclaves(actions, hourly_data, protect_indices):
    """Demote isolated discharge slots. Slots in protect_indices are kept."""
    for i in range(1, n - 1):
        if actions[i] != "discharge": continue
        if i in protect_indices: continue
        prev_same = actions[i-1] == "discharge"
        next_same = actions[i+1] == "discharge"
        if prev_same or next_same: continue
        nearby = (i >= 2 and actions[i-2] == "discharge") or \
                 (i+2 < n and actions[i+2] == "discharge")
        if nearby: continue
        actions[i] = "idle"
```

---

## v2.50.2 — WR-Target Self-Correct gegen tatsächliche Ist-Leistung

**Commit:** [4413396](https://github.com/dEeds83/ha-battery-storage-manager/commit/4413396)
**Datum:** 2026-06-01

User-Beobachtung: WR-Setpoint hängt auf Max-Wert, PID konvergiert sehr langsam → wirkt wie „festhängt".

### Ursache

- `_inverter_target_power` blieb intern auf Max nach Write
- WR-Aktor übernahm Setpoint kurzfristig nicht
- Actual-Sensor zeigte deutlich weniger als Target
- PID-Formel: `new_target = target - export_w * 0.5` → bei kleinem Export quasi keine Reduktion

### Fix in `_regulate_zero_feed`
[devices.py +17 Zeilen](custom_components/battery_storage_manager/devices.py)

Vor PID-Branch:
- Wenn Settle-Zeit abgelaufen UND `inverter_actual_power < inverter_target_power - 150 W`
- Clamp `target` auf `actual + 50 W` Reserve
- Reset PID-Integral und `last_error`
- Nächster Tick rechnet ab realistischem Wert weiter

Voraussetzung: `inverter_feed_actual_power_entity` muss konfiguriert sein.

---

## v2.51.0 — DP-Discharge braucht positiven Slot-Revenue

**Commit:** [05becc1](https://github.com/dEeds83/ha-battery-storage-manager/commit/05becc1)
**Datum:** 2026-06-07

### Problem

Plan vom 2026-06-07 zeigte Slot 18:00 als `discharge` mit Reason *„Spread 5,7 ct, netto −0,1 ct, η=85%"*. DP backward-induction:
- `val = revenue + dp[t+1]`
- 18:00 minimal negativ, aber unlockt 18:15+ mit netto +2 bis +17 ct
- DP nahm Mini-Verlust mit → unnötiger WR-Switch-Zyklus

### Fix
[optimizer.py:158-180](custom_components/battery_storage_manager/optimizer.py#L158-L180)

```python
revenue = delivered * price - delta * half_cycle_eur
if net_export_kwh > 0.001 and revenue > 0.0005:  # >= 0.05 ct
    val = revenue + dp[t + 1][new_si]
```

Threshold 0,05 ct/Slot:
- Klein genug für marginale Arbitrage (Spreads ≥ 6 ct bleiben akzeptiert)
- Groß genug um Floating-Point-Grenzfälle und Mini-Verlust-Discharges zu blocken

---

## Offene Themen (nicht behoben)

### Charge-Slot-Reihenfolge bei Solar-Voll-Effekt

Beobachtung 2026-06-07: Live-Plan wählte 4 Charges 13:15–14:00 (Preise 14,14 / 12,94 / 11,73 / 12,47 ct) — Slot 15:00 mit 11,75 ct blieb `hold`.

Analyse via Live-Replay durch lokalen Code:
- Lokales Replay findet 9 Charges in günstigsten Slots (11,73 bis 13,93 ct)
- Live-HA wählt 4 chronologisch erste

Ursache: Solar-Absorption durch `hold`/`idle`-Slots vor 13:15 hebt SOC bereits auf 84,4 %. Bei 14:00 ist SOC = max_soc (90 %), selbst ohne Charge durch Solar. Spätere günstige Slots sind dann ineffective (Akku voll).

Final Pass 5 swap (13:15 idle → 15:00 charge) würde scheitern: bei 15:00 wäre SOC bereits 90 % → Charge bringt 0 % SOC-Gewinn.

**Konsequenz:** Plan ist gegeben die SOC-Trajektorie nahe optimal. Diskrepanz zum lokalen Replay vermutlich durch unterschiedliche `discharge_kwh`-Werte (echte vs. approximierte house_w) oder temporären Headroom-Floor.

**Möglicher Fix (offen):** DP-Charge-Branch sollte parallele Solar-Absorption modellieren, um zu erkennen dass spätere Slots wegen Solar-Voll-Effekt ineffective sind. Aktuell ignoriert charge-Branch das.

---

## Test-Status

56/56 Tests grün (Stand v2.51.0). Davon neu seit v2.49.0:
- 6 Tests für `force_pre_solar_discharge` (overflow detection, price priority, min_soc safety, solar-slot exclusion, hold promotion, indices return)
- 4 Tests für `remove_dp_discharge_enclaves` (isolation, protect_indices, real-block preserved, 1-slot-gap counts as nearby)
- 1 Test für `test_discharge_chosen_despite_solar_absorption` (DP-Profitabilitäts-Check ohne SOC-Indexsenkung)

[tests/test_optimizer.py](tests/test_optimizer.py)

---

## Bereich für externe Überprüfung

Bitte folgende Punkte prüfen:

1. **`force_pre_solar_discharge` Logik:** Filter `solar_surplus_kwh > 0.05` zu strikt/zu locker? Edge cases bei sehr kleinem Surplus?
2. **`remove_dp_discharge_enclaves` Reihenfolge:** Reicht es, nach `force_pre_solar_discharge` zu laufen, oder gibt es Smoothing-Pässe vorher, die noch Single-Slot-Discharges erzeugen können?
3. **DP-Discharge-Threshold 0,0005 EUR:** Sinnvoller Wert? Größenordnung vs. typische Float-Rundungsfehler im DP-Solver?
4. **Solar-Curtailment-Tracker:** Schwelle SOC ≥ 99 % und Solar > 200 W praxisrelevant? Reicht 7-Tage-Fenster für strukturelle Trends?
5. **Symmetrisches Voting:** Ist „nur aktives `charge`-Votum des pessimistischen Szenarios vetoed" zu permissiv? Sollte auch `idle`-Votum dagegen zählen?
6. **Headroom-Floor-Adaption-Stufen:** SOC-Pressure-Stufen (0,7 / 1,0 / 1,5) und Curtailment-Stufen empirisch sinnvoll? Sollten sie konfigurierbar sein?
7. **Charge-Branch Solar-Awareness:** Lohnt DP-Erweiterung damit Charge-Branch parallele Solar-Absorption modelliert? (offenes Thema)

---

## Korrekturen an obiger Doku (nach Code-Review festgestellt)

Die Code-Snippets/Schwellen oben stimmen verbatim mit dem Quelltext; **die Zeilennummern sind jedoch veraltet** (Drift ~6–50 Zeilen, nicht vertrauen). Außerdem:

- **„Pass 5 swap würde scheitern weil SOC = 90 %"** (Abschnitt *Offene Themen*) ist mechanisch falsch: Pass 5 ist rein **preisbasiert** ohne SOC-Simulation und würde blind ausführen. Die SOC-bewusste Logik liegt in **Pass 6**.
- **Curtailment-Schwelle:** Code ist inklusiv `Solar >= 200 W` (nicht `> 200 W`); SOC `>= 99 %` stimmt.
- **`lost_kwh_24h` füttert den Floor NICHT** — nur die Curtailment-**Stunden** (24h-Count + 7d-Schnitt) gehen in die Floor-Adaption; `lost_kwh_24h` ist reine Diagnose.

---

## v2.52.0 — Fixes aus externer Prüfung

Antworten/Fixes zu den obigen Prüfpunkten und der Live-Verifikation (HA lief v2.50.0; Repo-HEAD = die geprüften Changes):

### Fix A — Roundtrip-Verlust-Discharges (Hauptbefund)
[optimizer.py `remove_unprofitable_predischarge`](custom_components/battery_storage_manager/optimizer.py), verdrahtet im Coordinator nach `remove_dp_discharge_enclaves`.

Das v2.51.0-Gate (`revenue > 0.0005`) prüft nur den **Discharge-Leg** (`η·price − ½ Zyklus`) und ist blind dafür, dass dieselbe Energie später teuer zurückgekauft wird. Bei kleinem `discharge_kwh`-Cap entlädt der DP morgens Überschuss-Energie, die der Abend-Peak nicht aufnimmt; `smooth_plan` Pass 6 lädt mittags wieder auf → _sell-high/buy-low_-Zyklus mit negativem Roundtrip („netto −X ct"-Slots). Neuer Final-Pass demotet jeden Discharge vor einem späteren Charge mit `price·η − günstigster_späterer_Ladepreis − Zyklus ≤ 0`. `force_pre_solar`-Slots geschützt. Repro: 114 → 0 Verlust-Paare.

### Fix B — 7-Tage-Curtailment-Hebel war still tot (Prüfpunkt 4)
[history.py `summarize_7day_curtailment`](custom_components/battery_storage_manager/history.py)

Live verifiziert: `avg_hours_per_day_7d = 0` bei gleichzeitig 24h = 3,5 h. Ursache: SOC/Solar-Entities ohne `state_class` führen keine Long-Term-Statistics → leere Listen → still 0. Fix: Verfügbarkeit wird erkannt (`curtailment_7day_available`), einmalig als Warnung geloggt und als Sensor-Attribut `avg_hours_per_day_7d_available` ausgewiesen.

### Fix C — Reason „Platz für Solar schaffen" überzeichnet (Prüfpunkt 1)
[optimizer.py `compute_presolar_discharge_hours`](custom_components/battery_storage_manager/optimizer.py)

Live: 7 Slots als „Platz für Solar schaffen" gelabelt bei `pre_solar_forced_slots = 0` und nur ~0,07 kWh späterem Surplus. Das Label braucht jetzt **kumulierten** späteren Surplus ≥ 0,1 kWh; `force_pre_solar`-Slots bleiben immer markiert.

### Fix D — `force_pre_solar_discharge` Early-Exit (Prüfpunkt 2)
Guard hing am veralteten `excess_kwh`-Einmal-Estimate (1:1-Proxy je Discharge-Delta). Bei zwischengeschaltetem Charge, der freigewordenen Headroom frisst, brach die Schleife zu früh ab, während echter Overflow + freie Kandidaten blieben. Jetzt terminiert sie am re-simulierten `new_overflow`/Kandidaten-Exhaust.

### Fix E — WR-Ist-Sensor Staleness-Watchdog (v2.50.2-Folgefix)
[helpers.py `should_self_correct_target`](custom_components/battery_storage_manager/helpers.py)

Ein eingefrorener/alter Ist-Wert konnte Self-Correct fehlauslösen und einen nötigen Discharge drosseln. Jetzt nur bei `last_changed ≤ 120 s` vertraut (Self-Correct + Charger-Abschalt-Heuristik).

### Test-Qualität
- `test_discharge_chosen_despite_solar_absorption` testet jetzt wirklich den net-export-Fix (Solar ≈ Discharge → `new_si == si`; alte `new_si<si`-Regel würde scheitern — verifiziert).
- Neuer Test für die `solar_surplus > 0.05`-Exclusion in `force_pre_solar_discharge` (Prüfpunkt 1, vorher ungetestet).
- `test_prefers_expensive_slots`: vacuous `if forced > 0`-Guard entfernt → unbedingte Assertion.

**Test-Status:** 82/82 grün (vorher 56). Neue Dateien: `tests/test_history.py`, `tests/test_helpers.py`.

### Noch offen (bewusst nicht geändert)
- **Prüfpunkt 5 (Voting-Permissivität):** unverändert — dokumentierte Design-Entscheidung, min_soc schützt hart. Nur zur Diskussion markiert.
- **Prüfpunkt 7 (Charge-Branch Solar-Awareness):** unverändert (Fix A entschärft das Symptom; die DP-Asymmetrie bleibt als Modellierungs-Nuance).
- **Prüfpunkt 3 (0,0005-EUR-Threshold):** sinnvoll bestätigt; Hinweis: absolut statt energie-normiert → effektiv strenger bei sehr kleinen Slots.
- **Prüfpunkt 6 (Floor-Stufen konfigurierbar):** offen.

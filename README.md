# Battery Storage Manager

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-2.24.0-blue.svg)](https://github.com/dEeds83/ha-battery-storage-manager)

Eine Home Assistant Custom Integration zur intelligenten Steuerung von AC-gekoppelten Batteriespeichern basierend auf dynamischen Strompreisen (Tibber), Solarprognosen und lernender Verbrauchsoptimierung.

## Features

### Optimierung
- **Szenario-DP Optimierung** – Dynamic Programming über 3 Szenarien (erwartet/pessimistisch/optimistisch), asymmetrischer Vote: Expected bestimmt Laden, Majority bestimmt Entladen
- **Kalman-Filter Solar-Korrektur** – Kombiniert Forecast mit Ist-Messung: reagiert schnell auf Wetteränderungen ohne Überschwingen
- **Exponentielle Verbrauchsprognose** – Gewichteter Durchschnitt (α=0,85) bevorzugt aktuelle Tage, erkennt Trends
- **EPEX Predictor Terminal-Value** – Bestimmt ob Akku am Tibber-Ende voll oder leer sein soll (keine falschen Aktionen)
- **Batterie-Zykluskosten** – Konfigurierbarer Degradationskostenparameter (ct/kWh) verhindert unprofitable Mini-Arbitrage
- **Roundtrip-Effizienz** – Konfigurierbar (Standard 85%), wird automatisch aus Smartshunt V×I-Messdaten kalibriert wenn verfügbar
- **15-Minuten-Preisauflösung** – Volle Granularität dynamischer Tibber-Tarife (15/30/60 Min, auto-erkannt)
- **Voller Netzpreis für Lade-Entscheidung** – DP bewertet Laden zum vollen Netzpreis, nicht zum effektiven Preis. Solar-Überschuss wird in hold/idle automatisch opportunistisch geladen — kostenlos und ohne Netz-Risiko. So werden nur wirklich günstige Slots für Netz-Laden verwendet
- **Solar-Headroom** – Netz-Laden wird auf `grid_max_soc` begrenzt, damit genug Platz für erwarteten Solarüberschuss bleibt. Headroom wird nur bis zum nächsten Sonnenuntergang berechnet, damit morgige Prognosen günstiges Netz-Laden heute nicht blockieren
- **6-Pass Smoothing Pipeline:**
  - Pass 1: Enclave-Entfernung (einzelne Aktions-Slots ohne Nachbarn entfernen, Proximity-Check ±2 Slots)
  - Pass 2: Alternations-Dämpfung (Lade↔Entlade-Paare unter Break-Even-Spread → idle)
  - Pass 3: Entlade-Slot-Swap (günstigste Entlade-Slots mit teureren Idle-Slots tauschen)
  - Pass 4: Lade-Block-Zusammenführung (Satelliten-Blöcke in Hauptblock mergen, Inseln entfernen)
  - Pass 5: Spät-Verschiebung (Lade-Blöcke an späteste Position im gleichen Preisband)
  - Pass 6: Zielbasierte Rückwärts-Auffüllung (Idle→Charge vor Entlade-Blöcken, nur wenn profitabel: Ladepreis ≤ Ø Entladepreis × Effizienz − Zykluskosten)
- **Terminal-Value mit Unsicherheitsabschlag** – Basis-Wert aus Tibber-Median (70% Konfidenz), EPEX-Wert überschreibt wenn höher
- **Temperaturbasierte Verbrauchsprognose** – Außentemperatur-Sensor korrigiert Verbrauch (±2%/°C außerhalb 15-25°C Komfortzone)
- **Smartshunt-Integration** – Victron Smartshunt liefert V×A = echte Batterieleistung, automatische Effizienz-Kalibrierung (Lade-/Entlade-/Roundtrip-Effizienz als Sensor)
- **Optimierungs-Log** – Alle Entscheidungen (Szenarien, Kalman, Swaps) als Sensor im UI einsehbar

### Steuerung
- **Dynamische Ladegeräte-Anzahl** – Beliebig viele Ladegeräte mit individueller Leistung konfigurierbar
- **Intelligentes Solar-Laden** – Grid-Export wird automatisch erkannt und durch Zuschalten von Ladegeräten absorbiert (AC-gekoppelt)
- **PID-geregelte Nulleinspeisung** – Wechselrichter-Leistung wird sanft und schwingungsfrei geregelt (P/I/D)
- **Hysterese-Schaltung** – Mindest-Ein-/Ausschaltzeiten verhindern Ladegeräte-Flackern (120s/60s)
- **Geräte-Synchronisierung** – Interner Status wird bei jedem Zyklus mit echten Switch-Zuständen abgeglichen

### Weitere Features
- **Solarprognose-Integration** – Forecast.Solar, Solcast, mehrere Anlagen summierbar
- **Eigenverbrauchsoptimierung** – Batterieentladung zur Deckung des Hausverbrauchs
- **Manueller Modus** – Volle manuelle Kontrolle über Laden und Entladen
- **Runtime-Toggles** – Netzladen, Entladen und Solarprognose jederzeit ein-/ausschaltbar
- **Eingebaute Lovelace Cards** – Plan- und Status-Visualisierung ohne zusätzliche Frontend-Plugins
- **Live-Konfiguration** – Alle Einstellungen nachträglich änderbar, ohne Neustart

## Voraussetzungen

- Home Assistant 2024.1.0 oder neuer
- [Tibber Integration](https://www.home-assistant.io/integrations/tibber/) mit Pulse für Echtzeit-Verbrauchsdaten
- Schaltbare Ladegeräte und Wechselrichter (über Home Assistant Switches steuerbar)
- Optional: [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) oder [Solcast](https://github.com/BJReplay/ha-solcast-solar) für Solarprognosen

## Installation

### HACS (empfohlen)

1. Öffne HACS in Home Assistant
2. Klicke auf **Integrationen** > **Drei-Punkte-Menü** > **Benutzerdefinierte Repositories**
3. Repository-URL hinzufügen: `https://github.com/dEeds83/ha-battery-storage-manager`
4. Kategorie: **Integration**
5. Klicke auf **Herunterladen**
6. Home Assistant neu starten

### Manuelle Installation

1. Kopiere den Ordner `custom_components/battery_storage_manager` in dein Home Assistant `custom_components` Verzeichnis
2. Home Assistant neu starten

## Konfiguration

Die Einrichtung erfolgt über die Home Assistant UI in drei Schritten:

**Einstellungen** > **Geräte & Dienste** > **Integration hinzufügen** > **Battery Storage Manager**

### Schritt 1: Tibber & Solar

| Parameter | Beschreibung | Pflicht |
|-----------|-------------|---------|
| Tibber Preis-Sensor | Aktueller Strompreis (z.B. `sensor.electricity_price`) | Ja |
| Tibber Preisprognose | Sensor mit today/tomorrow Preisattributen | Nein |
| Verbrauch (Pulse) | Aktueller Netzbezug in Watt | Ja |
| Einspeisung (Pulse) | Aktuelle Netzeinspeisung in Watt | Ja |
| Solar-Forecast-Sensor | Einzelner Solarsensor (Forecast.Solar oder Solcast) | Nein |
| Weitere Solar-Sensoren | Mehrfachauswahl für zusätzliche Solaranlagen | Nein |
| Solar-Leistung Sensor | Aktuelle PV-Produktion in Watt (für exakte Verbrauchsberechnung) | Nein |
| Solar-Energie heute Sensor | Tägliche PV-Produktion in kWh (für Prognose-Kalibrierung) | Nein |
| Außentemperatur-Sensor | Verbessert Verbrauchsprognose (Heizung/Kühlung) | Nein |
| EPEX Predictor aktivieren | Erweitert Preisprognose über Tibber-Fenster hinaus mit EPEX-Spotmarkt-Vorhersagen | Nein |
| EPEX Predictor Region | Gebotszone (DE, AT, BE, NL, SE1-4, DK1-2) | DE |

### Schritt 2: Geräte

| Parameter | Beschreibung | Standard |
|-----------|-------------|----------|
| Ladegeräte Schalter | Multi-Select aller Ladegerät-Switches | – |
| Standard-Leistung pro Ladegerät | Nennleistung in Watt (gilt für alle neuen Ladegeräte) | 800 W |
| Einspeise-Wechselrichter Switch | Schalter für Feed-Inverter (optional) | – |
| Einspeise-Wechselrichter Power-Entity | Number/Input-Number-Entity für Leistungsregelung (optional) | – |
| Einspeise-Wechselrichter Leistung | Maximale Nennleistung in Watt | 800 W |
| Einspeise-Wechselrichter Ist-Leistung | Sensor mit aktueller Wechselrichter-Ausgangsleistung (optional) | – |

> **Hinweis:** Es können beliebig viele Ladegeräte hinzugefügt werden. Im Options Flow behalten bestehende Ladegeräte ihre individuelle Leistung, neue bekommen die Standard-Leistung.

### Schritt 3: Batterie

| Parameter | Beschreibung | Standard |
|-----------|-------------|----------|
| SOC-Sensor | Ladezustand der Batterie (%) | – |
| Kapazität | Batteriekapazität in kWh | 5.0 kWh |
| Min SOC | Minimaler Ladezustand | 10% |
| Max SOC | Maximaler Ladezustand | 95% |
| Preisschwelle niedrig | Laden unter diesem Preis (Fallback ohne Prognose) | 15 ct/kWh |
| Preisschwelle hoch | Entladen über diesem Preis (Fallback ohne Prognose) | 30 ct/kWh |
| Hausverbrauch | Durchschnittlicher Verbrauch in Watt (Startwert, wird durch Lernfunktion ersetzt) | 500 W |
| Zykluskosten | Degradationskosten pro Lade-/Entladezyklus in ct/kWh | 10 ct/kWh |
| Roundtrip-Effizienz | Gesamteffizienz eines Lade-/Entladezyklus in Prozent | 90% |
| Batterie-Spannungs-Sensor | Victron Smartshunt Spannung (optional) | – |
| Batterie-Strom-Sensor | Victron Smartshunt Strom (optional) | – |

### Nachträgliche Anpassung

Alle Einstellungen können jederzeit nachträglich geändert werden:

**Einstellungen** > **Geräte & Dienste** > **Battery Storage Manager** > **Konfigurieren**

Änderungen werden **sofort übernommen**, kein Neustart nötig.

### Migration

Bestehende Installationen mit der alten Konfiguration (Ladegerät 1/2) werden beim Update **automatisch migriert**. Die Config-Entry-Version wird von 1 auf 2 angehoben.

## Mehrere Solaranlagen

Die Integration unterstützt beliebig viele Solarprognose-Sensoren. Alle Prognosen werden **pro Stunde aufsummiert** und bei 15-Min-Slots gleichmäßig aufgeteilt.

| Anbieter | Format | Erkannt über |
|----------|--------|-------------|
| Forecast.Solar | `watt_hours_period` Attribut | `{datetime: Wh}` Dict |
| Forecast.Solar (kumulativ) | `watt_hours` Attribut | Kumulatives `{datetime: Wh}` Dict |
| Forecast.Solar (Energy Platform) | `runtime_data.wh_period` | Config Entry Runtime Data |
| Solcast | `forecast` Attribut | `[{period_start, pv_estimate}]` Liste |

## Entitäten

### Sensoren

| Sensor | Beschreibung |
|--------|-------------|
| Betriebsmodus | Aktueller Modus (Laden Netz / Laden Solar / Entladen / Leerlauf) mit erweiterten Statusattributen |
| Strategie | Aktive Strategie (Preisoptimiert / Eigenverbrauch / Manuell) |
| Aktueller Strompreis | Strompreis in EUR/kWh mit günstigen/teuren Stunden als Attribute |
| Speicher Ladestand | Ladezustand in Prozent mit dynamischem Batterie-Icon |
| Netzleistung | Aktueller Netzbezug/-einspeisung in Watt mit Richtungsanzeige |
| Ladegerät N Status | Aktiv/Inaktiv pro konfiguriertem Ladegerät (dynamisch erzeugt) mit Leistung als Attribut |
| Wechselrichter Status | Aktiv / Inaktiv |
| Wechselrichter Leistung | Aktuelle Ist-Leistung des Einspeise-Wechselrichters in Watt |
| Wechselrichter Soll-Leistung | Vom Plugin gesetzter Zielwert für den Wechselrichter in Watt |
| Nächstes günstiges Fenster | Zeitpunkt der nächsten günstigen Preisperiode |
| Nächstes teures Fenster | Zeitpunkt der nächsten teuren Preisperiode |
| Speicherplan | Tagesplan-Zusammenfassung mit vollständigem Plan als Attribut |
| Geplante Aktion | Aktuelle Aktion dieses Zeitslots (Laden/Entladen/Solar/Halten/Inaktiv) |
| Erwartete Solarproduktion | Verbleibende erwartete Solarproduktion heute in kWh |
| Verbrauchsprognose | Vorhergesagter Hausverbrauch der aktuellen Stunde (W), 24h-Forecast als Attribut |
| Preisprognose | Nächste 12h Strompreise als CSV + Attribute (min/max/avg, slot_minutes, actions_csv mit D/C/H/I pro Stunde) |
| Solar Korrekturfaktor | Kalibrierungsfaktor für Solarprognosen (1.0 = exakt, <1 = Forecast überschätzt, >1 = unterschätzt) mit Intraday-Faktor als Attribut |
| Optimierungs-Log | Letzte Optimierungsentscheidung als State, vollständiges Log (max 50 Einträge) als Attribut |
| Aktionshistorie | Tatsächlich ausgeführte Aktionen (48h, 10-Min-Intervalle, persistent) |
| Gemessene Effizienz | Roundtrip-Effizienz aus Smartshunt V×I vs. Charger/Inverter-Leistung (Lade-/Entlade-/Roundtrip als Attribute) |

### Schalter

| Schalter | Beschreibung |
|----------|-------------|
| Automatik-Modus | Umschalten zwischen Automatik und Manuell |
| Zwangsladen | Manuelles Laden erzwingen |
| Zwangsentladen | Manuelles Entladen erzwingen |
| Netzladen erlauben | Laden aus dem Stromnetz erlauben/verbieten |
| Entladen erlauben | Batterieentladung erlauben/verbieten |
| Solarprognose nutzen | Solarbasierte Planung ein-/ausschalten |

### Zahlenwerte (Slider)

| Slider | Bereich | Schrittweite |
|--------|---------|-------------|
| Min SOC | 0–50% | 5% |
| Max SOC | 50–100% | 5% |
| Preisschwelle niedrig | 0–50 ct/kWh | 1 ct |
| Preisschwelle hoch | 0–100 ct/kWh | 1 ct |

## Eingebaute Dashboard-Cards

Die Integration liefert zwei Custom Lovelace Cards mit, die **automatisch geladen** werden. Die Cards erscheinen im Card-Picker und unterstützen den visuellen Editor.

### Battery Plan Card

Visualisiert den Speicherplan als farbcodiertes Balkendiagramm mit 15-Minuten-Auflösung:

```yaml
type: custom:battery-plan-card
entity: sensor.battery_storage_manager_speicherplan
title: Speicherplan
show_legend: true    # optional, Standard: true
show_solar: true     # optional, Standard: true
price_entity: sensor.battery_storage_manager_preisprognose  # optional, für EPEX-Markierung
```

**Funktionen:**
- Farbige Balken pro Zeitslot (Grün = Laden, Orange = Entladen, Blau = Halten, Grau = Idle)
- Preisachse links in ct/kWh
- Solarproduktion als goldene SVG-Linie (responsive, skaliert korrekt bei jeder Breite)
- Aktueller Zeitslot hervorgehoben mit blauem Jetzt-Marker
- Legende mit Dauer pro Aktionstyp (z.B. "Laden (2h15)")
- Aufklappbare Detailtabelle mit Preis, Solar, erwartetem SOC, Aktion und Begründung
- EPEX-Prognose-Slots optional einblendbar (Button "Prognose anzeigen", gestreiftes Muster)

### Battery Status Card

Kompakte Statusübersicht mit SOC-Ring und Live-Daten:

```yaml
type: custom:battery-status-card
entity: sensor.battery_storage_manager_betriebsmodus
title: Batteriespeicher
```

**Funktionen:**
- SOC als animierter Ringindikator (Grün > 60%, Orange > 30%, Rot darunter)
- Aktueller Strompreis in ct/kWh
- Betriebsmodus mit farbigem Icon
- Netzbezug/-einspeisung mit Richtung und Wattzahl
- Wechselrichter-Leistung (wenn aktiv)
- Strategie-Badge
- Integrierte Toggle-Switches

## Services

| Service | Beschreibung |
|---------|-------------|
| `battery_storage_manager.set_strategy` | Strategie wechseln (`price_optimized` / `self_consumption` / `manual`) |
| `battery_storage_manager.force_charge` | Laden erzwingen (wechselt zu Manuell) |
| `battery_storage_manager.force_discharge` | Entladen erzwingen (wechselt zu Manuell) |
| `battery_storage_manager.stop` | Alle Lade-/Entladevorgänge stoppen |

## Funktionsweise

### Planungszyklus (alle 15 Sekunden)

1. **Sensoren lesen** – SOC, Netzleistung, Strompreis, Switch-Zustände
2. **Geräte synchronisieren** – Interne Flags mit echten Switch-Zuständen abgleichen
3. **Verbrauch erfassen** – Aktuellen Hausverbrauch für rollende Statistik aufzeichnen
4. **Preise laden** – 15-Min-Preise via `tibber.get_prices` Action (oder Fallback auf Attribute)
5. **Solarprognose lesen** – Alle konfigurierten Solar-Sensoren aufsummieren
6. **Intraday Solar-Korrektur** – Restprognose anhand bisheriger Ist/Forecast-Ratio anpassen (Kalman-Filter)
7. **Batterieplan erstellen (DP):**
   - Ladekosten pro Slot (voller Netzpreis — Solar-Surplus wird opportunistisch in hold/idle geladen)
   - Dynamic Programming über alle Tibber-Slots: SOC diskretisiert in 0,5%-Stufen (~150 Levels)
   - 3 Szenarien (erwartet/optimistisch/pessimistisch) mit asymmetrischem Vote
   - Zykluskosten (½ auf Laden, ½ auf Entladen) und Roundtrip-Effizienz
   - Terminal-Value am Planende (Basis + EPEX) mit Unsicherheitsabschlag
   - 6-Pass Smoothing (Enclave → Alternation → Swap → Merge → Late-Shift → Profitables Backward-Fill)
   - Optimaler SOC-Pfad mit maximalem Profit extrahiert
8. **Aktion ausführen** – Ladegeräte/Wechselrichter entsprechend schalten

### Dynamic Programming Optimierung

Statt einfachem greedy Pairing nutzt der Algorithmus **Dynamic Programming** (Bellman-Rückwärtsinduktion) über diskretisierte SOC-Stufen (0,5%-Schritte):

```
dp[t][soc] = maximaler Profit erreichbar ab Zeitpunkt t mit Ladezustand soc
```

Für jeden Slot werden drei Optionen bewertet:
- **Idle**: Nichts tun (kein Gewinn/Verlust)
- **Laden** (≥ bei Gleichstand): Strom kaufen (Kosten = voller Netzpreis × kWh + ½ Zykluskosten). Solar-Überschuss wird separat in hold/idle durch opportunistisches Laden eingefangen — kostenlos und ohne Prognoserisiko
- **Entladen** (> strikt): Strom zurückspeisen (Erlös = Preis × kWh × Effizienz − ½ Zykluskosten)

**Szenario-DP:** Das DP wird 3× ausgeführt (Solar ×0.6/×1.0/×1.2, Verbrauch ×1.2/×1.0/×0.8). Asymmetrischer Vote: Expected-Szenario bestimmt **Laden**, Mehrheit bestimmt **Entladen** (konservativ).

**Terminal-Value:** Am Planende hat gespeicherte Energie einen Wert:
```
Basis-TV = Median(Tibber-Preise) × Effizienz × 0.7 − ½ Zykluskosten
EPEX-TV  = Median(EPEX-Prognose) × Effizienz × 0.7 − ½ Zykluskosten
TV = max(Basis, EPEX)  →  DP bevorzugt hohen End-SOC wenn morgen teuer
```

**Post-Smoothing Passes:**
- **SOC-Cleanup:** Infeasible Discharge-Slots (SOC ≤ min_soc) werden zu idle konvertiert
- **Final Pass 3:** Re-run nach SOC-Cleanup, verschiebt günstige Entladungen in teure freigewordene Slots
- **SOC-aware Reorder:** Benachbarte Discharge→Idle-Paare werden getauscht wenn der Idle-Slot teurer ist und SOC > min_soc
- **Charge Gap Fill:** Idle-Lücken innerhalb von Lade-Blöcken werden geschlossen wenn Preis ≤ Nachbar-Preis

Zusätzlich wird bei idle/hold zur Laufzeit **Grid-Export automatisch durch Charger-Zuschalten absorbiert**.

**Vorteile gegenüber greedy:**
- Findet das **globale Optimum** über alle Zeitslots
- Berücksichtigt **SOC-Limits** in der Bewertung (statt nachträglicher Korrektur)
- Integriert **Effizienz und Zykluskosten** direkt in die Bewertung
- Optimiert automatisch über **48h+** wenn morgen-Preise verfügbar sind
- EPEX Terminal-Value erweitert den Horizont ohne falsche Aktionen zu erzeugen
- Bei 96 Slots (24h × 15min) × 150 SOC-Stufen × 3 Szenarien ≈ ~45.000 Zustände (< 15ms Rechenzeit)

### EPEX Predictor (optional)

Wenn aktiviert, beeinflusst die EPEX-Prognose die Planung über einen **Terminal-Value** – es werden **keine** Lade-/Entlade-Aktionen für prognostizierte Zeiträume erzeugt:

- **Datenquelle:** [EpexPredictor](https://github.com/b3nn0/EpexPredictor) – statistisches Modell basierend auf Wetter- und Lastdaten
- **Lineare Regression:** Berechnet `Tibber ≈ a + b × EPEX` aus dem Überlappungsbereich (Fixkosten + MwSt)
- **Terminal-Value:** Bestimmt ob der Akku am Ende des Tibber-Fensters voll oder leer sein soll
  - Hohe EPEX-Preise vorhergesagt → DP bewertet hohen End-SOC positiv → lädt mehr
  - Niedrige EPEX-Preise → End-SOC neutral → entlädt alles Profitable
- **Visualisierung:** EPEX-Preise optional in der Plan-Card einblendbar (Button "Prognose anzeigen")
- **Caching:** Alle 2 Stunden aktualisiert
- **Regionen:** DE (Standard), AT, BE, NL, SE1-4, DK1-2

**Beispiel:** Tibber endet morgen 23:45. EPEX sagt hohe Preise für übermorgen vorher → Terminal-Value 5 ct/kWh → DP entscheidet sich, den Akku am Tibber-Ende bei 80% statt 20% zu belassen.

### Solar-Laden (AC-gekoppelt)

Das System ist auf AC-gekoppelte Speicher ausgelegt: Solarüberschuss fließt durchs Hausnetz und braucht die Ladegeräte, um in die Batterie zu kommen.

| Grid-Export | Aktion |
|---|---|
| < -100W (Export) | Nächsten Charger einschalten |
| -100W bis +200W | PID-Inverter regelt Feinabstimmung |
| > +200W (Import) | Letzten Charger ausschalten |

Kein Strom wird ins Netz verschenkt: Bei Grid-Export wird automatisch der nächste verfügbare Charger eingeschaltet. Bei zu viel Grid-Import wird der letzte Charger wieder abgeschaltet. Der PID-Inverter regelt die Nulleinspeisung dazwischen.

**Opportunistisches Solar-Laden:** Auch bei Plan-Aktionen "Halten" und "Idle" wird Solarüberschuss automatisch mitgenommen. Kostenlose Solarenergie wird nie verschenkt – der Plan kontrolliert nur Netz-Laden und Entlade-Zeitpunkte.

**Solar über max_soc:** Auch wenn der SOC das konfigurierte Maximum erreicht hat, wird reiner Solarüberschuss weiterhin geladen (kostenlose Energie). Nur der Wechselrichter-Defizit-Modus (der Netzstrom nutzt) wird über max_soc blockiert.

| SOC | Reiner Solar | Solar + WR-Defizit | Netz-Laden |
|---|---|---|---|
| < max_soc | ✅ | ✅ | ✅ |
| ≥ max_soc | ✅ Kostenlos | ❌ Zieht Netzstrom | ❌ |

**Betriebsmodus:** Der Sensor zeigt `solar_charging` (gold) wenn von Solar geladen wird, `charging` (grün) bei Netz-Laden – so ist im Dashboard sofort erkennbar, woher die Energie kommt.

### PID-geregelte Nulleinspeisung

Statt einfacher additiver Anpassung nutzt der Wechselrichter einen PID-Regler:
- **P** (proportional, Kp=0.6): Sofortige Reaktion auf Abweichung
- **I** (integral, Ki=0.15): Gleicht dauerhafte Offsets aus
- **D** (derivative, Kd=0.1): Dämpft schnelle Schwankungen
- **Anti-Windup**: Begrenzt den Integralterm
- **Asymmetrische Regelung**: Export sofort korrigieren, 0-50W Import tolerieren
- **Setpoint**: 25W Netzbezug (Mitte der 0-50W Toleranzzone)

### Lernende Verbrauchsprognose

Der konfigurierte Hausverbrauch (z.B. 500W) dient nur als Startwert. Die Integration lernt den tatsächlichen Verbrauch:

- **Erfassung** alle 30 Sekunden, Durchschnitt beim Stundenwechsel gespeichert
- **14-Tage rollender Durchschnitt** pro Tagesstunde (0-23)
- **Wochentag/Wochenende getrennt** – Mo-Fr und Sa-So haben eigene Profile
- **48h-fähig** – Morgen-Slots nutzen das passende Profil (z.B. Samstag-Daten für Samstag-Plan)
- **Persistent** (überlebt Neustarts, automatische Migration von v1-Format)
- **Charger/Inverter herausgerechnet** → reiner Hausverbrauch
- Beispiel: Wochentags 200W nachts, 400W morgens, 800W abends; Wochenende gleichmäßiger

### Solar-Prognose-Kalibrierung

Wenn der "Solar-Energie heute" Sensor konfiguriert ist, lernt die Integration auf zwei Ebenen:

**Tägliche Kalibrierung (um 20:00):**
- Vergleich Ist-Produktion vs. Forecast → Ratio (z.B. 8kWh / 10kWh = 0.8)
- 14-Tage rollender Durchschnitt der Ratios = Korrekturfaktor
- Vergleich erfolgt mit dem **Roh-Forecast** (Kalibrierungs- und Intraday-Faktor werden herausgerechnet um Zirkelschlüsse zu vermeiden)
- Bereich: 0.3–3.0, persistent gespeichert

**Intraday-Korrektur (ab 08:00, laufend):**
- Vergleicht die bisherige Ist-Produktion mit dem bisherigen Forecast
- Wenn um 11:00 erst 30% statt 50% des Forecasts produziert → Restprognose × 0.6
- Reagiert sofort auf Wetterumschwünge (z.B. plötzliche Bewölkung)
- Sensor-Attribut `intraday_factor` zeigt den aktuellen Tagesfaktor

Beispiel: Forecast sagt 10 kWh, um 12:00 erst 2 kWh statt 4 kWh → Restprognose wird halbiert → Nachmittags-Planung realistischer.

### Strategien

| Strategie | Verhalten |
|-----------|-----------|
| **Preisoptimiert** | Folgt dem Arbitrage-optimierten Speicherplan |
| **Eigenverbrauch** | Entlädt bei Netzbezug, lädt bei Überschuss – unabhängig vom Preis |
| **Manuell** | Keine automatische Steuerung, nur manuelle Aktionen über Schalter/Services |

### Runtime-Toggles

| Toggle | Wenn AUS |
|--------|----------|
| Netzladen erlauben | Plan-Aktionen "charge" werden übersprungen (idle stattdessen) |
| Entladen erlauben | Plan-Aktionen "discharge" werden übersprungen |
| Solarprognose nutzen | Solarprognosen werden nicht gelesen, Plan basiert nur auf Preisen |

## ePaper Dashboard (optional)

Im Ordner `esphome/` liegt eine fertige ESPHome-Konfiguration für ePaper-Displays. Unterstützte Boards:

| Board | Datei |
|---|---|
| Seeed XIAO ESP32-C3 | `boards/xiao_c3.yaml` |
| TRMNL 7.5" OG DIY Kit (ESP32-S3) | `boards/trmnl_diy_s3.yaml` (inkl. Batterie-Anzeige) |

Gemeinsames Dashboard-Layout in `dashboard-common.yaml`:
- **Header:** Titel, Uhrzeit, Datum, Geräte-Akku (S3)
- **Row 1:** SOC + Modus, Strompreis, Netzleistung, Verbrauchsprognose, Aktion
- **Row 2:** Planzusammenfassung
- **Row 3:** Solar-Leistung, Außentemperatur, Solar-Prognose, Solar heute, Netzbezug, Tageskosten
- **Chart:** 12h Preisverlauf mit Entlade-Schraffur (Punktmuster) und Lade-Markierung (horizontale Linien)
- Deep-Sleep alle 15 Minuten (Wake um :01, :16, :31, :46)
- Nachtmodus 01:00-06:00
- Deutsches Zahlenformat (Komma statt Punkt)

Neues Display einrichten: `battery-epaper.yaml` kopieren, `device_name` + `board: !include` anpassen, Secrets ergänzen.

## Lizenz

MIT License – siehe [LICENSE](LICENSE) für Details.

# Battery Storage Manager

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-2.2.0-blue.svg)](https://github.com/dEeds83/ha-battery-storage-manager)

Eine Home Assistant Custom Integration zur intelligenten Steuerung von AC-gekoppelten Batteriespeichern basierend auf dynamischen Strompreisen (Tibber), Solarprognosen und lernender Verbrauchsoptimierung.

## Features

### Optimierung
- **Dynamic Programming Optimierung** – Findet den global optimalen SOC-Pfad über alle Zeitslots (statt greedy Pairing)
- **EPEX Predictor Integration** – Erweitert das Planungsfenster über Tibber Day-Ahead hinaus mit skalierten EPEX-Spot-Prognosen (bis 4 Tage)
- **48h+ Lookahead** – Optimiert über morgen hinaus: Tibber-Preise + EPEX-Prognose automatisch kombiniert
- **Batterie-Zykluskosten** – Konfigurierbarer Degradationskostenparameter (ct/kWh) verhindert unprofitable Mini-Arbitrage
- **Roundtrip-Effizienz** – Konfigurierbarer Effizienzfaktor (Standard 90%) wird in die Entlade-Bewertung einberechnet
- **15-Minuten-Preisauflösung** – Nutzt die volle Granularität dynamischer Tibber-Tarife (15/30/60 Min, auto-erkannt)
- **Effektive Ladekosten** – Solar-unterstützte Stunden werden bevorzugt (z.B. 50% Solar → halber Netzpreis)
- **Pre-Solar-Entladung** – Entlädt proaktiv vor Solar-Stunden um Platz für kostenlose Solarenergie zu schaffen
- **Lernende Verbrauchsprognose** – 14-Tage rollender Durchschnitt, getrennt nach Wochentag/Wochenende
- **Solar-Prognose-Kalibrierung** – Lernt aus Abweichung Forecast vs. Ist, mit Intraday-Korrektur bei Wetterumschwung
- **Intraday Solar-Korrektur** – Passt die Restprognose laufend an (Ist-Produktion vs. bisheriger Forecast)
- **Optimierungs-Log** – Alle Entscheidungen (Umplanungen, Korrekturen) als Sensor im UI einsehbar

### Steuerung
- **Dynamische Ladegeräte-Anzahl** – Beliebig viele Ladegeräte mit individueller Leistung konfigurierbar
- **Intelligentes Solar-Laden** – Ladegeräte werden proportional zum Solarüberschuss zugeschaltet (AC-gekoppelt)
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
| Preisprognose | Nächste 12h Strompreise als CSV + Attribute (min/max/avg, slot_minutes) |
| Solar Korrekturfaktor | Kalibrierungsfaktor für Solarprognosen (1.0 = exakt, <1 = Forecast überschätzt, >1 = unterschätzt) mit Intraday-Faktor als Attribut |
| Optimierungs-Log | Letzte Optimierungsentscheidung als State, vollständiges Log (max 50 Einträge) als Attribut |

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
```

**Funktionen:**
- Farbige Balken pro Zeitslot (Grün = Laden, Orange = Entladen, Gold = Solar, Blau = Halten, Grau = Idle)
- Preisachse links in ct/kWh
- Solarproduktion als goldene Linie
- Aktueller Zeitslot hervorgehoben mit blauem Jetzt-Marker
- Legende mit Dauer pro Aktionstyp (z.B. "Laden (2h15)", "Solar (1h30)")
- Aufklappbare Detailtabelle mit Preis, Solar, erwartetem SOC, Aktion und Begründung
- Tooltip mit Details bei Hover

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

### Planungszyklus (alle 30 Sekunden)

1. **Sensoren lesen** – SOC, Netzleistung, Strompreis, Switch-Zustände
2. **Geräte synchronisieren** – Interne Flags mit echten Switch-Zuständen abgleichen
3. **Verbrauch erfassen** – Aktuellen Hausverbrauch für rollende Statistik aufzeichnen
4. **Preise laden** – 15-Min-Preise via `tibber.get_prices` Action (oder Fallback auf Attribute)
5. **Solarprognose lesen** – Alle konfigurierten Solar-Sensoren aufsummieren
6. **Intraday Solar-Korrektur** – Restprognose anhand bisheriger Ist/Forecast-Ratio anpassen
7. **Batterieplan erstellen (DP):**
   - Effektive Ladekosten pro Slot (Netzpreis × Grid-Anteil)
   - Dynamic Programming über alle Slots (bis 48h): SOC diskretisiert in 5%-Stufen
   - Jeder Slot wird mit 4 Optionen bewertet (Laden/Solar/Entladen/Idle)
   - Zykluskosten und Roundtrip-Effizienz in der Bewertung
   - Pre-Solar-Entladung (Platz für Solar schaffen)
   - Optimaler SOC-Pfad mit maximalem Profit extrahiert
7. **Aktion ausführen** – Ladegeräte/Wechselrichter entsprechend schalten

### Dynamic Programming Optimierung

Statt einfachem greedy Pairing nutzt der Algorithmus **Dynamic Programming** (Bellman-Rückwärtsinduktion) über diskretisierte SOC-Stufen (5%-Schritte):

```
dp[t][soc] = maximaler Profit erreichbar ab Zeitpunkt t mit Ladezustand soc
```

Für jeden Slot werden vier Optionen bewertet:
- **Idle**: Nichts tun (kein Gewinn/Verlust)
- **Laden**: Netzstrom kaufen (Kosten = effektiver Preis × kWh + Zykluskosten)
- **Solar-Laden**: Solarüberschuss nutzen (nur halbe Zykluskosten, kein Strompreis)
- **Entladen**: Strom zurückspeisen (Erlös = Preis × kWh × Effizienz − Zykluskosten)

**Vorteile gegenüber greedy:**
- Findet das **globale Optimum** über alle Zeitslots
- Berücksichtigt **SOC-Limits** in der Bewertung (statt nachträglicher Korrektur)
- Integriert **Effizienz und Zykluskosten** direkt in die Bewertung
- Optimiert automatisch über **48h+** wenn morgen-Preise oder EPEX-Prognosen verfügbar sind
- Bei 96 Slots (24h × 15min) × 19 SOC-Stufen = ~1800 Zustände (< 1ms Rechenzeit)

### EPEX Predictor (optional)

Wenn aktiviert, erweitert die Integration das Planungsfenster über Tibber's Day-Ahead-Preise hinaus:

- **Datenquelle:** [EpexPredictor](https://github.com/b3nn0/EpexPredictor) – statistisches Modell basierend auf Wetter- und Lastdaten
- **Skalierung:** Berechnet automatisch einen Markup-Faktor aus dem Überlappungsbereich Tibber ↔ EPEX (Netzentgelte, Steuern, Tibber-Marge)
- **Anwendung:** EPEX-Spotpreise × Markup = geschätzte Endkundenpreise für zukünftige Stunden
- **Caching:** Alle 30 Minuten aktualisiert, bis 96h Vorhersage
- **Regionen:** DE (Standard), AT, BE, NL, SE1-4, DK1-2

**Beispiel:** Tibber liefert Preise bis morgen 23:45. EPEX Predictor ergänzt Übermorgen und Danach. Der Markup-Faktor gleicht den Unterschied zwischen Spotmarkt und Endkundenpreis aus (z.B. EPEX 5ct → Tibber 25ct → Faktor 5,0×).

### Solar-Laden (AC-gekoppelt)

Das System ist auf AC-gekoppelte Speicher ausgelegt: Solarüberschuss fließt durchs Hausnetz und braucht die Ladegeräte, um in die Batterie zu kommen.

| Solarüberschuss | Aktion |
|---|---|
| ≥ 80% aller Ladegeräte | Alle Ladegeräte an |
| ≥ 80% eines Ladegeräts | Größtes passendes an |
| ≥ 100W (< 80% Ladegerät) | Kleinstes Ladegerät + Wechselrichter deckt Defizit |
| < 100W | Idle (zu wenig Überschuss) |

Der **wahre Solarüberschuss** wird bei jedem Zyklus berechnet: gemessener Export + Leistung aktiver Ladegeräte + Wechselrichter-Einspeisung. So wird Oszillation verhindert.

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

Im Ordner `esphome/` liegt eine fertige ESPHome-Konfiguration für das **Seeed Studio XIAO 7.5" ePaper Panel** (800×480). Das Display zeigt:

- SOC mit Betriebsmodus, Strompreis, Netzleistung, Verbrauch, aktuelle Aktion
- Günstigstes und teuerstes Zeitfenster
- 12h Preiskurve mit Min/Max-Markierungen
- Deep-Sleep alle 15 Minuten (Wake um :01, :16, :31, :46)
- Nachtmodus 01:00-06:00 (ein langer Schlaf bis 06:01)

Details: [esphome/README.md](esphome/README.md)

## Lizenz

MIT License – siehe [LICENSE](LICENSE) für Details.

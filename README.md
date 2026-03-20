# Battery Storage Manager

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-1.5.0-blue.svg)](https://github.com/dEeds83/ha-battery-storage-manager)

Eine Home Assistant Custom Integration zur intelligenten Steuerung von Batteriespeichern basierend auf Strompreisen (Tibber), Solarprognosen und Eigenverbrauchsoptimierung.

## Features

- **Preisoptimierte Steuerung** – Automatisches Laden bei günstigen Strompreisen und Entladen bei teuren Preisen basierend auf 24-48h Preisprognosen
- **Solarprognose-Integration** – Berücksichtigung von Solarertragsprognosen (Forecast.Solar, Solcast) inkl. Unterstützung mehrerer Anlagen
- **Intelligenter Speicherplan** – Stündlicher Aktionsplan mit farbcodierter Visualisierung direkt im Dashboard
- **Eigenverbrauchsoptimierung** – Batterieentladung zur Deckung des Hausverbrauchs, Laden bei Solarüberschuss
- **Manueller Modus** – Volle manuelle Kontrolle über Laden und Entladen
- **Zero-Feed-Regelung** – Automatische Wechselrichter-Leistungsanpassung zur Vermeidung von Netzeinspeisung
- **Dual-Charger-Unterstützung** – Steuerung von zwei Ladegeräten und einem Einspeise-Wechselrichter
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

### Schritt 2: Geräte

| Parameter | Beschreibung | Standard |
|-----------|-------------|----------|
| Ladegerät 1 Switch | Schalter für erstes Ladegerät | – |
| Ladegerät 1 Leistung | Nennleistung in Watt | 800 W |
| Ladegerät 2 Switch | Schalter für zweites Ladegerät | – |
| Ladegerät 2 Leistung | Nennleistung in Watt | 800 W |
| Einspeise-Wechselrichter Switch | Schalter für Feed-Inverter (optional) | – |
| Einspeise-Wechselrichter Power-Entity | Number/Input-Number-Entity für Leistungsregelung (optional) | – |
| Einspeise-Wechselrichter Leistung | Maximale Nennleistung in Watt | 800 W |
| Einspeise-Wechselrichter Ist-Leistung | Sensor mit aktueller Wechselrichter-Ausgangsleistung (optional) | – |

### Schritt 3: Batterie

| Parameter | Beschreibung | Standard |
|-----------|-------------|----------|
| SOC-Sensor | Ladezustand der Batterie (%) | – |
| Kapazität | Batteriekapazität in kWh | 5.0 kWh |
| Min SOC | Minimaler Ladezustand | 10% |
| Max SOC | Maximaler Ladezustand | 95% |
| Preisschwelle niedrig | Laden unter diesem Preis | 15 ct/kWh |
| Preisschwelle hoch | Entladen über diesem Preis | 30 ct/kWh |
| Hausverbrauch | Durchschnittlicher Verbrauch in Watt | 500 W |

### Nachträgliche Anpassung

Alle Einstellungen können jederzeit nachträglich geändert werden:

**Einstellungen** > **Geräte & Dienste** > **Battery Storage Manager** > **Konfigurieren**

Der Options-Flow durchläuft die gleichen drei Schritte mit vorausgefüllten aktuellen Werten. Änderungen werden **sofort übernommen**, kein Neustart nötig.

## Mehrere Solaranlagen

Die Integration unterstützt beliebig viele Solarprognose-Sensoren. Alle Prognosen werden **pro Stunde aufsummiert**, verschiedene Formate können gemischt werden:

| Anbieter | Format | Erkannt über |
|----------|--------|-------------|
| Forecast.Solar | `watt_hours_period` Attribut | `{datetime: Wh}` Dict |
| Forecast.Solar (kumulativ) | `watt_hours` Attribut | Kumulatives `{datetime: Wh}` Dict |
| Solcast | `forecast` Attribut | `[{period_start, pv_estimate}]` Liste |

**Beispiel:** Zwei Dachflächen + Garage als separate Solcast-Accounts:
- `sensor.solcast_dach_sued` → Solar-Forecast-Sensor (Hauptfeld)
- `sensor.solcast_dach_west` + `sensor.forecast_solar_garage` → Weitere Solar-Sensoren

## Entitäten

### Sensoren

| Sensor | Beschreibung |
|--------|-------------|
| Betriebsmodus | Aktueller Modus (Laden / Entladen / Leerlauf) mit erweiterten Statusattributen |
| Strategie | Aktive Strategie (Preisoptimiert / Eigenverbrauch / Manuell) |
| Aktueller Strompreis | Strompreis in EUR/kWh mit günstigen/teuren Stunden als Attribute |
| Speicher Ladestand | Ladezustand in Prozent mit dynamischem Batterie-Icon |
| Netzleistung | Aktueller Netzbezug/-einspeisung in Watt mit Richtungsanzeige |
| Ladegerät 1 Status | Aktiv / Inaktiv |
| Ladegerät 2 Status | Aktiv / Inaktiv |
| Wechselrichter Status | Aktiv / Inaktiv |
| Wechselrichter Leistung | Aktuelle Ist-Leistung des Einspeise-Wechselrichters in Watt |
| Wechselrichter Soll-Leistung | Vom Plugin gesetzter Zielwert für den Wechselrichter in Watt |
| Nächstes günstiges Fenster | Zeitpunkt der nächsten günstigen Preisperiode |
| Nächstes teures Fenster | Zeitpunkt der nächsten teuren Preisperiode |
| Speicherplan | Tagesplan-Zusammenfassung mit vollständigem stündlichen Plan als Attribut |
| Geplante Aktion | Aktuelle Aktion dieser Stunde (Laden/Entladen/Solar/Halten/Inaktiv) |
| Erwartete Solarproduktion | Verbleibende erwartete Solarproduktion heute in kWh |

### Schalter

| Schalter | Beschreibung |
|----------|-------------|
| Automatik-Modus | Umschalten zwischen Automatik und Manuell |
| Zwangsladen | Manuelles Laden erzwingen |
| Zwangsentladen | Manuelles Entladen erzwingen |
| Netzladen erlauben | Laden aus dem Stromnetz erlauben/verbieten |
| Entladen erlauben | Batterieentladung erlauben/verbieten |
| Solarprognose nutzen | Solarbasierte Planung ein-/ausschalten |

Die drei **Runtime-Toggles** (Netzladen, Entladen, Solarprognose) können jederzeit geschaltet werden und wirken sofort auf die Planungslogik.

### Zahlenwerte (Slider)

| Slider | Bereich | Schrittweite |
|--------|---------|-------------|
| Min SOC | 0–50% | 5% |
| Max SOC | 50–100% | 5% |
| Preisschwelle niedrig | 0–50 ct/kWh | 1 ct |
| Preisschwelle hoch | 0–100 ct/kWh | 1 ct |

## Eingebaute Dashboard-Cards

Die Integration liefert zwei Custom Lovelace Cards mit, die **automatisch geladen** werden (kein manuelles Hinzufügen von Ressourcen oder HACS-Frontend-Plugins nötig). Die Cards erscheinen im Card-Picker und unterstützen den visuellen Editor.

### Battery Plan Card

Visualisiert den stündlichen Speicherplan als farbcodiertes Balkendiagramm:

```yaml
type: custom:battery-plan-card
entity: sensor.battery_storage_manager_speicherplan
title: Speicherplan
show_legend: true    # optional, Standard: true
show_solar: true     # optional, Standard: true
```

**Funktionen:**
- Farbige Balken pro Stunde nach Aktionstyp (Grün = Laden, Orange = Entladen, Gold = Solar, Blau = Halten, Grau = Idle)
- Preisachse links in ct/kWh
- Solarproduktion als goldene Linie im Overlay
- Aktuelle Stunde hervorgehoben mit blauem Rahmen und Jetzt-Marker
- Legende mit Stundenzählung pro Aktionstyp
- Aufklappbare Detailtabelle (Zeit, Preis, Solar, erwarteter SOC, Aktion, Grund)
- Tooltip mit Details bei Hover auf Balken

### Battery Status Card

Kompakte Statusübersicht mit SOC-Ring und Live-Daten:

```yaml
type: custom:battery-status-card
entity: sensor.battery_storage_manager_betriebsmodus
title: Batteriespeicher
toggle_entities:                                                    # optional
  - switch.battery_storage_manager_netzladen_erlauben
  - switch.battery_storage_manager_entladen_erlauben
  - switch.battery_storage_manager_solarprognose_nutzen
```

**Funktionen:**
- SOC als animierter Ringindikator (Grün > 60%, Orange > 30%, Rot darunter)
- Aktueller Strompreis in ct/kWh
- Betriebsmodus mit farbigem Icon
- Netzbezug/-einspeisung mit Richtung und Wattzahl
- Wechselrichter-Leistung (wenn aktiv)
- Strategie-Badge (Preisoptimiert / Eigenverbrauch / Manuell)
- Integrierte Toggle-Switches direkt in der Card

## Services

| Service | Beschreibung |
|---------|-------------|
| `battery_storage_manager.set_strategy` | Strategie wechseln (`price_optimized` / `self_consumption` / `manual`) |
| `battery_storage_manager.force_charge` | Laden erzwingen (wechselt zu Manuell) |
| `battery_storage_manager.force_discharge` | Entladen erzwingen (wechselt zu Manuell) |
| `battery_storage_manager.stop` | Alle Lade-/Entladevorgänge stoppen |

## Funktionsweise

### Planungszyklus

Der Battery Storage Manager analysiert alle 30 Sekunden die Situation und erstellt einen stündlichen Aktionsplan:

1. **Solarprognose lesen** – Alle konfigurierten Solar-Sensoren werden gelesen und pro Stunde aufsummiert
2. **Solarüberschuss berechnen** – Erwarteter Solarertrag minus Hausverbrauch pro Stunde
3. **Ladebedarf ermitteln** – Wie viele Stunden Netzladen nötig, um die Batterie zu füllen (nach Abzug von Solar)
4. **Entladekapazität berechnen** – Wie viele Stunden Entladung möglich basierend auf aktuellem SOC
5. **Aktionen zuweisen:**
   - **Solar-Laden** – Stunden mit Solarüberschuss (> 50 Wh nach Hausverbrauch)
   - **Netzladen** – Günstigste Stunden ohne nennenswerte Solarproduktion
   - **Entladen** – Teuerste Stunden (über Durchschnittspreis)
   - **Halten** – Ladung für kommende teure Stunden aufbewahren (keine Entladung)
   - **Inaktiv** – Keine Aktion nötig

### Strategien

| Strategie | Verhalten |
|-----------|-----------|
| **Preisoptimiert** | Folgt dem berechneten Speicherplan basierend auf Preisen und Solar |
| **Eigenverbrauch** | Entlädt bei Netzbezug, lädt bei Überschuss – unabhängig vom Preis |
| **Manuell** | Keine automatische Steuerung, nur manuelle Aktionen über Schalter/Services |

### Zero-Feed-Regelung

Wenn ein Wechselrichter-Power-Entity konfiguriert ist, regelt der Manager die Ausgangsleistung automatisch so, dass der Netzbezug gegen null geht, ohne Strom ins Netz einzuspeisen. Die Regelung passt sich alle 30 Sekunden an den aktuellen Netzbezug an.

### Runtime-Toggles

Die drei Laufzeit-Schalter beeinflussen die Planausführung sofort:

| Toggle | Wenn AUS |
|--------|----------|
| Netzladen erlauben | Plan-Aktionen "charge" werden übersprungen (idle stattdessen) |
| Entladen erlauben | Plan-Aktionen "discharge" werden übersprungen |
| Solarprognose nutzen | Solarprognosen werden nicht gelesen, Plan basiert nur auf Preisen |

## Weitere Dashboard-Beispiele

Im Ordner `examples/dashboard.yaml` finden sich zusätzliche Lovelace-Card-Konfigurationen:

- Entities-Card mit allen Sensoren
- Entities-Card mit allen Schaltern und Slidern
- Markdown-Card mit farbiger Plantabelle (Jinja2-Template)
- ApexCharts-Card mit Preisdiagramm (benötigt `apexcharts-card` via HACS)
- ApexCharts-Card mit Aktions-Timeline

## Lizenz

MIT License – siehe [LICENSE](LICENSE) für Details.

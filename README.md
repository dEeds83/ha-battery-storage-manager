# Battery Storage Manager

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/dEeds83/ha-battery-storage-manager)

Eine Home Assistant Custom Integration zur intelligenten Steuerung von Batteriespeichern basierend auf Strompreisen von Tibber.

## Features

- **Preisoptimierte Steuerung** – Automatisches Laden bei günstigen Strompreisen und Entladen bei teuren Preisen
- **Eigenverbrauchsoptimierung** – Batterieentladung zur Deckung des Hausverbrauchs, Laden bei Solarüberschuss
- **Manueller Modus** – Volle manuelle Kontrolle über Laden und Entladen
- **Dynamische Preisschwellen** – Automatische Ermittlung günstiger und teurer Zeitfenster anhand der 24-48h Preisprognose
- **Dual-Charger-Unterstützung** – Steuerung von zwei Ladegeräten und einem Einspeise-Wechselrichter
- **SOC-Management** – Konfigurierbare Min/Max-Schwellwerte für den Ladezustand
- **Mehrsprachig** – Vollständige Unterstützung für Deutsch und Englisch

## Voraussetzungen

- Home Assistant 2024.1.0 oder neuer
- [Tibber Integration](https://www.home-assistant.io/integrations/tibber/) mit Pulse für Echtzeit-Verbrauchsdaten
- Schaltbare Ladegeräte und Wechselrichter (über Home Assistant Switches steuerbar)

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

### Schritt 1: Tibber

| Parameter | Beschreibung |
|-----------|-------------|
| Tibber Preis-Sensor | Aktueller Strompreis (z.B. `sensor.electricity_price`) |
| Tibber Preisprognose | Preisprognose mit today/tomorrow Attributen |
| Verbrauch (Pulse) | Aktueller Netzbezug in Watt |
| Einspeisung (Pulse) | Aktuelle Netzeinspeisung in Watt |

### Schritt 2: Geräte

| Parameter | Beschreibung | Standard |
|-----------|-------------|----------|
| Ladegerät 1 Switch | Schalter für erstes Ladegerät | – |
| Ladegerät 1 Leistung | Nennleistung in Watt | 800 W |
| Ladegerät 2 Switch | Schalter für zweites Ladegerät | – |
| Ladegerät 2 Leistung | Nennleistung in Watt | 800 W |
| Einspeise-Wechselrichter Switch | Schalter für Feed-Inverter | – |
| Einspeise-Wechselrichter Leistung | Nennleistung in Watt | 800 W |

### Schritt 3: Batterie

| Parameter | Beschreibung | Standard |
|-----------|-------------|----------|
| SOC-Sensor | Ladezustand der Batterie (%) | – |
| Kapazität | Batteriekapazität in kWh | 5.0 kWh |
| Min SOC | Minimaler Ladezustand | 10% |
| Max SOC | Maximaler Ladezustand | 95% |
| Preisschwelle niedrig | Laden unter diesem Preis | 15 ct/kWh |
| Preisschwelle hoch | Entladen über diesem Preis | 30 ct/kWh |

## Entitäten

### Sensoren

| Sensor | Beschreibung |
|--------|-------------|
| Betriebsmodus | Aktueller Modus: Leerlauf / Laden / Entladen |
| Strategie | Aktive Strategie: Preisoptimiert / Eigenverbrauch / Manuell |
| Aktueller Strompreis | Strompreis in EUR/kWh mit günstigen/teuren Stunden als Attribute |
| Batterie SOC | Ladezustand in Prozent |
| Netzleistung | Aktueller Netzbezug/-einspeisung in Watt |
| Ladegerät 1 Status | Aktiv / Inaktiv |
| Ladegerät 2 Status | Aktiv / Inaktiv |
| Wechselrichter Status | Aktiv / Inaktiv |
| Nächstes günstiges Fenster | Zeitpunkt der nächsten günstigen Preisperiode |
| Nächstes teures Fenster | Zeitpunkt der nächsten teuren Preisperiode |

### Schalter

| Schalter | Beschreibung |
|----------|-------------|
| Automatikmodus | Umschalten zwischen Automatik und Manuell |
| Laden erzwingen | Manuelles Laden erzwingen |
| Entladen erzwingen | Manuelles Entladen erzwingen |

### Zahlenwerte (Slider)

| Slider | Bereich | Schrittweite |
|--------|---------|-------------|
| Min SOC | 0–50% | 5% |
| Max SOC | 50–100% | 5% |
| Preisschwelle niedrig | 0–50 ct/kWh | 1 ct |
| Preisschwelle hoch | 0–100 ct/kWh | 1 ct |

## Services

| Service | Beschreibung |
|---------|-------------|
| `battery_storage_manager.set_strategy` | Strategie wechseln (price_optimized / self_consumption / manual) |
| `battery_storage_manager.force_charge` | Laden erzwingen (wechselt zu Manuell) |
| `battery_storage_manager.force_discharge` | Entladen erzwingen (wechselt zu Manuell) |
| `battery_storage_manager.stop` | Alle Lade-/Entladevorgänge stoppen |

## Funktionsweise

Der Battery Storage Manager analysiert kontinuierlich (alle 30 Sekunden) die Strompreise und den Batteriezustand:

1. **Preisanalyse**: Die verfügbaren Stundenpreise (heute + morgen) werden in Drittel aufgeteilt – das untere Drittel gilt als günstig, das obere als teuer
2. **Ladeentscheidung**: Bei günstigen Preisen und SOC < Max wird geladen
3. **Entladeentscheidung**: Bei teuren Preisen und SOC > Min wird entladen, sofern in den nächsten 3 Stunden teure Preise erwartet werden
4. **Eigenverbrauch**: Im Eigenverbrauchs-Modus wird bei Netzbezug aus der Batterie entladen und bei Überschuss geladen

## Lizenz

MIT License – siehe [LICENSE](LICENSE) für Details.

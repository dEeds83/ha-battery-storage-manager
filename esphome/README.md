# Battery Storage Manager – ePaper Dashboard

ESPHome-Konfiguration für das **Seeed Studio XIAO 7.5" ePaper Panel** (800x480, ESP32-C3).

Zeigt den aktuellen Batteriestatus als kompaktes, energiesparendes Dashboard an.

## Display-Layout

```
┌─────────────────────────────────────────────────────────┐
│ Batteriespeicher                            14:30       │
│                                          21.03.2026     │
├──────────┬──────────────────────────────────────────────┤
│          │ Strompreis │ Netz        │ Solar heute       │
│   72%    │ 24.8 ct    │ -608 W      │ 18.2 kWh         │
│  Solar   ├────────────┴─────────────┴───────────────────┤
│          │ Speicherplan                                  │
│          │ 2h Laden (1.3 kWh) | 2h Entladen            │
│          ├──────────────┬──────────────┬────────────────┤
│          │ Aktion       │ Verbrauch    │ Wechselrichter │
│          │ Laden (Solar)│ 400 W        │ 0 W            │
├──────────┴──────────────┴──────────────┴────────────────┤
│ 24h Preisverlauf                                        │
│ ┃                                                       │
│ ┃   ████ günstig        ░░░░ teuer                      │
│ ┃                                                       │
│ 14  17  20  23  02  05  08  11          Preisoptimiert  │
└─────────────────────────────────────────────────────────┘
```

## Angezeigte Werte

| Bereich | Wert |
|---------|------|
| SOC-Ring | Batterieladestand mit Prozentanzeige |
| Betriebsmodus | Idle / Netz / Solar / Entladen |
| Strompreis | Aktueller Preis in ct/kWh |
| Netzleistung | Bezug (+) oder Einspeisung (-) in Watt |
| Solar heute | Erwartete Solarproduktion in kWh |
| Speicherplan | Zusammenfassung (Laden/Entladen Stunden) |
| Geplante Aktion | Aktuelle Plan-Aktion |
| Verbrauchsprognose | Vorhergesagter Hausverbrauch in Watt |
| Wechselrichter | Aktuelle Leistung |
| 24h Preisverlauf | Zeitstrahl mit günstig/teuer Markierungen |
| Strategie | Preisoptimiert / Eigenverbrauch / Manuell |

## Installation

### Voraussetzungen

- [ESPHome](https://esphome.io/) installiert (als HA Add-on oder standalone)
- Seeed Studio XIAO 7.5" ePaper Panel
- Battery Storage Manager Integration konfiguriert

### Setup

1. Kopiere `battery-epaper.yaml` in dein ESPHome-Konfigurationsverzeichnis

2. Erstelle/ergänze `secrets.yaml`:
   ```yaml
   wifi_ssid: "DeinWLAN"
   wifi_password: "DeinPasswort"
   api_key: "dein-api-schluessel"
   ota_password: "dein-ota-passwort"
   ```

3. **Entity-IDs anpassen** – Die Sensor-IDs in der YAML müssen zu deiner Installation passen. Prüfe die korrekten IDs unter **Einstellungen → Geräte & Dienste → Battery Storage Manager**.

4. Flashen:
   ```bash
   esphome run battery-epaper.yaml
   ```

### Update-Intervall

Das Display aktualisiert sich standardmäßig **alle 60 Sekunden** und zusätzlich bei Änderungen von SOC oder Betriebsmodus. Für längere Akkulaufzeit kann `update_interval` erhöht werden (z.B. `300s` für 5 Minuten).

## Anpassungen

### Eigene Entity-IDs

Die `entity_id` Werte unter `sensor:` und `text_sensor:` müssen an deine Installation angepasst werden. Die Standard-IDs folgen dem Muster:

```
sensor.battery_storage_manager_[sensor_name]
```

### Display-Rotation

Falls das Display gedreht werden soll:

```yaml
display:
  - platform: waveshare_epaper
    rotation: 180  # 0, 90, 180, 270
```

### Partial Refresh

Für schnellere Updates ohne Vollbild-Flackern, ändere das Modell:

```yaml
    model: 7.50inV2p  # p = partial refresh support
```

## Hardware

- **Display:** 7.5" ePaper, 800x480 Pixel, monochrom (schwarz/weiß)
- **Controller:** UC8179
- **MCU:** XIAO ESP32-C3 (RISC-V, WiFi, BLE)
- **Akkulaufzeit:** ~3 Monate bei 6h Refresh-Intervall (mit 2000mAh Akku)

## Hinweise

- Der `busy_pin` muss `inverted: true` sein – sonst kann das Display beschädigt werden
- ePaper behält das Bild auch ohne Strom
- Bei erstmaliger Verwendung kann das Display einige Sekunden zum Aufbau brauchen

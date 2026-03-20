# CLAUDE.md

## Workflow

- Nach jedem `git tag` + `git push --tags` immer auch ein GitHub Release mit `gh release create` erstellen
- Release Notes auf Deutsch, mit Zusammenfassung der Änderungen seit dem letzten Tag
- Bei jedem neuen Release **alle Versionsnummern** aktualisieren:
  - `manifest.json` → `"version": "x.y.z"`
  - `__init__.py` → `?v=x.y.z` (Frontend Cache-Busting)
  - `README.md` → Version-Badge `version-x.y.z-blue`

## Projekt

- Home Assistant Custom Integration (Python + JS)
- AC-gekoppeltes Batteriespeicher-System: Solarüberschuss geht übers Hausnetz und braucht die Ladegeräte, um in die Batterie zu kommen
- Übersetzungsdateien: strings.json (Basis), translations/en.json, translations/de.json müssen synchron gehalten werden
- Frontend: Custom Lovelace Cards in `frontend/` (battery-plan-card.js, battery-status-card.js)

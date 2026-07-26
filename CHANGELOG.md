# Changelog

## [1.0.0] – 2026
### Hinzugefügt
- Kontinuierliches Reachability-Monitoring (alle 5s)
- Automatische Ausfallklassifikation mit Confidence-Score
- FritzBox TR-064-Integration (Sync-Rate, SNR, Dämpfung, Event-Log)
- Selbst-Monitoring (CPU/RAM/Temperatur/Zykluszeit)
- Tamper-evident SQLite mit WAL-Modus
- Provider-Nachweisexport: PDF + CSV (3-Schichten-Trennung)
- Systemd-Service mit install.sh
- Optionale Telegram/E-Mail-Benachrichtigungen

## [Unreleased]
### Behoben
- DB-Aufblähung: `measurements`-Tabelle wird jetzt nach `measurement_retention_days` (Standard 30) gekürzt; VACUUM läuft danach
- Event-Rauschen: Paketverlust-/Latenz-Events erst ab `min_affected_targets` (Standard 2) gleichzeitig betroffenen Zielen
- Monats-Report wird durch `reports.auto_generate`/`generate_time` nun tatsächlich automatisch erzeugt (vorher ignoriert)

### Hinzugefügt
- Provider-Nachweis überarbeitet: Methodik-Abschnitt, Vertragsbewertung nach anerkannten Kriterien (Bundesnetzagentur Vfg 99/2021: Maximum/Normal/Minimum), Verfügbarkeits-/Ausfall-Abschnitt mit Zeitstempeln, Schweizer 80%-Praxis (ombudscom), Rechtshinweis; neue Ausfall-CSV; Tabellen brechen sauber um
- Config: contract_normal/min_download_mbps + _upload_mbps (drei Vertragswerte)
- Docker-Support: `Dockerfile`, `docker-compose.yml` (Host-Netzwerk), `.dockerignore`, `DEPLOY-DOCKER.md`
- Config: `database.measurement_retention_days`, `thresholds.min_affected_targets`

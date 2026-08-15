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
- Öffentliche DNS-Checks (`targets.public_domains`) nutzten ohne explizite `nameserver`-Angabe den System-Resolver — läuft NetWatch auf demselben Host wie ein lokaler Resolver (AdGuard/Pi-hole), liefen diese Checks faktisch durch ihn statt unabhängig zu sein. Anbieter-Ausfallerkennung selbst war davon nie betroffen (immer reine IP-ICMP-Checks), aber DNS-Fehler-Events waren es potenziell schon
- Beispiel-"Local DNS"-Ziel (`host: 127.0.0.1`, Typ `dns`) testete strukturell nie den lokalen Resolver: die Zeichenkette "127.0.0.1" wurde als aufzulösender Hostname behandelt und lieferte immer NXDOMAIN, unabhängig vom tatsächlichen Resolver-Status — das Ziel stand dauerhaft auf "down"

### Hinzugefügt
- Provider-Nachweis überarbeitet: Methodik-Abschnitt, Vertragsbewertung nach anerkannten Kriterien (Bundesnetzagentur Vfg 99/2021: Maximum/Normal/Minimum), Verfügbarkeits-/Ausfall-Abschnitt mit Zeitstempeln, Schweizer 80%-Praxis (ombudscom), Rechtshinweis; neue Ausfall-CSV; Tabellen brechen sauber um
- Config: contract_normal/min_download_mbps + _upload_mbps (drei Vertragswerte)
- Docker-Support: `Dockerfile`, `docker-compose.yml` (Host-Netzwerk), `.dockerignore`, `DEPLOY-DOCKER.md`
- Config: `database.measurement_retention_days`, `thresholds.min_affected_targets`
- Config: `targets.*.nameserver` — DNS-Ziele können jetzt einen festen externen Nameserver direkt per IP abfragen, statt den System-/lokalen Resolver zu nutzen. Beispiel-Config fragt die vier öffentlichen Domain-Checks über drei verschiedene Betreiber (1.1.1.1/8.8.8.8/9.9.9.9) ab und führt den lokalen Resolver (AdGuard) als eigenes, separat ausgewertetes Diagnose-Ziel, das nie in ISP-/DNS-Ausfallwertung einfließt
- Provider-Nachweis, Kapitel 8: neue "Ausschluss eigener Ursachen"-Tabelle (Heimnetz, lokaler DNS-Resolver, andere Docker-Container/Dienste, WLAN, Router-Neustart, Eigennutzung) plus quantifizierte Zahlen zur Erreichbarkeit des lokalen Resolvers im Messzeitraum
- `Database.get_target_reachability()` — aggregierte Erreichbarkeits-Statistik für ein einzelnes Ziel über einen Zeitraum (eine Query statt aller Einzelmessungen)

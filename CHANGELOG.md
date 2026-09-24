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
### Behoben (2026-09-24)
- **IP-Wechsel falsch gezählt:** Scheiterte die Abfrage der öffentlichen IP (z. B. während eines Ausfalls), wurde „keine IP" als Wechsel gespeichert und die Rückkehr als zweiter. Der Monatsbericht zeigte dadurch ~169 „IP-Wechsel", tatsächlich waren es 15. Gescheiterte Abfragen gelten jetzt als „unbekannt" (letzte bekannte Adresse bleibt), Bericht und Dashboard zählen nur echte Wechsel zwischen bekannten Adressen – auch für bereits gespeicherte Altdaten, die unverändert bleiben. Der Bericht betrachtet jetzt wirklich nur den Berichtsmonat (vorher: die letzten 200 Einträge)
- **Messlücken durch die IP-Abfrage:** Sie lief synchron in der Messschleife (3 Anbieter × 10 s Timeout) – ein hakender Abfragedienst hielt die Messung bis ~60 s an, ausgerechnet bei Netzproblemen. Läuft jetzt im Hintergrund (Timeout 5 s)
- `vacuum_interval_days` wurde ignoriert, VACUUM lief täglich (schreibt die ganze ~1-GB-Datei neu und blockiert währenddessen). Jetzt wie konfiguriert, Merker in `database/.last_vacuum`
- traceroute/mtr: bei Fehlschlag stand im Log nur „failed: None" – jetzt mit Exit-Code und Fehlermeldung

### Geändert (2026-09-24)
- Telegram: neue Filter `only_closed`, `event_types`, `min_duration_seconds` und eine kurze deutsche Meldung („✅ Internet wieder da – war 5 min 35 s weg · 22:11–22:17 Uhr · Ursache: Leitung/Provider"). Versand im Hintergrund mit Wiederholungen (direkt nach einem Ausfall hakt DNS oft noch). Token/Chat-ID auch aus der Umgebung (`NETWATCH_TELEGRAM_BOT_TOKEN`/`BOT_TOKEN`, `…_CHAT_ID`/`CHAT_ID`)
- Docker: `NET_ADMIN` entfernt (nicht nötig, NetWatch liest nur die Routing-Tabelle); `mem_limit` und Healthcheck jetzt im Repo statt nur lokal; Hostspezifisches gehört in `docker-compose.override.yml` (git-ignoriert)
- DB-Migration v11: Index `idx_measurements_target` entfernt – alle Ziel-Abfragen filtern auch nach Zeit, das deckt der Zeit-Index ab. An einer Kopie der echten DB gemessen: Abfragen 0,005–0,016 s → 0,04 s, Datei ~256 MB (−24 %) kleiner (nach dem nächsten VACUUM)
- Beispielkonfiguration: Speedtest alle 30 statt 15 Minuten (≈ 0,5 statt 1 GB Datenvolumen pro Tag)

### Behoben
- Dashboard-Übersicht & Monats-Rollup zählten Ausfälle des **laufenden Tages** nicht: `daily_statistics` wurde nur einmal täglich *für gestern* aggregiert. Heutige Ereignisse (am Monatsersten der ganze Monat) fehlten dadurch in „Ausfälle gesamt", „ISP-Ausfälle (aktueller Monat)", der Verfügbarkeits-Anzeige und im Provider-Nachweis — bis Mitternacht. Der aktuelle Tag wird jetzt bei jedem Wartungs-Tick (~alle 8 min) und beim Start mitberechnet; laufende Ausfälle zählen dabei nur bis „jetzt" statt bis Tagesende als Ausfallzeit
- Übersicht: Ring-Label „30 Tage" zeigte tatsächlich die Verfügbarkeit des aktuellen Kalendermonats → jetzt „Monat"
- `/api/export/provider`: ungültiger `days`-Parameter (z. B. `?days=abc`) führte zu HTTP 500 statt eines Fallbacks — wird jetzt auf 1–365 begrenzt, unparsbare Werte fallen auf 14 zurück
- DB-Aufblähung: `measurements`-Tabelle wird jetzt nach `measurement_retention_days` (Standard 30) gekürzt; VACUUM läuft danach
- Event-Rauschen: Paketverlust-/Latenz-Events erst ab `min_affected_targets` (Standard 2) gleichzeitig betroffenen Zielen
- Monats-Report wird durch `reports.auto_generate`/`generate_time` nun tatsächlich automatisch erzeugt (vorher ignoriert)
- Öffentliche DNS-Checks (`targets.public_domains`) nutzten ohne explizite `nameserver`-Angabe den System-Resolver — läuft NetWatch auf demselben Host wie ein lokaler Resolver (AdGuard/Pi-hole), liefen diese Checks faktisch durch ihn statt unabhängig zu sein. Anbieter-Ausfallerkennung selbst war davon nie betroffen (immer reine IP-ICMP-Checks), aber DNS-Fehler-Events waren es potenziell schon
- Beispiel-"Local DNS"-Ziel (`host: 127.0.0.1`, Typ `dns`) testete strukturell nie den lokalen Resolver: die Zeichenkette "127.0.0.1" wurde als aufzulösender Hostname behandelt und lieferte immer NXDOMAIN, unabhängig vom tatsächlichen Resolver-Status — das Ziel stand dauerhaft auf "down"
- `NetworkMonitor.measure_all()`: die Zielreihenfolge-Sortierung am Ende der Methode unpackte Ziel-Tupel noch mit der alten 3er-Form (`(name, _, _)`), die neue `nameserver`-Erweiterung machte daraus 4er-Tupel — crashte in jedem Messzyklus (`ValueError: too many values to unpack`) bis zum Fix

### Geändert
- Provider-Nachweis: alle Bezüge auf Paragraphen/Gesetze/Regulatoren entfernt (u. a. „Bundesnetzagentur", „Vfg 99/2021", „amtliches Messverfahren", „Minderungs-/Sonderkündigungsrecht", „Rechtlicher Hinweis"). Die Prozentwerte (90 %, 80 %) sind jetzt neutral als **gebräuchliche Richtwerte** ausgewiesen; `networktest.ch`/`ombudscom` nur noch beiläufig im Verwendungshinweis
- Provider-Nachweis strukturell überarbeitet für bessere Lesbarkeit: Seitenzahlen in der Fusszeile, durchgehende Kapitelnummerierung 1–8 mit Trennlinie unter jeder Überschrift, „Inhalt/Aufbau"-Zeile unter dem Fazit, Überschriften brechen nicht mehr allein am Seitenende um, Methodik-Text (vorher vorne, dicht) als Unterabschnitte ans Ende (Kapitel 8) mit Stichpunkten

### Entfernt
- README.bl.md (Baseldütsch-Übersetzung) entfernt, inkl. Verweise in README.md/README.en.md

### Hinzugefügt
- Dashboard: Button **„Provider-Nachweis exportieren"** auf der Seite *ISP-Nachweise* — löst den Export (`/api/export/provider`) direkt aus, mit Zeitraum-Auswahl (7–90 Tage), Fortschrittsanzeige und Download-Links zu PDF + allen CSVs. Der Endpunkt existierte schon, hatte aber keine Bedienoberfläche
- Provider-Nachweis: **„Fazit"** ganz oben auf Seite 1 — farbcodierte Gesamtbewertung (rot/orange/grün) mit den konkreten Befunden (Vertragsabweichung, per Router-Log bestätigte Leitungsabrisse, wahrscheinliche Drosselung, Leitungsinstabilität) plus kompakte Kernbefund-Tabelle, damit die Kernaussage auch bei nur überflogenem Bericht sofort ins Auge fällt
- Provider-Nachweis überarbeitet: Methodik-Abschnitt, Vertragsvergleich nach den drei Vertragswerten (Maximum/normal verfügbar/Minimum) mit gebräuchlichen Richtwerten, Verfügbarkeits-/Ausfall-Abschnitt mit Zeitstempeln, ergänzender 80%-Richtwert, Verwendungshinweis; neue Ausfall-CSV; Tabellen brechen sauber um
- Config: contract_normal/min_download_mbps + _upload_mbps (drei Vertragswerte)
- Docker-Support: `Dockerfile`, `docker-compose.yml` (Host-Netzwerk), `.dockerignore`, `DEPLOY-DOCKER.md`
- Config: `database.measurement_retention_days`, `thresholds.min_affected_targets`
- Config: `targets.*.nameserver` — DNS-Ziele können jetzt einen festen externen Nameserver direkt per IP abfragen, statt den System-/lokalen Resolver zu nutzen. Beispiel-Config fragt die vier öffentlichen Domain-Checks über drei verschiedene Betreiber (1.1.1.1/8.8.8.8/9.9.9.9) ab und führt den lokalen Resolver (AdGuard) als eigenes, separat ausgewertetes Diagnose-Ziel, das nie in ISP-/DNS-Ausfallwertung einfließt
- Provider-Nachweis, Kapitel 8: neue "Ausschluss eigener Ursachen"-Tabelle (Heimnetz, lokaler DNS-Resolver, andere Docker-Container/Dienste, WLAN, Router-Neustart, Eigennutzung) plus quantifizierte Zahlen zur Erreichbarkeit des lokalen Resolvers im Messzeitraum
- `Database.get_target_reachability()` — aggregierte Erreichbarkeits-Statistik für ein einzelnes Ziel über einen Zeitraum (eine Query statt aller Einzelmessungen)
- Optionale Docker-Per-Container-Stats als Zusatzbeweis: Host-seitiges Snapshot-Skript (`netwatch-docker-stats.sh` + systemd-Timer, alle 10s) schreibt `docker stats` in eine Datei, die NetWatch nur liest (`src/docker_stats.py`) — bewusst ohne Docker-Socket im Container, da dessen Mount praktisch Root auf dem Host wäre. Wird bei jedem neu eröffneten Ereignis als Beweisdatei mitgeschrieben. Siehe DEPLOY-DOCKER.md.

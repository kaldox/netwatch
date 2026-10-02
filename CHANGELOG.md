# Changelog

Alle nennenswerten Änderungen an NetWatch, neueste Version zuerst.
Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach
[Semantic Versioning](https://semver.org/lang/de/). Die Versionen 1.1.0 bis 1.4.0 wurden
nachträglich aus der Git-Historie zugeordnet; das Datum ist jeweils das des letzten Commits
der Version.

| Version | Datum | Das Wichtigste |
|---|---|---|
| [1.6.2](#162--2026-10-02) | 2026-10-02 | Offene Ausfälle enden bei der ersten Messung mit Verbindung (1.6.1 schloss sie bei kurzen Neustarts zu spät) |
| [1.6.1](#161--2026-10-02) | 2026-10-02 | Ausfälle, die ein Neustart offen liess, werden beim Start geschlossen – keine falschen Ausfallzeiten bis Mitternacht mehr |
| [1.6.0](#160--2026-09-27) | 2026-09-27 | Provider-Nachweis korrigiert: Router-Belege aus dem Protokoll, nur der Messzeitraum, keine VPN-Meldungen |
| [1.5.0](#150--2026-09-24) | 2026-09-24 | IP-Wechsel korrekt gezählt, keine Messlücken mehr durch die IP-Abfrage, Telegram-Meldung nach Ausfall, schlankere Datenbank |
| [1.4.0](#140--2026-09-02) | 2026-09-02 | Export-Button im Dashboard, Fazit auf Seite 1 des Provider-Nachweises, Ausfälle des laufenden Tages werden mitgezählt |
| [1.3.0](#130--2026-08-15) | 2026-08-15 | DNS-Prüfungen unabhängig vom lokalen Resolver, Ausschluss eigener Ursachen im Nachweis, Docker-Statistik als Zusatzbeleg |
| [1.2.0](#120--2026-08-08) | 2026-08-08 | FritzBox-Protokoll als zentraler Beleg, keine Fehlalarme mehr durch einzelne verlorene Gateway-Pings |
| [1.1.0](#110--2026-07-26) | 2026-07-26 | Docker-Betrieb, Datenbank-Aufräumen, Vertragsvergleich mit drei Vertragswerten |
| [1.0.0](#100--2026-06-22) | 2026-06-22 | Erste Version |

## [Unreleased]

## [1.6.2] – 2026-10-02

### Behoben
- **1.6.1 schloss offene Ausfälle viel zu spät.** Als Ende galt die letzte Messung vor einer
  Messlücke von über 2 Minuten – ein Pi-Neustart hinterlässt aber nur rund eine Minute Lücke, also
  lief die Suche bis zur 12-Stunden-Grenze. Jetzt endet ein Ausfall bei der **ersten Messung, die
  wieder Verbindung zeigt** (lokaler Ausfall: Gateway erreichbar; Provider-Ausfall: externe IPs;
  DNS-Ausfall: DNS-Ziele). Nur für Ereignisse ohne solches Signal (Paketverlust, Latenz) gilt die
  Lücken-Regel, jetzt mit 30 Sekunden. Test mit dem Ablauf vom 01.10.2026 ergänzt.

## [1.6.1] – 2026-10-02

### Behoben
- **Ausfälle blieben nach einem Neustart für immer offen.** Offene Ereignisse hält der Classifier
  nur im Speicher; wurde NetWatch oder der Pi während eines Ausfalls neu gestartet, schloss sie
  niemand mehr. Die Tagesstatistik zählte sie bis Mitternacht (UTC) als Ausfallzeit – am
  01.10.2026 machten zwei lokale Ausfälle während eines Umbaus so aus wenigen Minuten 443 Minuten,
  und der Status stand dauerhaft auf „PROBLEM“. Beim Start werden solche Ereignisse jetzt mit der
  letzten Messung vor der Neustart-Lücke beendet (Vermerk `closed_after_restart` im Ereignis) und
  die Tagesstatistik der betroffenen Tage neu berechnet.

## [1.6.0] – 2026-09-27

### Behoben
- **Provider-Nachweis zählte FritzBox-Meldungen ohne Zeitraum.** Das Router-Protokoll wurde ohne
  Datumsfilter gelesen und bei 500 Einträgen abgeschnitten. „500 Trennungen“ oder „164
  Verkabelungs-Meldungen“ stammten dadurch aus mehreren Monaten. Jetzt zählt nur der Messzeitraum.
- **Router-Bestätigung der Ausfälle war falsch.** Sie beruhte auf dem per TR-064 abgefragten
  WAN-Status, der einem Abriss hinterherhinkt und im entscheidenden Moment noch „Connected“ meldet.
  Der Bericht wies dadurch z. B. „0 bestätigt“ bzw. „13 von 60“ aus, obwohl das Protokoll 60 von 62
  Ausfällen sekundengenau belegt („Keine DSL-Synchronisierung“ → „Internetverbindung wurde
  getrennt“). Bestätigt wird jetzt über das Ereignisprotokoll (±90 s um den Ausfall).
- VPN-Meldungen einzelner Geräte („WireGuard-Verbindung … wurde getrennt“) zählten als
  Leitungstrennung – im Bericht und bei der Einordnung neuer Protokolleinträge.
- Kapitel „Router-Protokoll“ trennt jetzt echte Trennungen („Internetverbindung wurde getrennt“,
  „Keine DSL-Synchronisierung“) von den Fehlermeldungen der Neueinwahl, statt alles als
  „Leitungsabrisse pro Tag“ zu summieren.
- Methodik nannte fest „Durchsatz alle 15 Minuten“ und „direkt am Router“; beides kommt jetzt aus
  der Konfiguration bzw. ist neutral formuliert („per Netzwerkkabel mit dem Router verbunden“).

### Geändert
- Das Kapitel zu den Verkabelungs-Hinweisen der FritzBox ist neutral formuliert (die Ursache lässt
  sich aus der Meldung allein nicht der Hausinstallation zuordnen) und über
  `reports.provider_show_cabling` abschaltbar; die folgenden Kapitel rücken dann nach.
- Die spekulative Deutung der SNR-Marge („konservativ konfigurierte Leitung“) ist entfernt.
- Die Router-CSV im Nachweis enthält nur noch den Messzeitraum und keine VPN-Meldungen.

### Hinzugefügt
- 7 Tests für den Provider-Nachweis (Zeitraum, VPN, Bestätigungsfenster, Kapitelnummern,
  kompletter Bericht mit und ohne Verkabelungs-Kapitel).

## [1.5.0] – 2026-09-24

### Behoben
- **IP-Wechsel falsch gezählt:** Scheiterte die Abfrage der öffentlichen IP (z. B. während
  eines Ausfalls), wurde „keine IP" als Wechsel gespeichert und die Rückkehr als zweiter. Der
  Monatsbericht zeigte dadurch ~169 „IP-Wechsel", tatsächlich waren es 15. Gescheiterte
  Abfragen gelten jetzt als „unbekannt" (die letzte bekannte Adresse bleibt). Bericht und
  Dashboard zählen nur echte Wechsel zwischen bekannten Adressen, auch bei bereits
  gespeicherten Altdaten (diese bleiben unverändert). Der Bericht betrachtet jetzt wirklich nur
  den Berichtsmonat statt der letzten 200 Einträge.
- **Messlücken durch die IP-Abfrage:** Sie lief synchron in der Messschleife (3 Anbieter × 10 s
  Timeout). Ein hakender Abfragedienst hielt die Messung bis ~60 s an, ausgerechnet bei
  Netzproblemen. Sie läuft jetzt im Hintergrund (Timeout 5 s).
- `vacuum_interval_days` wurde ignoriert, VACUUM lief täglich (schreibt die ganze ~1-GB-Datei
  neu und blockiert währenddessen). Jetzt wie konfiguriert, Merker in `database/.last_vacuum`.
- traceroute/mtr: Bei einem Fehlschlag stand im Log nur „failed: None", jetzt mit Exit-Code
  und Fehlermeldung.

### Geändert
- Telegram: neue Filter `only_closed`, `event_types`, `min_duration_seconds` und eine kurze
  deutsche Meldung („✅ Internet wieder da – war 5 min 35 s weg · 22:11–22:17 Uhr · Ursache:
  Leitung/Provider"). Versand im Hintergrund mit Wiederholungen, weil DNS direkt nach einem
  Ausfall oft noch hakt. Token und Chat-ID auch aus der Umgebung
  (`NETWATCH_TELEGRAM_BOT_TOKEN`/`BOT_TOKEN`, `…_CHAT_ID`/`CHAT_ID`).
- Docker: `NET_ADMIN` entfernt (nicht nötig, NetWatch liest nur die Routing-Tabelle).
  `mem_limit` und Healthcheck stehen jetzt im Repo. Hostspezifisches gehört in
  `docker-compose.override.yml` (git-ignoriert).
- Datenbank-Migration v11: Index `idx_measurements_target` entfernt. Alle Ziel-Abfragen filtern
  auch nach Zeit, das deckt der Zeit-Index ab. An einer Kopie der echten Datenbank gemessen:
  Abfragen 0,005–0,016 s → 0,04 s, Datei ~256 MB (−24 %) kleiner nach dem nächsten VACUUM.
- Beispielkonfiguration: Speedtest alle 30 statt 15 Minuten (≈ 0,5 statt 1 GB Datenvolumen pro
  Tag).

## [1.4.0] – 2026-09-02

### Hinzugefügt
- Dashboard: Button **„Provider-Nachweis exportieren"** auf der Seite *ISP-Nachweise*. Er löst
  den Export (`/api/export/provider`) direkt aus, mit Zeitraum-Auswahl (7–90 Tage),
  Fortschrittsanzeige und Download-Links zu PDF und allen CSVs. Den Endpunkt gab es schon,
  aber ohne Bedienoberfläche.
- Provider-Nachweis: **„Fazit"** ganz oben auf Seite 1. Farbcodierte Gesamtbewertung
  (rot/orange/grün) mit den konkreten Befunden (Vertragsabweichung, per Router-Log bestätigte
  Leitungsabrisse, wahrscheinliche Drosselung, Leitungsinstabilität) und einer kompakten
  Kernbefund-Tabelle.
- `SECURITY.md` und CI-Workflow (Tests auf Python 3.11 und 3.12 bei jedem Push).

### Geändert
- Provider-Nachweis: alle Bezüge auf Paragraphen, Gesetze und Regulatoren entfernt (u. a.
  „Bundesnetzagentur", „Vfg 99/2021", „amtliches Messverfahren",
  „Minderungs-/Sonderkündigungsrecht", „Rechtlicher Hinweis"). Die Prozentwerte (90 %, 80 %)
  sind jetzt neutral als **gebräuchliche Richtwerte** ausgewiesen.
- Provider-Nachweis: Hinweise zum weiteren Vorgehen (Reklamation, Schlichtungsstelle)
  entfernt. Der Bericht geht an die Anbieterin selbst und bleibt rein sachlich.
- Provider-Nachweis besser lesbar: Seitenzahlen in der Fußzeile, durchgehende
  Kapitelnummerierung 1–8 mit Trennlinie unter jeder Überschrift, „Aufbau"-Zeile unter dem
  Fazit, Überschriften brechen nicht mehr allein am Seitenende um, Methodik als Kapitel 8 mit
  Stichpunkten ans Ende verschoben.
- Dokumentation mit echten Umlauten statt ae/oe/ue.

### Behoben
- Dashboard-Übersicht und Monats-Rollup zählten Ausfälle des **laufenden Tages** nicht:
  `daily_statistics` wurde nur einmal täglich *für gestern* berechnet. Heutige Ereignisse (am
  Monatsersten der ganze Monat) fehlten dadurch bis Mitternacht in „Ausfälle gesamt",
  „ISP-Ausfälle (aktueller Monat)", der Verfügbarkeits-Anzeige und im Provider-Nachweis. Der
  aktuelle Tag wird jetzt bei jedem Wartungs-Tick (~alle 8 min) und beim Start mitberechnet;
  laufende Ausfälle zählen dabei nur bis „jetzt" statt bis Tagesende.
- Übersicht: Das Ring-Label „30 Tage" zeigte tatsächlich die Verfügbarkeit des aktuellen
  Kalendermonats, es heißt jetzt „Monat".
- `/api/export/provider`: Ein ungültiger `days`-Parameter (z. B. `?days=abc`) führte zu
  HTTP 500. Er wird jetzt auf 1–365 begrenzt, nicht lesbare Werte fallen auf 14 zurück.

## [1.3.0] – 2026-08-15

### Hinzugefügt
- Config: `targets.*.nameserver`. DNS-Ziele können einen festen externen Nameserver direkt per
  IP abfragen statt des System- oder lokalen Resolvers. Die Beispielkonfiguration fragt die
  vier öffentlichen Domain-Checks über drei Betreiber ab (1.1.1.1, 8.8.8.8, 9.9.9.9) und führt
  den lokalen Resolver (AdGuard) als eigenes Diagnose-Ziel, das nie in die ISP- oder
  DNS-Ausfallwertung einfließt.
- Provider-Nachweis, Kapitel 8: Tabelle „Ausschluss eigener Ursachen" (Heimnetz, lokaler
  DNS-Resolver, andere Docker-Container, WLAN, Router-Neustart, Eigennutzung) mit Zahlen zur
  Erreichbarkeit des lokalen Resolvers im Messzeitraum.
- `Database.get_target_reachability()`: Erreichbarkeits-Statistik für ein einzelnes Ziel über
  einen Zeitraum (eine Abfrage statt aller Einzelmessungen).
- Optionale Docker-Statistik je Container als Zusatzbeleg: Ein Skript auf dem Host
  (`netwatch-docker-stats.sh` + systemd-Timer, alle 10 s) schreibt `docker stats` in eine
  Datei, die NetWatch nur liest (`src/docker_stats.py`). Bewusst ohne Docker-Socket im
  Container, weil dessen Einbindung praktisch Root-Rechte auf dem Host bedeutet. Die Werte
  werden bei jedem neuen Ereignis als Beweisdatei mitgeschrieben, siehe `DEPLOY-DOCKER.md`.

### Behoben
- Öffentliche DNS-Checks (`targets.public_domains`) nutzten ohne `nameserver`-Angabe den
  System-Resolver. Läuft NetWatch auf demselben Host wie ein lokaler Resolver
  (AdGuard/Pi-hole), liefen diese Checks faktisch durch ihn statt unabhängig. Die
  Anbieter-Ausfallerkennung war davon nie betroffen (reine ICMP-Checks auf IP-Adressen), die
  DNS-Fehler-Ereignisse potenziell schon.
- Das Beispiel-Ziel „Local DNS" (`host: 127.0.0.1`, Typ `dns`) prüfte nie den lokalen
  Resolver: „127.0.0.1" wurde als aufzulösender Name behandelt und lieferte immer NXDOMAIN,
  das Ziel stand dauerhaft auf „down".
- `NetworkMonitor.measure_all()` stürzte nach der `nameserver`-Erweiterung in jedem Messzyklus
  ab (`ValueError: too many values to unpack`), am selben Tag behoben.

### Entfernt
- `README.bl.md` (Baseldütsch-Übersetzung) samt Verweisen in `README.md` und `README.en.md`.

## [1.2.0] – 2026-08-08

### Hinzugefügt
- Provider-Nachweis: Das Ereignisprotokoll der FritzBox ist jetzt der zentrale, vom Messgerät
  unabhängige Beleg. Eigenes Kapitel mit allen Zwangstrennungen und Neuverbindungen im
  Zeitraum (Router-Meldung wörtlich).
- Provider-Nachweis: Jeder Anbieter-Ausfall wird mit dem WAN-Status der FritzBox im selben
  Moment abgeglichen und als „Leitungsabriss bestätigt" oder „Providernetz (Leitung lief)"
  eingeordnet.
- Provider-Nachweis: Abschnitt zur Abgrenzung der Ursachen. Anbieter-Ausfälle werden per ICMP
  an feste öffentliche IP-Adressen erkannt, ohne Namensauflösung; Durchsatz und Latenz gelten
  nur als ergänzender Beleg, weil Eigennutzung sie beeinflussen kann.

### Geändert
- Beispielkonfiguration: Ping- und DNS-Timeout 2 statt 5 s, damit nicht erreichbare Ziele den
  Zyklus nicht blockieren.

### Behoben
- Ein einzelner verlorener Gateway-Ping löste fälschlich `LOCAL_NETWORK_FAILURE` aus. Ein
  lokaler Ausfall zählt jetzt erst nach `failure_threshold` aufeinanderfolgenden Zyklen, wie
  bei der ISP-Erkennung; der Gateway wird mit 3 statt 1 Paket geprüft.
- Pings senden im Abstand von 0,2 s statt 1 s. Ein Ping mit 5 Paketen dauert so ~1 s statt ~4 s,
  der Messzyklus bleibt unter 5 s und erzeugt keine Fehlalarme mehr durch Überlauf.

## [1.1.0] – 2026-07-26

### Hinzugefügt
- Docker-Betrieb: `Dockerfile`, `docker-compose.yml` (Host-Netzwerk), `.dockerignore`,
  `DEPLOY-DOCKER.md`.
- Provider-Nachweis überarbeitet: Methodik-Abschnitt, Vertragsvergleich nach den drei
  Vertragswerten (Maximum, normalerweise verfügbar, Minimum), Verfügbarkeits- und
  Ausfall-Abschnitt mit Zeitstempeln, neue Ausfall-CSV; Tabellen brechen sauber um.
- Config: `contract_normal_*` und `contract_min_*` für Download und Upload (drei
  Vertragswerte), `database.measurement_retention_days`, `thresholds.min_affected_targets`.

### Geändert
- README auf Deutsch als Hauptsprache, englische Fassung als `README.en.md`.

### Behoben
- Datenbank wuchs unbegrenzt: Die Tabelle `measurements` wird jetzt nach
  `measurement_retention_days` (Standard 30) gekürzt, danach läuft VACUUM.
- Zu viele Ereignisse: Paketverlust- und Latenz-Ereignisse erst, wenn mindestens
  `min_affected_targets` (Standard 2) Ziele gleichzeitig betroffen sind.
- Der Monatsbericht wird über `reports.auto_generate`/`generate_time` jetzt wirklich
  automatisch erzeugt (die Einstellung wurde vorher ignoriert).

## [1.0.0] – 2026-06-22

### Hinzugefügt
- Kontinuierliche Erreichbarkeitsmessung (alle 5 s)
- Automatische Ausfall-Einordnung mit Confidence-Score
- FritzBox-TR-064-Anbindung (Sync-Rate, SNR, Dämpfung, Ereignisprotokoll)
- Selbstüberwachung (CPU, RAM, Temperatur, Zykluszeit)
- Manipulationserkennbare SQLite-Datenbank im WAL-Modus
- Provider-Nachweis als PDF und CSV, getrennt nach Hausverkabelung, DSL-Leitung und
  Anbieternetz
- systemd-Dienst mit `install.sh`
- Optionale Benachrichtigung per Telegram oder E-Mail

[Unreleased]: https://github.com/kaldox/netwatch/compare/v1.6.2...HEAD
[1.6.2]: https://github.com/kaldox/netwatch/compare/v1.6.1...v1.6.2
[1.6.1]: https://github.com/kaldox/netwatch/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/kaldox/netwatch/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/kaldox/netwatch/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/kaldox/netwatch/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/kaldox/netwatch/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/kaldox/netwatch/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/kaldox/netwatch/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/kaldox/netwatch/releases/tag/v1.0.0

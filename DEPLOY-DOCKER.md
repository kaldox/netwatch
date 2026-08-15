# NetWatch auf Docker umstellen (Daten behalten)

Diese Anleitung migriert die bestehende **systemd**-Installation unter
`/opt/netwatch` auf **Docker**, ohne die Verlaufsdaten zu verlieren.
Danach erscheint NetWatch als Container in Portainer.

> Alle Befehle laufen per SSH auf dem Pi.

## 1. Alten Dienst stoppen
```bash
sudo systemctl disable --now netwatch
```

## 2. Aktualisierten Code holen
Nachdem die Fixes zu GitHub gepusht sind:
```bash
cd /opt
sudo git clone https://github.com/kaldox/netwatch.git netwatch-docker
cd netwatch-docker
```

## 3. Bestehende Daten + Konfiguration übernehmen
```bash
sudo cp /opt/netwatch/config/config.yaml  config/
sudo cp -r /opt/netwatch/database/.        database/
sudo cp -r /opt/netwatch/data/.            data/
sudo cp -r /opt/netwatch/reports/.         reports/   2>/dev/null || true
```

## 4. Container bauen und starten
```bash
sudo docker compose up -d --build
```
Der erste Build dauert auf dem Pi ein paar Minuten.

## 5. Prüfen
```bash
sudo docker compose ps
sudo docker compose logs -f --tail=40    # Strg+C zum Beenden
```
Dashboard: http://192.168.178.119:8080 — und der Container **netwatch**
taucht jetzt in Portainer auf.

## 6. Wenn alles läuft: alte Installation entfernen (optional)
```bash
# Erst sicher sein, dass Docker läuft! Dann:
sudo rm -rf /opt/netwatch          # alte systemd-Kopie
sudo rm /etc/systemd/system/netwatch.service
sudo systemctl daemon-reload
```

## Zurück zu systemd (Notfall-Rollback)
```bash
cd /opt/netwatch-docker && sudo docker compose down
sudo systemctl enable --now netwatch
```

## Datenbank-Pflege
Die Retention ist jetzt **im Code** (Standard: 30 Tage Rohdaten, siehe
`database.measurement_retention_days` in der config). Der separate
`netwatch-prune`-systemd-Timer wird dann **nicht** mehr gebraucht.

## Optional: Per-Container Docker-Stats als Zusatzbeweis

NetWatch selbst rührt den Docker-Socket nie an — ihn (auch `:ro`) in den
Container zu mounten wäre praktisch Root auf dem ganzen Host, das Mount-Flag
schützt nur die Socket-*Datei*, nicht die Docker-API dahinter. Stattdessen
schreibt ein kleines Skript **auf dem Host** (außerhalb jedes Containers)
periodisch `docker stats` in eine Datei, die NetWatch über sein ohnehin
gemountetes `data/`-Verzeichnis nur liest. Damit lässt sich zu jedem Ereignis
zusätzlich zur host-weiten CPU/RAM-Erfassung auch belegen, dass kein anderer
Container (AdGuard, n8n, was auch immer) gerade die Last verursacht hat.

```bash
sudo cp netwatch-docker-stats.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/netwatch-docker-stats.sh
sudo cp netwatch-docker-stats.service netwatch-docker-stats.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now netwatch-docker-stats.timer
```

Läuft alle 10s, schreibt nach `data/docker_stats_snapshot.txt`. NetWatch
hängt den Inhalt bei jedem neu eröffneten Ereignis als Beweisdatei an
(`data/evidence/<event_id>/*.docker_stats.txt`), sofern die Datei nicht
älter als 60s ist. Kein Effekt, wenn der Timer nicht läuft — rein additiv.

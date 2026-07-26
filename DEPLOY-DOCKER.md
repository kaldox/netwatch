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

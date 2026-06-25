# NetWatch

**🇩🇪 Deutsch** · [🇬🇧 English](README.en.md) · [🐻 Baseldütsch](README.bl.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue)

**Durchgehende Überwachung der Internet-Qualität, die festnagelt, *wo* dein Verbindungsproblem wirklich liegt — deine Verkabelung, deine Leitung oder dein Provider.**

NetWatch läuft 24/7 auf einem Raspberry Pi (oder jeder Linux-Kiste, die durchläuft) und beantwortet die Fragen, die ein einmaliger Speedtest nicht beantworten kann:

- Ist mein Internet *tatsächlich* so langsam wie es sich anfühlt — und zu welchen Tageszeiten?
- Wenn die Verbindung abbricht: ist das **mein** Problem oder das vom **Provider**?
- Krieg ich die Geschwindigkeit, für die ich zahle — und wenn nicht, *warum* nicht?

Es misst dafür durchgehend, klassifiziert Ausfälle automatisch, liest die Sicht des Routers auf die Leitung aus und erzeugt Nachweise, die du deinem Provider vorlegen kannst.

> **Hinweis:** NetWatch wurde ursprünglich rund um die **AVM FritzBox** gebaut (im deutschsprachigen Raum sehr verbreitet), die als einzige heute voll unterstützt wird. Andere Router laufen in einem eingeschränkten, experimentellen Modus — siehe [Router-Unterstützung](#router-unterstützung).

---

## Warum es das gibt

Ein normaler Speedtest sagt dir „du hast jetzt gerade 33 Mbit/s." Er sagt dir **nicht**:

- ob das der Provider ist, der drosselt, eine schlechte Leitung, oder ein Problem bei dir zuhause;
- ob das Gerät, von dem du testest, selbst der Flaschenhals ist;
- wie deine Verbindung letzten Dienstag um 20 Uhr aussah.

NetWatch trennt das Problem in **drei Schichten**, damit die Ursache eindeutig ist:

| Schicht | Was geprüft wird | Beispiel-Befund |
|---------|------------------|-----------------|
| **1. Hausverkabelung** | Die Verkabelungsfehler-Erkennung des Routers | *„Eine Spleißstelle im Haus kostet ~5.4 Mbit/s"* |
| **2. Die Leitung** | Sync-Rate & physikalisches Maximum gegen deinen Vertrag | *„Leitung schafft maximal 41.9 Mbit/s — kann die vertraglichen 50 nicht liefern"* |
| **3. Der Provider** | Gemessener Durchsatz gegen das, womit die Leitung synct | *„Leitung synct 36 Mbit/s, nur 31 kommen an"* |

Entscheidend: Es erfasst auch die **eigene Last des Mess-Geräts** (CPU, RAM, Temperatur, Mess-Timing) bei jeder Messung — damit „dein Pi war einfach überlastet" als Erklärung für eine schlechte Messung ausgeschlossen werden kann.

---

## Funktionen

- **Durchgehendes Erreichbarkeits-Monitoring** (alle 5s) — Gateway, öffentliche IPs, DNS, Paketverlust, Latenz, Jitter
- **Automatische Ausfall-Klassifikation** — unterscheidet lokales Netz / Provider / DNS / Routing / Latenz / Paketverlust mit einem Confidence-Score
- **Echte Durchsatz-Tests** (alle 15 Min) — tatsächlicher Down-/Upload gegen Cloudflare, nicht nur Ping
- **Router-Leitungsdaten** (FritzBox via TR-064) — Sync-Rate, physikalisches Maximum, SNR-Abstand, Dämpfung, Verbindungsabbrüche
- **Router-Ereignislog-Parsing** — fängt die Warnungen des Routers ab, inklusive Hausverkabelungsfehler
- **Selbst-Monitoring** — CPU / RAM / Temperatur / Zykluszeit pro Messung, um das Mess-Gerät als Ursache auszuschließen
- **Manipulationssicherer Speicher** — Append-only SQLite, Beweis-Dateien pro Ereignis
- **Lokales Web-Dashboard** — dunkel, offline-fähig, mit Klartext-Bewertung zu jedem Befund
- **Provider-Nachweisexport** — PDF-Report + CSV-Rohdaten, mit sauberer Trennung der drei Schichten

---

## Anforderungen

- Raspberry Pi 4+ (oder jede durchlaufende Linux-Kiste), per Ethernet am Router
- Raspberry Pi OS / Debian (Bookworm oder neuer)
- Python 3.11+
- Für FritzBox-Leitungsdaten: eine FritzBox mit aktiviertem TR-064 (**Heimnetz → Netzwerk → Netzwerkeinstellungen → „Zugriff für Anwendungen zulassen"**)

---

## Installation

```bash
git clone https://github.com/kaldox/netwatch.git
cd netwatch

# Beispiel-Config kopieren und für dein Setup anpassen
cp config/config.example.yaml config/config.yaml
nano config/config.yaml      # Vertragsgeschwindigkeit, Router-Host etc. setzen

# Als systemd-Service installieren (legt User, venv, Service an)
sudo ./install.sh
```

Dann `http://<deine-pi-ip>:8080` öffnen.

### Manueller / Entwicklungs-Lauf

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m src.main
```

---

## Konfiguration

Alle Einstellungen liegen in `config/config.yaml` (aus `config/config.example.yaml` kopieren). Wichtige Abschnitte:

```yaml
speedtest:
  enabled: true
  interval_seconds: 900        # wie oft ein echter Durchsatztest läuft

fritzbox:
  enabled: true
  vendor: "fritzbox"           # "fritzbox" | "generic_tr064" | "none"
  host: "192.168.178.1"
  username: "dein-fritzbox-user"
  password: "dein-fritzbox-passwort"   # nur für erweiterte DSL-Diagnose
  contract_download_mbps: 0    # deine vertragliche Geschwindigkeit (0 = Vergleich aus)
  contract_upload_mbps: 0
```

> **Sicherheit:** Dein Router-Passwort liegt in `config/config.yaml`, die git-ignoriert ist und mit `640`-Rechten installiert wird (nur root + Service-User). Leg einen **dedizierten FritzBox-User** mit nur den nötigen Rechten an, statt dein Admin-Passwort zu nehmen.

---

## Der Provider-Nachweisexport

Wenn NetWatch ein bis zwei Wochen Daten gesammelt hat:

```bash
sudo -u netwatch /opt/netwatch/venv/bin/python -m src.export_cli 14
```

Das erzeugt unter `reports/`:

- **`netwatch_providernachweis_<datum>.pdf`** — strukturierter Report mit Trennung Hausverkabelung / Leitung / Provider und einer Mess-Integritäts-Erklärung
- **`netwatch_speedtests_<datum>.csv`** — jeder Durchsatztest mit dem Leitungs-Sync daneben
- **`netwatch_fritzbox_<datum>.csv`** — Leitungs-Sync / Max / SNR / Dämpfung über die Zeit
- **`netwatch_fritzbox_log_<datum>.csv`** — klassifizierte Router-Log-Ereignisse (Sync-Änderungen, Abbrüche, Verkabelungsfehler)

### Ein Wort zur Ehrlichkeit

Dieses Tool ist darauf ausgelegt, einen *ehrlichen* Fall zu machen, keinen konstruierten. Es zeigt bewusst Probleme auf **deiner** Seite (Hausverkabelung, überlastetes Mess-Gerät), damit der Teil, den du dem Provider zuschreibst, sauber und belastbar ist. Wenn dein Router einen Hausverkabelungsfehler meldet, behebe den zuerst — sonst zeigt der Provider zu Recht darauf.

Für eine rechtlich anerkannte Messung (zumindest in Deutschland) kombiniere NetWatchs durchgehende Aufzeichnung mit der offiziellen **Breitbandmessung-Desktop-App der Bundesnetzagentur**. NetWatch ist die Langzeit-Dokumentation rund um diese Momentaufnahme.

---

## Router-Unterstützung

| Router | Modus | Was du bekommst |
|--------|-------|-----------------|
| **AVM FritzBox** | ✅ Voll | Sync-Rate, physikalisches Max, SNR, Dämpfung, Ereignislog, Verkabelungsfehler-Erkennung |
| Generisches TR-064 | 🧪 Experimentell | Nur Sync-Rate + Link-Status (keine erweiterte Diagnose) |
| Keiner | ✅ Läuft | Volle Erreichbarkeit + Speed-Monitoring, kein Leitungsvergleich |

Einstellbar über `fritzbox.vendor` in der Config. Einen neuen Router hinzuzufügen heißt, ein kleines `RouterProvider`-Interface in `src/router.py` zu implementieren — **Beiträge sehr willkommen.**

---

## Wie die Ausfall-Klassifikation funktioniert

NetWatch beobachtet alle Ziele und klassifiziert, wenn etwas ausfällt, das Muster:

- **Gateway nicht erreichbar** → `LOCAL_NETWORK_FAILURE` (deine Seite)
- **Gateway OK, alle externen Ziele weg** → `ISP_FAILURE` (Provider)
- **IPs erreichbar, DNS scheitert** → `DNS_FAILURE`
- **Teilweise Erreichbarkeit** → `ROUTING_FAILURE`
- **Latenz / Paketverlust über Schwelle** → `LATENCY_DEGRADATION` / `PACKET_LOSS`

Jedes Ereignis erfasst die öffentliche IP vorher/während/nachher, fährt ein Traceroute + MTR und macht einen Snapshot der Pi-Ressourcen — damit du einen echten Ausfall von einem Mess-Artefakt unterscheiden kannst.

---

## Architektur

```
src/
├── main.py          # Orchestrator: Messschleife, Scheduler, Signal-Handling
├── monitor.py       # parallele Erreichbarkeit/Latenz/DNS-Messung
├── classifier.py    # Ausfall-Klassifikations-Zustandsmaschine
├── speedtest.py     # Cloudflare-Durchsatztest
├── fritzbox.py      # FritzBox TR-064 Leitungsdaten + Ereignislog-Reader
├── router.py        # herstellerneutrale Router-Abstraktion (experimentell)
├── resources.py     # CPU/RAM/Last/Temperatur-Sampling
├── database.py      # thread-sichere SQLite mit automatischen Schema-Migrationen
├── statistics.py    # tägliche/monatliche Aggregation
├── reports.py       # monatlicher PDF-Report
├── export.py        # Provider-Nachweisexport (PDF + CSV)
├── dashboard.py     # Flask-Web-Dashboard + JSON-API
├── storage.py       # Logging, CSV-Export, Beweis-Dateien
└── notifier.py      # optionale Telegram-/E-Mail-Alerts
```

Daten liegen in SQLite mit WAL-Modus und automatischen, nur-vorwärts laufenden Schema-Migrationen.

---

## Mitmachen

Issues und Pull Requests willkommen — besonders:

- Router-Provider für Nicht-AVM-Hardware (siehe `src/router.py`)
- Dashboard-Übersetzungen (aktuell Deutsch)
- Zusätzliche Ausfall-Klassifikations-Heuristiken

---

## Lizenz

MIT — siehe [LICENSE](LICENSE).

---

## Haftungsausschluss

NetWatch ist ein Mess- und Dokumentationswerkzeug. Es ist keine Rechtsberatung, und seine Ausgabe ist für sich allein keine rechtsverbindliche Messung. Für formale Streitfälle kombiniere es mit den offiziellen Messverfahren deines Providers und deiner nationalen Regulierungsbehörde.

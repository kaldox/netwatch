# NetWatch

[🇩🇪 Deutsch](README.md) · [🇬🇧 English](README.en.md) · **🐻 Baseldütsch**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue)

> ⚠️ **Experimentell:** Die Versjoon isch uf Baseldütsch – churzwiilig gmeint, nid zwingend perfekt. Wär e Fähler findet, derf ne gärn flicke.

**Durchgehendi Überwachig vo dr Internet-Qualität, wo festnaglet, *wo* dis Problem würklig lit — dini Verkabelig, dini Leitig oder dr Provider.**

NetWatch lauft 24/7 uf eme Raspberry Pi (oder jedere Linux-Chischte wo durchlauft) und beantwortet di Froge, wo en eimoolige Speedtest nid cha:

- Isch s Internet *würklig* so langsam wie s sich aafüehlt — und zu welere Tageszit?
- Wenn d Verbindig abbricht: isch das **mis** Problem oder s vom **Provider**?
- Übechumm i d Gschwindigkeit, für wo n i zahl — und wenn nid, *worum* nid?

Es misst drum durchgehend, klassifiziert Uusfäll automatisch, list, wie dr Router d Leitig gseht, und macht Nochwiis, wo du em Provider chasch vorlege.

> **Hiwiis:** NetWatch isch ursprünglich rund um d **AVM FritzBox** boue worde (im dütschsprochige Ruum sehr verbreitet), wo als einzige hütt voll unterstützt isch. Anderi Router laufe nume iigschränkt und experimentell — lueg [Router-Unterstützig](#router-unterstützig).

---

## Worum s das git

En normaale Speedtest seit dr „du hesch jetz grad 33 Mbit/s." Er seit dr **nid**:

- öb das dr Provider isch wo drosslet, e schlächti Leitig, oder e Problem bi dir deheim;
- öb s Grät, vo wo du tesch, sälber dr Flaschehals isch;
- wie dini Verbindig letschte Zischtig am Obe um achti uusgseh het.

NetWatch trennt s Problem in **drei Schichte**, demit d Ursach klar isch:

| Schicht | Was prüeft wird | Bispil-Befund |
|---------|------------------|-----------------|
| **1. Husverkabelig** | D Verkabeligsfähler-Erkennig vom Router | *„E Spleissstell im Hus koschtet ~5.4 Mbit/s"* |
| **2. D Leitig** | Sync-Rate & physikalischs Maximum gäge dr Vertrag | *„Leitig schafft maximal 41.9 Mbit/s — cha di vertraglige 50 nid liefere"* |
| **3. Dr Provider** | Gmässene Durchsatz gäge das wo d Leitig synct | *„Leitig synct 36 Mbit/s, nume 31 chömme aa"* |

Wichtig: Es erfasst au di **eigeni Last vom Mässgrät** (CPU, RAM, Temperatur, Mäss-Timing) bi jedere Mässig — demit „dis Pi isch eifach überlaschtet gsi" als Erklärig für e schlächti Mässig uusgschlosse cha wärde.

---

## Funktione

- **Durchgehends Erreichbarkeits-Monitoring** (alli 5s) — Gateway, öffentligi IPs, DNS, Paketverluscht, Latänz, Jitter
- **Automatischi Uusfall-Klassifikation** — underscheidet lokals Netz / Provider / DNS / Routing / Latänz / Paketverluscht mit eme Confidence-Score
- **Echti Durchsatz-Tescht** (alli 15 Min) — würkligi Down-/Uploads gäge Cloudflare, nid nume Ping
- **Router-Leitigsdate** (FritzBox via TR-064) — Sync-Rate, physikalischs Max, SNR-Abstand, Dämpfig, Verbindigsabbrüch
- **Router-Ereignislog-Parsing** — fangt d Warnige vom Router ab, inklusiv Husverkabeligsfähler
- **Sälbscht-Monitoring** — CPU / RAM / Temperatur / Zyklusziit pro Mässig, zum s Mässgrät als Ursach uusschliesse
- **Manipulationssichere Speicher** — Append-only SQLite, Bewiis-Date pro Ereignis
- **Lokals Web-Dashboard** — dunkel, offline-fähig, mit Klartext-Bewertig zu jedem Befund
- **Provider-Nochwiisexport** — PDF-Report + CSV-Rohdate, mit suuberer Trennig vo de drei Schichte

---

## Aaforderige

- Raspberry Pi 4+ (oder jedi durchlaufendi Linux-Chischte), per Ethernet am Router
- Raspberry Pi OS / Debian (Bookworm oder neuer)
- Python 3.11+
- Für FritzBox-Leitigsdate: e FritzBox mit aktivierts TR-064 (**Heimnetz → Netzwärk → Netzwärkiistellige → „Zuegriff für Aawändige zuelo"**)

---

## Installation

```bash
git clone https://github.com/kaldox/netwatch.git
cd netwatch

# Bispil-Config kopiere und für dis Setup aapasse
cp config/config.example.yaml config/config.yaml
nano config/config.yaml      # Vertragsgschwindigkeit, Router-Host etc. setze

# Als systemd-Service installiere (leit User, venv, Service aa)
sudo ./install.sh
```

Denn `http://<dini-pi-ip>:8080` ufmache.

---

## Dr Provider-Nochwiisexport

Wenn NetWatch ei bis zwei Wuche Date gsammlet het:

```bash
sudo -u netwatch /opt/netwatch/venv/bin/python -m src.export_cli 14
```

Das macht under `reports/` e PDF mit dr Trennig Husverkabelig / Leitig / Provider plus CSV-Rohdate.

### E Wort zur Ehrligkeit

Das Tool isch drufuus gleit, en *ehrlige* Fall z mache, kei konschtruierte. Es zeigt bewusst Probleme uf **dinere** Site (Husverkabelig, überlaschtets Mässgrät), demit dr Teil, wo du em Provider zueschribsch, suuber und belaschtbar isch. Wenn dr Router en Husverkabeligsfähler mäldet, flick dä zerscht — sunscht zeigt dr Provider z Rächt druf.

---

## Router-Unterstützig

| Router | Modus | Was d übechunnsch |
|--------|-------|-----------------|
| **AVM FritzBox** | ✅ Voll | Sync-Rate, physikalischs Max, SNR, Dämpfig, Ereignislog, Verkabeligsfähler |
| Generischs TR-064 | 🧪 Experimentell | Nume Sync-Rate + Link-Status |
| Keine | ✅ Lauft | Volli Erreichbarkeit + Speed-Monitoring, kei Leitigsverglich |

---

## Architektur

```
src/
├── main.py          # Orchestrator: Mässschleife, Scheduler
├── monitor.py       # parallääli Erreichbarkeit/Latänz/DNS-Mässig
├── classifier.py    # Uusfall-Klassifikations-Zuestandsmaschine
├── speedtest.py     # Cloudflare-Durchsatztescht
├── fritzbox.py      # FritzBox TR-064 Leitigsdate + Ereignislog
├── database.py      # thread-sicheri SQLite mit Auto-Migratione
├── export.py        # Provider-Nochwiisexport (PDF + CSV)
├── dashboard.py     # Flask-Web-Dashboard + JSON-API
└── notifier.py      # optionali Telegram-/E-Mail-Alerts
```

---

## Mitmache

Issues und Pull Requests willkomme — bsunders Router-Provider für Nicht-AVM-Hardware und Dashboard-Übersetzige.

---

## Lizänz

MIT — lueg [LICENSE](LICENSE).

---

## Haftigsuusschluss

NetWatch isch e Mäss- und Dokumentationswärchzüg. Es isch kei Rächtsbroterig, und sini Uusgaab isch für sich elei kei rächtsverbindligi Mässig. Für formali Striitfäll kombinier s mit de offizielle Mässverfahre vo dim Provider und dinere nationale Regulierigsbehörde.

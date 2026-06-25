# NetWatch

[🇩🇪 Deutsch](README.md) · **🇬🇧 English** · [🐻 Baseldütsch](README.bl.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue)

**Continuous internet-quality monitoring that pins down *where* your connection problem actually is — your wiring, your line, or your provider.**

NetWatch runs 24/7 on a Raspberry Pi (or any always-on Linux box) and answers the questions you can't answer with a one-off speed test:

- Is my internet *actually* as slow as it feels — and at what times of day?
- When it drops, is it **my** fault or the **provider's**?
- Am I getting the speed I pay for — and if not, *why* not?

It does this by measuring continuously, classifying outages automatically, reading your router's own view of the line, and producing evidence you can take to your provider.

> **Note:** NetWatch was originally built around the **AVM FritzBox** (very common in German-speaking countries), which is the only fully-supported router today. Other routers work in a limited, experimental mode — see [Router support](#router-support).

---

## Why this exists

A normal speed test tells you "you have 33 Mbit/s right now." It does **not** tell you:

- whether that's the provider throttling you, a bad line, or a problem inside your own home;
- whether the device you're testing from is itself the bottleneck;
- what your connection looked like last Tuesday at 8pm.

NetWatch separates the problem into **three layers** so the cause is unambiguous:

| Layer | What it checks | Example finding |
|-------|----------------|-----------------|
| **1. Home wiring** | The router's own cabling-defect detection | *"An in-house splice is costing ~5.4 Mbit/s"* |
| **2. The line** | Sync rate & physical max vs. your contract | *"Line maxes out at 41.9 Mbit/s — can't deliver the contracted 50"* |
| **3. The provider** | Measured throughput vs. what the line syncs at | *"Line syncs 36 Mbit/s, only 31 arrives"* |

Crucially, it also records the **monitoring device's own load** (CPU, RAM, temperature, measurement timing) with every reading — so "your Pi was just overloaded" can be ruled out as an explanation for a bad measurement.

---

## Features

- **Continuous reachability monitoring** (every 5s) — gateway, public IPs, DNS, packet loss, latency, jitter
- **Automatic outage classification** — distinguishes local-network / ISP / DNS / routing / latency / packet-loss faults with a confidence score
- **Real throughput tests** (every 15 min) — actual download/upload against Cloudflare, not just ping
- **Router line readout** (FritzBox via TR-064) — sync rate, physical max, SNR margin, attenuation, connection drops
- **Router event-log parsing** — captures the router's own warnings, including in-home cabling defects
- **Self-monitoring** — CPU / RAM / temperature / cycle-time logged per measurement to rule out the measuring device as a cause
- **Tamper-evident storage** — append-only SQLite, per-event evidence files
- **Local web dashboard** — dark-themed, offline-capable, with a plain-language verdict on each finding
- **Provider evidence export** — PDF report + CSV raw data, separating the three layers cleanly

---

## Screenshots

The dashboard runs locally at `http://<your-pi-ip>:8080`:

- **Übersicht / Overview** — availability ring, current speed, public IP, recent events
- **Geschwindigkeit / Speed** — download/upload over time with the line-sync reference, hour-of-day pattern, and the FritzBox line-comparison verdict
- **Ereignisse / Events** — full outage history with a ⚠ flag when the Pi was under load at the time
- **ISP-Nachweise / ISP evidence** — outages with before/during/after public IP and traceroutes

*(The UI is currently in German.)*

---

## Requirements

- Raspberry Pi 4+ (or any always-on Linux box), wired to your router via Ethernet
- Raspberry Pi OS / Debian (Bookworm or newer)
- Python 3.11+
- For FritzBox line data: a FritzBox with TR-064 enabled (**Home Network → Network → Network Settings → "Allow access for applications"**)

---

## Installation

```bash
git clone https://github.com/kaldox/netwatch.git
cd netwatch

# Copy the example config and edit it for your setup
cp config/config.example.yaml config/config.yaml
nano config/config.yaml      # set your contract speed, router host, etc.

# Install as a systemd service (creates a user, venv, service)
sudo ./install.sh
```

Then open `http://<your-pi-ip>:8080`.

### Manual / development run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m src.main
```

---

## Configuration

All settings live in `config/config.yaml` (copy it from `config/config.example.yaml`). Key sections:

```yaml
speedtest:
  enabled: true
  interval_seconds: 900        # how often to run a real throughput test

fritzbox:
  enabled: true
  vendor: "fritzbox"           # "fritzbox" | "generic_tr064" | "none"
  host: "192.168.178.1"
  username: "your-fritzbox-user"
  password: "your-fritzbox-password"   # only for extended DSL diagnostics
  contract_download_mbps: 0    # your contracted speed (0 = comparison off)
  contract_upload_mbps: 0
```

> **Security:** Your router password is stored in `config/config.yaml`, which is git-ignored and installed with `640` permissions (root + service user only). Create a **dedicated FritzBox user** with only the permissions it needs, rather than using your admin password.

---

## The provider evidence export

Once NetWatch has collected a week or two of data:

```bash
sudo -u netwatch /opt/netwatch/venv/bin/python -m src.export_cli 14
```

This produces, under `reports/`:

- **`netwatch_providernachweis_<date>.pdf`** — a structured report separating home-wiring / line / provider, with a measurement-integrity statement
- **`netwatch_speedtests_<date>.csv`** — every throughput test with the line sync alongside
- **`netwatch_fritzbox_<date>.csv`** — line sync / max / SNR / attenuation over time
- **`netwatch_fritzbox_log_<date>.csv`** — classified router log events (sync changes, disconnects, cabling defects)

### A word on honesty

This tool is designed to make an *honest* case, not to manufacture one. It explicitly surfaces problems on **your** side (in-home wiring, an overloaded measuring device) so that the part you attribute to the provider is clean and defensible. If your router reports an in-home cabling defect, fix that first — otherwise the provider will rightly point to it.

For a legally recognised measurement (at least in Germany), pair NetWatch's continuous record with the official **Bundesnetzagentur Breitbandmessung** desktop app. NetWatch is the long-term documentation around that snapshot.

---

## Router support

| Router | Mode | What you get |
|--------|------|--------------|
| **AVM FritzBox** | ✅ Full | Sync rate, physical max, SNR, attenuation, event log, cabling-defect detection |
| Generic TR-064 | 🧪 Experimental | Sync rate + link status only (no extended diagnostics) |
| None | ✅ Works | Full reachability + speed monitoring, no line comparison |

Set this with `fritzbox.vendor` in the config. Adding a new router means implementing one small `RouterProvider` interface in `src/router.py` — **contributions very welcome.**

---

## How outage classification works

NetWatch watches all targets and, when things fail, classifies the pattern:

- **Gateway unreachable** → `LOCAL_NETWORK_FAILURE` (your side)
- **Gateway OK, all external targets down** → `ISP_FAILURE` (provider)
- **IPs reachable, DNS fails** → `DNS_FAILURE`
- **Partial reachability** → `ROUTING_FAILURE`
- **Latency / packet loss over threshold** → `LATENCY_DEGRADATION` / `PACKET_LOSS`

Each event records the public IP before/during/after, runs a traceroute + MTR, and snapshots the Pi's resource state — so you can tell a real outage from a measurement artefact.

---

## Architecture

```
src/
├── main.py          # orchestrator: measurement loop, schedulers, signal handling
├── monitor.py       # parallel reachability/latency/DNS measurement
├── classifier.py    # outage classification state machine
├── speedtest.py     # Cloudflare throughput test
├── fritzbox.py      # FritzBox TR-064 line data + event-log reader
├── router.py        # vendor-neutral router abstraction (experimental)
├── resources.py     # CPU/RAM/load/temperature sampling
├── database.py      # thread-safe SQLite with automatic schema migrations
├── statistics.py    # daily/monthly aggregation
├── reports.py       # monthly PDF report
├── export.py        # provider evidence export (PDF + CSV)
├── dashboard.py     # Flask web dashboard + JSON API
├── storage.py       # logging, CSV export, evidence files
└── notifier.py      # optional Telegram / email alerts
```

Data is stored in SQLite with WAL mode and automatic, forward-only schema migrations.

---

## Contributing

Issues and pull requests welcome — especially:

- Router providers for non-AVM hardware (see `src/router.py`)
- Dashboard translations (currently German)
- Additional outage-classification heuristics

---

## License

MIT — see [LICENSE](LICENSE).

---

## Disclaimer

NetWatch is a measurement and documentation tool. It is not legal advice, and its output is not by itself a legally binding measurement. For formal disputes, combine it with your provider's and your national regulator's official measurement procedures.

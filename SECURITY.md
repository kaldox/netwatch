# Security Policy

> English version below · [Deutsche Version](#sicherheitsrichtlinie)

## Reporting a Vulnerability

Found a security issue in **NetWatch itself** (not in your ISP line or a
device it monitors)? Please report it privately, not as a public issue:

1. Go to the [Security tab](https://github.com/kaldox/netwatch/security) of this repository
2. Click **"Report a vulnerability"** to open a private GitHub Security Advisory
3. Describe the issue: what it is, how to reproduce it, and its impact

You'll get an acknowledgement and, once a fix is ready, credit in the advisory
(unless you'd rather stay anonymous).

## Scope

**In scope** – vulnerabilities in NetWatch's own code, e.g.:
- The FritzBox/router credentials or captured data leaking outside the host
- Injection or unsafe deserialization when parsing router responses, config,
  or the SQLite database
- Path traversal / unsafe file writes in report or export generation
- The web dashboard exposing data or actions it shouldn't (it is meant for a
  trusted LAN, but obvious holes still count)

**Out of scope:**
- Running the dashboard unauthenticated on an untrusted network – it is
  designed for a trusted home LAN
- Vulnerabilities in third-party components (Flask, reportlab, the router
  firmware) – please report those upstream
- Line problems NetWatch *detects* – that's the tool doing its job

## Supported Versions

Only the latest release is supported with security fixes. Always update to the
newest version.

## Response

NetWatch is a hobby project maintained in spare time – no guaranteed response
time or SLA, but security reports get priority over features and regular bugs.

---

## Sicherheitsrichtlinie

## Eine Schwachstelle melden

Eine Sicherheitslücke in **NetWatch selbst** gefunden (nicht in deiner
Anschlussleitung oder einem überwachten Gerät)? Bitte privat melden, nicht als
öffentliches Issue:

1. Zum [Security-Tab](https://github.com/kaldox/netwatch/security) dieses Repos gehen
2. **"Report a vulnerability"** klicken – öffnet einen privaten GitHub Security Advisory
3. Beschreiben: was ist das Problem, wie reproduziert man es, welche Auswirkung hat es

Du bekommst eine Rückmeldung und, sobald ein Fix bereitsteht, eine Nennung im
Advisory (auf Wunsch auch anonym).

## Umfang

**Relevant** – Schwachstellen im eigenen NetWatch-Code, z.B.:
- FritzBox-/Router-Zugangsdaten oder erfasste Daten verlassen den Host
- Injection oder unsichere Deserialisierung beim Einlesen von
  Router-Antworten, Config oder der SQLite-Datenbank
- Path-Traversal / unsichere Dateizugriffe bei Report- oder Export-Erzeugung
- Das Web-Dashboard gibt Daten oder Aktionen preis, die es nicht sollte
  (gedacht für ein vertrauenswürdiges LAN, offensichtliche Lücken zählen
  trotzdem)

**Nicht relevant:**
- Das Dashboard unauthentifiziert in einem nicht vertrauenswürdigen Netz
  betreiben – es ist für ein vertrauenswürdiges Heimnetz gedacht
- Schwachstellen in Drittkomponenten (Flask, reportlab, Router-Firmware) –
  bitte dort melden
- Leitungsprobleme, die NetWatch *erkennt* – das ist der Zweck des Tools

## Unterstützte Versionen

Nur die jeweils aktuellste Version bekommt Sicherheits-Fixes. Immer auf die
neueste Version aktualisieren.

## Reaktionszeit

NetWatch ist ein Hobby-Projekt, gepflegt in der Freizeit – keine garantierte
Reaktionszeit oder SLA, aber Sicherheitsmeldungen haben Vorrang.

# Lobbyregister-Monitor BMWE

Automatische Übersicht neuer Stellungnahmen und Gutachten aus dem [Lobbyregister des Deutschen Bundestages](https://www.lobbyregister.bundestag.de), gefiltert nach Relevanz für das Bundesministerium für Wirtschaft und Energie (BMWE) und den Bundestag.

## Was macht dieses System?

Jeden **Montag um 6:00 Uhr** läuft automatisch ein Skript, das:

1. Neue Stellungnahmen und Gutachten über die [Lobbyregister API v2](https://api.lobbyregister.bundestag.de/rest/v2/swagger-ui/) abruft
2. Eine öffentlich zugängliche **Übersichtsseite** auf GitHub Pages aktualisiert
3. Eine **wöchentliche Zusammenfassungs-Mail** an das Büropostfach versendet
4. Einen **Statusbericht** an die Admin-Adresse schickt (Selbsttest: API, E-Mail-Dienst, Seitenverfügbarkeit, API-Versionsänderungen)

## Links

| | |
|---|---|
| **Übersichtsseite** | https://bmwe-iiia4.github.io/lobbyregister-monitor |
| **Nutzungsanleitung** | https://bmwe-iiia4.github.io/lobbyregister-monitor/hilfe.html |
| **Wartungsdokumentation** | https://bmwe-iiia4.github.io/lobbyregister-monitor/wartung.html |
| **GitHub Actions (Logs)** | https://github.com/BMWE-IIIA4/lobbyregister-monitor/actions |

## Gefilterte Inhalte

**Empfänger:** BMWE (21. Wahlperiode) und Bundestag (21. Wahlperiode)

**Themenfelder:** Energie & Wasserstoff · Klimaschutz · EU-Binnenmarkt · EU-Gesetzgebung · Wettbewerbsrecht · Politisches Leben/Parteien · Sonstige

**Zeitraum:** ab 1. Januar 2026

## Dateien

```
.github/workflows/update.yml   – Automatischer Wochenablauf (GitHub Actions)
scripts/fetch_and_build.py     – Datenabruf und HTML-Generierung
scripts/send_email.py          – Wöchentlicher E-Mail-Versand
scripts/health_check.py        – Wöchentlicher Selbsttest und Admin-Bericht
scripts/template.html          – HTML-Vorlage für die Übersichtsseite
docs/                          – Generierte Seiten (werden automatisch überschrieben)
```

## Wartung und Übergabe

Alle Informationen zur Wartung, zu Zugangsdaten, Fehlerbehebung und zur vollständigen Neueinrichtung durch eine dritte Person stehen in der [Wartungsdokumentation](https://bmwe-iiia4.github.io/lobbyregister-monitor/wartung.html).

**Ansprechpartner:** Martin Jahn, BMWE Unterabteilung IIIA4 · martin.jahn@bmwe.bund.de

## Datenquelle

Alle Daten stammen direkt aus dem Lobbyregister des Deutschen Bundestages und werden unverändert weitergegeben. Rechtsgrundlage der Veröffentlichungspflicht: [Lobbyregistergesetz (LobbyRG)](https://www.lobbyregister.bundestag.de/informationen-und-hilfe/rechtsvorschriften-parlamentarische-materialien-gl-2022--863566).

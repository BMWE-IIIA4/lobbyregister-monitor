# Lobbyregister-Monitor BMWE

Automatische Übersicht neuer Stellungnahmen und Gutachten aus dem [Lobbyregister des Deutschen Bundestages](https://www.lobbyregister.bundestag.de), gefiltert nach Relevanz für das Bundesministerium für Wirtschaft und Energie (BMWE) und den Bundestag.

## Was macht dieses System?

Jeden Tag **um 6:00 Uhr** läuft automatisch ein Skript, das:

1. Neue Stellungnahmen und Gutachten über die [Lobbyregister API v2](https://api.lobbyregister.bundestag.de/rest/v2/swagger-ui/) abruft
2. Die Einträge per **Google Gemini 3.1 Flash Lite** auf Energie- und Klimarelevanz prüft und Beschreibungstexte zusammenfasst
3. Eine öffentlich zugängliche **Übersichtsseite** auf GitHub Pages aktualisiert
4. Jeden **Montag** eine **wöchentliche Zusammenfassungs-Mail** an das Büropostfach versendet
5. Jeden **Montag** einen **Statusbericht** an die Admin-Adresse schickt (Selbsttest: API, E-Mail-Dienst, Gemini, Seitenverfügbarkeit, API-Versionsänderungen)

## Links

| | |
|---|---|
| **Übersichtsseite** | https://lobbyregister-bot.de |
| **Nutzungsanleitung** | https://lobbyregister-bot.de/hilfe.html |
| **Wartungsdokumentation** | https://lobbyregister-bot.de/wartung.html |
| **GitHub Actions (Logs)** | https://lobbyregister-bot.de/actions |

## Gefilterte Inhalte

**Empfänger:** BMWE (21. Wahlperiode) und Bundestag (21. Wahlperiode)

**Themenfelder:** Energie & Wasserstoff · Klimaschutz · EU-Binnenmarkt · EU-Gesetzgebung · Wettbewerbsrecht · Politisches Leben/Parteien · Sonstige

**KI-Relevanzfilter:** Einträge in breiten Kategorien (Wettbewerbsrecht, EU-Gesetzgebung, Parteien, Sonstige) werden per Gemini 3.1 Flash Lite auf Bezug zum Aufgabenportfolio der Unterabteilung IIIA geprüft. Einträge ohne Energie-/Klimabezug werden aussortiert. Energie/Wasserstoff-Einträge bleiben immer erhalten.

**Zeitraum:** ab 1. Januar 2026

## Dateien

```
.github/workflows/update.yml   – Automatischer Tagesablauf (GitHub Actions)
scripts/fetch_and_build.py     – Datenabruf und HTML-Generierung
scripts/gemini_enrich.py       – KI-Relevanzfilterung, Zusammenfassungen und HTML-Rebuild (Gemini 3.1 Flash Lite)
scripts/send_email.py          – Wöchentlicher E-Mail-Versand (montags)
scripts/health_check.py        – Wöchentlicher Selbsttest und Admin-Bericht (montags)
scripts/template.html          – HTML-Vorlage für die Übersichtsseite
docs/                          – Generierte Seiten (werden automatisch überschrieben)
```

## Wartung und Übergabe

Alle Informationen zur Wartung, zu Zugangsdaten, Fehlerbehebung und zur vollständigen Neueinrichtung durch eine dritte Person stehen in der [Wartungsdokumentation](https://lobbyregister-bot.de/wartung.html).

**Ansprechpartner:** Martin Jahn, BMWE, IIIA4 · martin.jahn@bmwe.bund.de

## Datenquelle

Alle Daten stammen direkt aus dem Lobbyregister des Deutschen Bundestages und werden unverändert weitergegeben. Beschreibungstexte werden per KI zusammengefasst; die Originaltexte bleiben in der Datendatei erhalten. Rechtsgrundlage der Veröffentlichungspflicht: [Lobbyregistergesetz (LobbyRG)](https://www.lobbyregister.bundestag.de/informationen-und-hilfe/rechtsvorschriften-parlamentarische-materialien-gl-2022--863566).

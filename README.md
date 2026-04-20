# Lobbyregister-Monitor

Automatische Übersicht neuer Stellungnahmen und Gutachten aus dem [Lobbyregister des Deutschen Bundestages](https://www.lobbyregister.bundestag.de), gefiltert nach Relevanz für das Bundesministerium für Wirtschaft und Energie (BMWE) und den Bundestag.

## Was macht dieses System?

Jeden Tag um 6:00 Uhr läuft automatisch ein Workflow, der:

1. **Inkrementell** neue Stellungnahmen über die [Lobbyregister API v2](https://api.lobbyregister.bundestag.de/rest/v2/swagger-ui/) abruft (nur neue Registereinträge, bereits bekannte werden übersprungen)
2. Die Einträge per **Google Gemini 3.1 Flash Lite** auf Energie- und Klimarelevanz prüft und Beschreibungstexte zusammenfasst (mit lokalem Cache für bereits geprüfte Einträge)
3. Eine öffentlich zugängliche **Übersichtsseite** auf GitHub Pages aktualisiert
4. Jeden **Montag** eine **wöchentliche Zusammenfassungs-Mail** versendet
5. Jeden **Montag** einen **Statusbericht** durchführt (Selbsttest: API, E-Mail-Dienst, Gemini, Seitenverfügbarkeit)
6. Ein **Workflow-Protokoll** mit Durchlauf-Statistiken archiviert

## Gefilterte Inhalte

**Empfänger:** BMWE und Bundestag

**Zweistufige Filterung:** Pre-Filter auf Organisationsebene, strikter Filter auf Stellungnahme-Ebene

**Themenfelder:** Energie & Wasserstoff · Klimaschutz · EU-Binnenmarkt · EU-Gesetzgebung · Wettbewerbsrecht · Politisches Leben/Parteien · Sonstige

**KI-Relevanzfilter:** Einträge in breiten Kategorien werden per Gemini auf Bezug zum Aufgabenportfolio geprüft. Einträge ohne Energie-/Klimabezug werden aussortiert. Energie/Wasserstoff-Einträge bleiben immer erhalten.

**Zeitraum:** ab 1. Januar 2026

## Dateien

```
.github/workflows/update.yml   – Automatischer Tagesablauf (GitHub Actions)
scripts/fetch_and_build.py     – Inkrementeller Datenabruf und HTML-Generierung
scripts/gemini_enrich.py       – KI-Relevanzfilterung, Zusammenfassungen, HTML-Rebuild
scripts/send_email.py          – Wöchentlicher E-Mail-Versand (montags)
scripts/health_check.py        – Wöchentlicher Selbsttest und Admin-Bericht (montags)
scripts/save_run_log.py        – Workflow-Protokoll (nach jedem Durchlauf)
scripts/template.html          – HTML-Vorlage für die Übersichtsseite
docs/                          – Generierte Seiten (werden automatisch überschrieben)
```

## Datenquelle

Alle Daten stammen direkt aus dem Lobbyregister des Deutschen Bundestages und werden unverändert weitergegeben. Beschreibungstexte werden per KI zusammengefasst; die Originaltexte bleiben in der Datendatei erhalten. Rechtsgrundlage: [Lobbyregistergesetz (LobbyRG)](https://www.lobbyregister.bundestag.de/informationen-und-hilfe/rechtsvorschriften-parlamentarische-materialien-gl-2022--863566).

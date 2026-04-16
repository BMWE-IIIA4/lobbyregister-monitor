"""
fetch_and_build.py
==================
Ruft Stellungnahmen vom Lobbyregister ab und generiert die HTML-Seite.

Datenquelle: Lobbyregister API v2
API-Dokumentation: https://api.lobbyregister.bundestag.de/rest/v2/swagger-ui/
"""

import json
import os
import requests
from datetime import datetime, date, timedelta
from collections import defaultdict
from pathlib import Path

# ── Konfiguration ──────────────────────────────────────────────────────────────

API_BASE = "https://api.lobbyregister.bundestag.de/rest/v2"
API_KEY = os.environ.get("LOBBYREGISTER_API_KEY", "5bHB2zrUuHR6YdPoZygQhWfg2CBrjUOi")
SITE_URL = "https://bmwe-iiia4.github.io/lobbyregister-monitor"

# Startdatum: nur Einträge ab diesem Datum
START_DATE = date(2026, 1, 1)

# Themenfelder und ihre Priorität (für Sortierung)
FIELD_PRIORITY = {
    "FOI_ENERGY": 1,
    "FOI_ENERGY_RENEWABLE": 1,
    "FOI_ENERGY_ELECTRICITY": 1,
    "FOI_ENERGY_GAS": 1,
    "FOI_ENERGY_HYDROGEN": 1,
    "FOI_ENVIRONMENT|FOI_ENVIRONMENT_CLIMATE": 2,
    "FOI_ENVIRONMENT_CLIMATE": 2,
    "FOI_EUROPEAN_UNION|FOI_EU_DOMESTIC_MARKET": 2,
    "FOI_EU_DOMESTIC_MARKET": 2,
    "FOI_EUROPEAN_UNION|FOI_EU_LAWS": 2,
    "FOI_EU_LAWS": 2,
    "FOI_BUNDESTAG": 2,
    "FOI_ECONOMY|FOI_ECONOMY_COMPETITION_LAW": 3,
    "FOI_ECONOMY_COMPETITION_LAW": 3,
    "FOI_POLITICAL_PARTIES": 3,
    "FOI_OTHER": 3,
}

# Deutsche Bezeichnungen der Themenfelder
FIELD_LABELS = {
    "FOI_ENERGY": "Energie",
    "FOI_ENERGY_RENEWABLE": "Erneuerbare Energie",
    "FOI_ENERGY_ELECTRICITY": "Strom",
    "FOI_ENERGY_GAS": "Gas",
    "FOI_ENERGY_HYDROGEN": "Wasserstoff",
    "FOI_ENVIRONMENT_CLIMATE": "Klimaschutz",
    "FOI_EU_DOMESTIC_MARKET": "EU-Binnenmarkt",
    "FOI_EU_LAWS": "EU-Gesetzgebung",
    "FOI_BUNDESTAG": "Bundestag",
    "FOI_ECONOMY_COMPETITION_LAW": "Wettbewerbsrecht",
    "FOI_POLITICAL_PARTIES": "Politisches Leben, Parteien",
    "FOI_OTHER": "Sonstige Interessenbereiche",
}

# Gesuchte Empfänger (Kurzbezeichnungen)
TARGET_RECIPIENTS = {"BMWE", "Bundestag"}

# Gesuchte Themenfelder
TARGET_FIELDS = set(FIELD_PRIORITY.keys())


# ── API-Abfrage ────────────────────────────────────────────────────────────────

def fetch_all_statements():
    """Ruft alle relevanten Registereinträge mit Stellungnahmen ab."""
    headers = {"Authorization": f"ApiKey {API_KEY}"}
    all_statements = []
    cursor = None
    page = 0

    print("Rufe Daten vom Lobbyregister ab...")

    while True:
        params = {"format": "json"}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = requests.get(
                f"{API_BASE}/registerentries",
                headers=headers,
                params=params,
                timeout=30
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"FEHLER beim API-Abruf (Seite {page}): {e}")
            raise

        data = resp.json()
        entries = data if isinstance(data, list) else data.get("results", [])

        for entry in entries:
            stmts = extract_statements(entry)
            all_statements.extend(stmts)

        page += 1
        new_cursor = data.get("cursor") if isinstance(data, dict) else None
        if not new_cursor or new_cursor == cursor:
            break
        cursor = new_cursor

        if page % 10 == 0:
            print(f"  Seite {page} abgerufen, {len(all_statements)} Stellungnahmen bisher...")

    print(f"Gesamt: {len(all_statements)} relevante Stellungnahmen gefunden.")
    return all_statements


def extract_statements(entry):
    """Extrahiert relevante Stellungnahmen aus einem Registereintrag."""
    results = []

    register_number = entry.get("registerNumber", "")
    detail = entry.get("registerEntryDetail", {})
    org_name = extract_org_name(detail)
    statements_data = detail.get("statements", {})

    if not statements_data.get("statementsPresent", False):
        return []

    for stmt in statements_data.get("statements", []):
        # Datum prüfen
        stmt_date = None
        sending_date = None
        upload_date = None

        for rg in stmt.get("recipientGroups", []):
            if rg.get("sendingDate"):
                try:
                    sending_date = date.fromisoformat(rg["sendingDate"])
                    stmt_date = sending_date
                except ValueError:
                    pass

        # firstPublicationDate aus account
        pub_date_str = entry.get("account", {}).get("firstPublicationDate", "")
        if pub_date_str:
            try:
                upload_date = date.fromisoformat(pub_date_str[:10])
            except ValueError:
                pass

        # Nur Einträge ab START_DATE
        check_date = upload_date or stmt_date
        if check_date and check_date < START_DATE:
            continue

        # Empfänger prüfen
        recipients = extract_recipients(stmt)
        if not any(r in TARGET_RECIPIENTS for r in recipients):
            continue

        # Themenfelder prüfen
        fields = extract_fields(stmt)
        if not fields:
            continue

        # Priorität berechnen
        priority = min(
            (FIELD_PRIORITY.get(f["code"], 99) for f in fields),
            default=99
        )

        result = {
            "statement_number": stmt.get("statementNumber", ""),
            "register_number": register_number,
            "regulatory_project_title": stmt.get("regulatoryProjectTitle", "Kein Titel"),
            "regulatory_project_number": stmt.get("regulatoryProjectNumber", ""),
            "org_name": org_name,
            "sending_date": sending_date.isoformat() if sending_date else None,
            "upload_date": upload_date.isoformat() if upload_date else None,
            "pdf_url": stmt.get("pdfUrl", ""),
            "pdf_pages": stmt.get("pdfPageCount", 0),
            "summary": stmt.get("text", {}).get("text", ""),
            "recipients": recipients,
            "fields": fields,
            "priority": priority,
        }
        results.append(result)

    return results


def extract_org_name(detail):
    """Extrahiert den Organisationsnamen."""
    identity = detail.get("lobbyistIdentity", {})
    return identity.get("name", "Unbekannte Organisation")


def extract_recipients(stmt):
    """Extrahiert Empfänger-Kurzbezeichnungen."""
    recipients = []
    for rg in stmt.get("recipientGroups", []):
        recips = rg.get("recipients", {})
        # Bundesregierung
        for fg in recips.get("federalGovernment", []):
            dept = fg.get("department", {})
            short = dept.get("shortTitle", "")
            if short:
                recipients.append(short)
        # Bundestag
        for p in recips.get("parliament", []):
            de_name = p.get("de", "")
            if de_name:
                recipients.append("Bundestag")
                break
    return list(dict.fromkeys(recipients))  # Deduplizieren


def extract_fields(stmt):
    """Extrahiert Themenfelder."""
    fields = []
    for f in stmt.get("fieldsOfInterest", []):
        code = f.get("code", "")
        label = f.get("de") or FIELD_LABELS.get(code, code)
        fields.append({"code": code, "label": label})
    return fields


# ── Daten speichern ────────────────────────────────────────────────────────────

def save_data(statements):
    """Speichert die Daten als JSON für die E-Mail-Generierung."""
    Path("docs").mkdir(exist_ok=True)
    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "statements": sorted(
                statements,
                key=lambda x: (x.get("upload_date") or "0000-00-00"),
                reverse=True
            )
        }, f, ensure_ascii=False, indent=2)
    print("Daten gespeichert: docs/data.json")


# ── HTML generieren ────────────────────────────────────────────────────────────

def build_url(stmt):
    """Baut den Link zum Lobbyregistereintrag."""
    sn = stmt.get("statement_number", "")
    rn = stmt.get("register_number", "")
    base = "https://www.lobbyregister.bundestag.de/inhalte-der-interessenvertretung/stellungnahmengutachtensuche"
    if sn and rn:
        return f"{base}/{sn}/{rn}"
    return base


def format_date_de(iso_date):
    """Formatiert ein ISO-Datum auf Deutsch."""
    if not iso_date:
        return "–"
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return iso_date


def group_by_date(statements):
    """Gruppiert Stellungnahmen nach Upload-Datum."""
    grouped = defaultdict(list)
    for stmt in statements:
        key = stmt.get("upload_date") or stmt.get("sending_date") or "unbekannt"
        grouped[key].append(stmt)
    return dict(sorted(grouped.items(), reverse=True))


def get_weekday_de(iso_date):
    """Gibt deutschen Wochentag + Datum zurück."""
    days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    months = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
              "Juli", "August", "September", "Oktober", "November", "Dezember"]
    try:
        d = date.fromisoformat(iso_date)
        return f"{days[d.weekday()]}, {d.day}. {months[d.month]} {d.year}"
    except Exception:
        return iso_date


def render_entry_card(stmt):
    """Rendert eine Eintrags-Karte als HTML."""
    title = stmt["regulatory_project_title"]
    org = stmt["org_name"]
    sending = format_date_de(stmt.get("sending_date"))
    upload = format_date_de(stmt.get("upload_date"))
    summary = stmt.get("summary") or "Kein Beschreibungstext verfügbar."
    recipients = stmt.get("recipients", [])
    fields = stmt.get("fields", [])
    pdf_url = stmt.get("pdf_url", "")
    pdf_pages = stmt.get("pdf_pages", 0)
    stmt_url = build_url(stmt)
    stmt_number = stmt.get("statement_number", "")

    recip_badges = "".join(
        f'<span class="abadge">{r}</span>' for r in recipients
    )
    field_tags = "".join(
        f'<span class="tag">{f["label"]}</span>' for f in fields
    )
    pdf_link = f'<a href="{pdf_url}" target="_blank">↗ PDF herunterladen ({pdf_pages} Seiten)</a>' if pdf_url else '<span style="color:#999">Kein PDF verfügbar</span>'

    return f"""
    <div class="entry-card" data-vorhaben="{title.replace('"', '&quot;')}">
      <div class="row-title">{title}</div>
      <div class="meta-row">
        <div class="mc grow"><strong>Bereitgestellt von</strong>{org}</div>
        <div class="mc fixd"><strong>Datum Stellungnahme</strong>{sending}</div>
        <div class="mc fixd"><strong>Hochgeladen am</strong>{upload}</div>
      </div>
      <div class="meta-row">
        <div class="mc grow"><strong>Adressaten</strong>{recip_badges}</div>
        <div class="mc grow"><strong>Themen</strong>{field_tags}</div>
      </div>
      <div class="row-full"><strong>Inhalt</strong>{summary[:400]}{"..." if len(summary) > 400 else ""}</div>
      <div class="link-row">
        <div class="lc"><a href="{stmt_url}" target="_blank">↗ Lobbyregistereintrag ({stmt_number})</a></div>
        <div class="lc">{pdf_link}</div>
      </div>
    </div>"""


def generate_html(statements, generated_at):
    """Generiert die vollständige HTML-Seite."""

    # Nach Datum gruppieren
    by_date = group_by_date(statements)

    # Alle Regelungsvorhaben für Seitenfilter sammeln
    vorhaben_counts = defaultdict(int)
    for stmt in statements:
        vorhaben_counts[stmt["regulatory_project_title"]] += 1

    # Tagesabschnitte rendern
    day_sections_html = ""
    for iso_date, day_stmts in list(by_date.items())[:100]:  # Max 100 Tage
        # Innerhalb eines Tages nach Priorität sortieren
        day_stmts_sorted = sorted(day_stmts, key=lambda x: x.get("priority", 99))
        day_label = get_weekday_de(iso_date)
        cards = "".join(render_entry_card(s) for s in day_stmts_sorted)
        day_sections_html += f"""
        <div class="day-section" data-date="{iso_date}">
          <div class="day-header">{day_label}</div>
          {cards}
        </div>"""

    # Filterlist für Seitenleiste
    filter_items = "".join(
        f'<li data-v="{v.replace(chr(34), &quot;)}">'
        f'<span>{v}</span><span class="filter-count">{c}</span></li>'
        for v, c in sorted(vorhaben_counts.items(), key=lambda x: -x[1])
    )

    gen_dt = datetime.fromisoformat(generated_at)
    gen_str = gen_dt.strftime("%d. %B %Y, %H:%M Uhr").replace(
        "January", "Januar").replace("February", "Februar").replace(
        "March", "März").replace("April", "April").replace(
        "May", "Mai").replace("June", "Juni").replace(
        "July", "Juli").replace("August", "August").replace(
        "September", "September").replace("October", "Oktober").replace(
        "November", "November").replace("December", "Dezember")

    total = len(statements)

    # Themenfelder für Untertitel
    fields_subtitle = "Energie &amp; Wasserstoff, Klimaschutz, EU-Binnenmarkt, EU-Gesetzgebung, Bundestag, Wettbewerbsrecht, Politisches Leben/Parteien, Sonstige"

    with open("scripts/template.html", "r", encoding="utf-8") as f:
        template = f.read()

    html = template.replace("{{DAY_SECTIONS}}", day_sections_html)
    html = html.replace("{{FILTER_ITEMS}}", filter_items)
    html = html.replace("{{GENERATED_AT}}", gen_str)
    html = html.replace("{{TOTAL_COUNT}}", str(total))
    html = html.replace("{{FIELDS_SUBTITLE}}", fields_subtitle)
    html = html.replace("{{SITE_URL}}", SITE_URL)

    return html


# ── Hauptprogramm ──────────────────────────────────────────────────────────────

def main():
    print("=== Lobbyregister Monitor – Seitengenerierung ===")

    # Daten abrufen
    statements = fetch_all_statements()

    if not statements:
        print("WARNUNG: Keine Einträge gefunden. Seite wird trotzdem generiert.")

    # Speichern
    save_data(statements)

    # HTML generieren
    generated_at = datetime.now().isoformat()
    html = generate_html(statements, generated_at)

    Path("docs").mkdir(exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Seite generiert: docs/index.html ({len(statements)} Einträge)")
    print("=== Fertig ===")


if __name__ == "__main__":
    main()

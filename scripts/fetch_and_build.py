"""
fetch_and_build.py
==================
Ruft Stellungnahmen vom Lobbyregister ab und generiert die HTML-Seite.

Datenquelle: Lobbyregister stellungnahmengutachtenJson-Endpunkt
(öffentlicher JSON-Endpunkt, kein API-Key erforderlich)
"""

import json
import os
import requests
from datetime import datetime, date
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

# ── Konfiguration ──────────────────────────────────────────────────────────────

BASE_URL = "https://www.lobbyregister.bundestag.de/inhalte-der-interessenvertretung/stellungnahmengutachtenJson"
SITE_URL = "https://bmwe-iiia4.github.io/lobbyregister-monitor"
START_DATE = date(2026, 1, 1)

# Filter-Parameter (analog zur Such-URL)
FILTER_PARAMS = {
    "pageSize": "500",
    "sort": "FIRSTPUBLICATION_DESC",
    "filter[circleofrecipients][21. Wahlperiode Bundesregierung|Bundesministerium für Wirtschaft und Energie (BMWE)]": "true",
    "filter[circleofrecipients][21. Wahlperiode Bundestag]": "true",
    "filter[fieldsofinterest][FOI_BUNDESTAG]": "true",
    "filter[fieldsofinterest][FOI_ECONOMY|FOI_ECONOMY_COMPETITION_LAW]": "true",
    "filter[fieldsofinterest][FOI_ENERGY]": "true",
    "filter[fieldsofinterest][FOI_ENVIRONMENT|FOI_ENVIRONMENT_CLIMATE]": "true",
    "filter[fieldsofinterest][FOI_EUROPEAN_UNION|FOI_EU_DOMESTIC_MARKET]": "true",
    "filter[fieldsofinterest][FOI_EUROPEAN_UNION|FOI_EU_LAWS]": "true",
    "filter[fieldsofinterest][FOI_OTHER]": "true",
    "filter[fieldsofinterest][FOI_POLITICAL_PARTIES]": "true",
}

# Themenfeld-Priorität
FIELD_PRIORITY = {
    "Energie": 1, "Erneuerbare Energie": 1, "Strom": 1,
    "Gas": 1, "Wasserstoff": 1,
    "Klimaschutz": 2, "EU-Binnenmarkt": 2, "EU-Gesetzgebung": 2, "Bundestag": 2,
    "Wettbewerbsrecht": 3, "Politisches Leben, Parteien": 3, "Sonstige Interessenbereiche": 3,
}


# ── API-Abfrage ────────────────────────────────────────────────────────────────

def fetch_statements():
    """Ruft Stellungnahmen über den öffentlichen JSON-Endpunkt ab."""
    print("Rufe Daten vom Lobbyregister ab...")
    
    try:
        resp = requests.get(
            BASE_URL,
            params=FILTER_PARAMS,
            timeout=60,
            headers={"Accept": "application/json"}
        )
        print(f"  HTTP Status: {resp.status_code}")
        print(f"  URL: {resp.url[:120]}...")
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"FEHLER beim API-Abruf: {e}")
        raise

    try:
        data = resp.json()
    except Exception as e:
        print(f"FEHLER beim JSON-Parsen: {e}")
        print(f"Antwort (erste 500 Zeichen): {resp.text[:500]}")
        raise

    print(f"  Antwort-Typ: {type(data)}")
    if isinstance(data, dict):
        print(f"  Schlüssel: {list(data.keys())[:10]}")
    elif isinstance(data, list):
        print(f"  Anzahl Einträge: {len(data)}")

    return data


def parse_statements(data):
    """Extrahiert und filtert Stellungnahmen aus der API-Antwort."""
    results = []

    # Verschiedene mögliche Strukturen der Antwort
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Mögliche Schlüssel: 'content', 'results', 'items', 'stellungnahmen'
        for key in ["content", "results", "items", "stellungnahmen", "data"]:
            if key in data:
                items = data[key]
                print(f"  Einträge gefunden unter Schlüssel '{key}': {len(items)}")
                break
        if not items:
            print(f"  Alle Schlüssel: {list(data.keys())}")
            # Versuche direkt das dict als einzelnen Eintrag
            items = [data]

    print(f"  Verarbeite {len(items)} Rohdaten-Einträge...")

    for item in items:
        stmt = parse_single_statement(item)
        if stmt:
            results.append(stmt)

    return results


def parse_single_statement(item):
    """Parst einen einzelnen Stellungnahme-Eintrag."""
    if not isinstance(item, dict):
        return None

    # Datum ermitteln
    upload_date = None
    sending_date = None

    for date_field in ["firstPublicationDate", "uploadDate", "createdAt", "bereitgestelltAm"]:
        val = item.get(date_field, "")
        if val:
            try:
                upload_date = date.fromisoformat(str(val)[:10])
                break
            except ValueError:
                pass

    for date_field in ["sendingDate", "statementDate", "datumDerStellungnahme"]:
        val = item.get(date_field, "")
        if val:
            try:
                sending_date = date.fromisoformat(str(val)[:10])
                break
            except ValueError:
                pass

    # Datum-Filter
    check_date = upload_date or sending_date
    if check_date and check_date < START_DATE:
        return None

    # Organisation
    org = (item.get("providedBy") or item.get("bereitgestelltVon") or
           item.get("organisation") or item.get("name") or "Unbekannte Organisation")
    if isinstance(org, dict):
        org = org.get("name", str(org))

    # Regelungsvorhaben
    title = (item.get("regulatoryProjectTitle") or item.get("regelungsvorhaben") or
             item.get("title") or item.get("titel") or "Kein Titel")

    # Adressaten
    recipients = extract_recipients_from_item(item)

    # Themenfelder
    fields = extract_fields_from_item(item)

    # Priorität
    priority = min(
        (FIELD_PRIORITY.get(f.get("label", ""), 99) for f in fields),
        default=99
    )

    # IDs und Links
    stmt_number = item.get("statementNumber") or item.get("sgId") or item.get("id") or ""
    reg_number = item.get("registerNumber") or item.get("registerNummer") or ""
    pdf_url = item.get("pdfUrl") or item.get("pdf") or item.get("dokumentUrl") or ""
    pdf_pages = item.get("pdfPageCount") or item.get("seitenanzahl") or 0
    summary = item.get("text", {})
    if isinstance(summary, dict):
        summary = summary.get("text", "")
    elif not isinstance(summary, str):
        summary = ""

    return {
        "statement_number": str(stmt_number),
        "register_number": str(reg_number),
        "regulatory_project_title": str(title),
        "org_name": str(org),
        "sending_date": sending_date.isoformat() if sending_date else None,
        "upload_date": upload_date.isoformat() if upload_date else None,
        "pdf_url": str(pdf_url),
        "pdf_pages": int(pdf_pages) if pdf_pages else 0,
        "summary": str(summary)[:600],
        "recipients": recipients,
        "fields": fields,
        "priority": priority,
    }


def extract_recipients_from_item(item):
    """Extrahiert Empfänger aus verschiedenen möglichen Strukturen."""
    recipients = []
    
    for key in ["recipients", "adressaten", "empfaenger", "circleOfRecipients", "recipientGroups"]:
        val = item.get(key)
        if not val:
            continue
        if isinstance(val, list):
            for r in val:
                if isinstance(r, str):
                    recipients.append(r)
                elif isinstance(r, dict):
                    name = (r.get("shortTitle") or r.get("name") or
                            r.get("de") or r.get("title") or "")
                    if name:
                        recipients.append(name)
        elif isinstance(val, str):
            recipients.append(val)

    return list(dict.fromkeys(recipients)) if recipients else ["BMWE"]


def extract_fields_from_item(item):
    """Extrahiert Themenfelder aus verschiedenen möglichen Strukturen."""
    fields = []
    
    for key in ["fieldsOfInterest", "themenfelder", "interessenbereiche", "departments"]:
        val = item.get(key)
        if not val:
            continue
        if isinstance(val, list):
            for f in val:
                if isinstance(f, str):
                    fields.append({"code": f, "label": f})
                elif isinstance(f, dict):
                    label = f.get("de") or f.get("label") or f.get("name") or f.get("title") or ""
                    code = f.get("code") or label
                    if label:
                        fields.append({"code": code, "label": label})

    return fields if fields else [{"code": "FOI_OTHER", "label": "Sonstige Interessenbereiche"}]


# ── HTML-Generierung ───────────────────────────────────────────────────────────

def build_url(stmt):
    sn = stmt.get("statement_number", "")
    rn = stmt.get("register_number", "")
    base = "https://www.lobbyregister.bundestag.de/inhalte-der-interessenvertretung/stellungnahmengutachtensuche"
    if sn and rn:
        return f"{base}/{sn}/{rn}"
    return base


def format_date_de(iso_date):
    if not iso_date:
        return "–"
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return iso_date


def get_weekday_de(iso_date):
    days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    months = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
              "Juli", "August", "September", "Oktober", "November", "Dezember"]
    try:
        d = date.fromisoformat(iso_date)
        return f"{days[d.weekday()]}, {d.day}. {months[d.month]} {d.year}"
    except Exception:
        return iso_date


def render_entry_card(stmt):
    title = stmt["regulatory_project_title"].replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
    org = stmt["org_name"].replace('<', '&lt;').replace('>', '&gt;')
    sending = format_date_de(stmt.get("sending_date"))
    upload = format_date_de(stmt.get("upload_date"))
    summary = stmt.get("summary", "") or "Kein Beschreibungstext verfügbar."
    summary = summary.replace('<', '&lt;').replace('>', '&gt;')
    recipients = stmt.get("recipients", [])
    fields = stmt.get("fields", [])
    pdf_url = stmt.get("pdf_url", "")
    pdf_pages = stmt.get("pdf_pages", 0)
    stmt_url = build_url(stmt)
    stmt_number = stmt.get("statement_number", "")

    recip_badges = "".join(f'<span class="abadge">{r}</span>' for r in recipients)
    field_tags = "".join(f'<span class="tag">{f["label"]}</span>' for f in fields)
    pdf_link = (f'<a href="{pdf_url}" target="_blank">↗ PDF herunterladen ({pdf_pages} Seiten)</a>'
                if pdf_url else '<span style="color:#999">Kein PDF verfügbar</span>')

    return f"""
    <div class="entry-card" data-vorhaben="{title}">
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
      <div class="row-full"><strong>Inhalt</strong>{summary}</div>
      <div class="link-row">
        <div class="lc"><a href="{stmt_url}" target="_blank">↗ Lobbyregistereintrag ({stmt_number})</a></div>
        <div class="lc">{pdf_link}</div>
      </div>
    </div>"""


def generate_html(statements, generated_at):
    by_date = defaultdict(list)
    for stmt in statements:
        key = stmt.get("upload_date") or stmt.get("sending_date") or "unbekannt"
        by_date[key].append(stmt)

    vorhaben_counts = defaultdict(int)
    for stmt in statements:
        vorhaben_counts[stmt["regulatory_project_title"]] += 1

    day_sections_html = ""
    for iso_date, day_stmts in sorted(by_date.items(), reverse=True):
        day_stmts_sorted = sorted(day_stmts, key=lambda x: x.get("priority", 99))
        day_label = get_weekday_de(iso_date)
        cards = "".join(render_entry_card(s) for s in day_stmts_sorted)
        day_sections_html += f"""
        <div class="day-section" data-date="{iso_date}">
          <div class="day-header">{day_label}</div>
          {cards}
        </div>"""

    filter_items = "".join(
        f'<li data-v="{v.replace(chr(34), chr(39))}">'
        f'<span>{v}</span><span class="filter-count">{c}</span></li>'
        for v, c in sorted(vorhaben_counts.items(), key=lambda x: -x[1])
    )

    gen_dt = datetime.fromisoformat(generated_at)
    months_de = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
                 "Juli", "August", "September", "Oktober", "November", "Dezember"]
    gen_str = f"{gen_dt.day}. {months_de[gen_dt.month]} {gen_dt.year}, {gen_dt.strftime('%H:%M')} Uhr"

    fields_subtitle = ("Energie &amp; Wasserstoff, Klimaschutz, EU-Binnenmarkt, EU-Gesetzgebung, "
                       "Bundestag, Wettbewerbsrecht, Politisches Leben/Parteien, Sonstige")

    with open("scripts/template.html", "r", encoding="utf-8") as f:
        template = f.read()

    html = template.replace("{{DAY_SECTIONS}}", day_sections_html)
    html = html.replace("{{FILTER_ITEMS}}", filter_items)
    html = html.replace("{{GENERATED_AT}}", gen_str)
    html = html.replace("{{TOTAL_COUNT}}", str(len(statements)))
    html = html.replace("{{FIELDS_SUBTITLE}}", fields_subtitle)
    html = html.replace("{{SITE_URL}}", SITE_URL)
    return html


# ── Hauptprogramm ──────────────────────────────────────────────────────────────

def main():
    print("=== Lobbyregister Monitor – Seitengenerierung ===")

    raw = fetch_statements()
    statements = parse_statements(raw)

    print(f"Gefilterte Einträge: {len(statements)}")

    if not statements:
        print("WARNUNG: Keine Einträge nach Filterung. Prüfe API-Antwortstruktur.")

    Path("docs").mkdir(exist_ok=True)
    generated_at = datetime.now().isoformat()

    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": generated_at,
            "statements": sorted(statements,
                key=lambda x: (x.get("upload_date") or "0000-00-00"), reverse=True)
        }, f, ensure_ascii=False, indent=2)

    html = generate_html(statements, generated_at)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Seite generiert: docs/index.html ({len(statements)} Einträge)")
    print("=== Fertig ===")


if __name__ == "__main__":
    main()

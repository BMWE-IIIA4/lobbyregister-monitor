"""
fetch_and_build.py
==================
Ruft Stellungnahmen über die offizielle Lobbyregister API V2 ab.

Strategie:
1. Alle Registereinträge per /registerentries mit Cursor-Pagination laden
2. Für jeden Eintrag die Stellungnahmen (statements) clientseitig filtern
   nach: Empfänger (BMWE/Bundestag), Themenfeldern, Datum (ab START_DATE)
3. Gefilterte Stellungnahmen als HTML-Seite rendern
"""

import json
import os
import requests
from datetime import datetime, date
from collections import defaultdict
from pathlib import Path

# ── Konfiguration ──────────────────────────────────────────────────────────────

API_BASE = "https://api.lobbyregister.bundestag.de/rest/v2"
API_KEY = os.environ.get("LOBBYREGISTER_API_KEY", "")

SITE_URL = "https://bmwe-iiia4.github.io/lobbyregister-monitor"
START_DATE = date(2026, 1, 1)

# Empfänger-Schlüsselwörter (werden im Empfängertext gesucht)
TARGET_RECIPIENTS_KEYWORDS = [
    "BMWE", "Bundesministerium für Wirtschaft und Energie",
    "Bundestag",
]

TARGET_FIELD_CODES = {
    "FOI_ENERGY", "FOI_ENERGY_RENEWABLE", "FOI_ENERGY_ELECTRICITY",
    "FOI_ENERGY_GAS", "FOI_ENERGY_HYDROGEN",
    "FOI_ENVIRONMENT_CLIMATE", "FOI_EU_DOMESTIC_MARKET", "FOI_EU_LAWS",
    "FOI_BUNDESTAG", "FOI_ECONOMY_COMPETITION_LAW",
    "FOI_POLITICAL_PARTIES", "FOI_OTHER",
}

FIELD_PRIORITY = {
    "FOI_ENERGY": 1, "FOI_ENERGY_RENEWABLE": 1, "FOI_ENERGY_ELECTRICITY": 1,
    "FOI_ENERGY_GAS": 1, "FOI_ENERGY_HYDROGEN": 1,
    "FOI_ENVIRONMENT_CLIMATE": 2, "FOI_EU_DOMESTIC_MARKET": 2,
    "FOI_EU_LAWS": 2, "FOI_BUNDESTAG": 2,
    "FOI_ECONOMY_COMPETITION_LAW": 3, "FOI_POLITICAL_PARTIES": 3, "FOI_OTHER": 3,
}

FIELD_LABELS = {
    "FOI_ENERGY": "Energie", "FOI_ENERGY_RENEWABLE": "Erneuerbare Energie",
    "FOI_ENERGY_ELECTRICITY": "Strom", "FOI_ENERGY_GAS": "Gas",
    "FOI_ENERGY_HYDROGEN": "Wasserstoff", "FOI_ENVIRONMENT_CLIMATE": "Klimaschutz",
    "FOI_EU_DOMESTIC_MARKET": "EU-Binnenmarkt", "FOI_EU_LAWS": "EU-Gesetzgebung",
    "FOI_BUNDESTAG": "Bundestag", "FOI_ECONOMY_COMPETITION_LAW": "Wettbewerbsrecht",
    "FOI_POLITICAL_PARTIES": "Politisches Leben, Parteien",
    "FOI_OTHER": "Sonstige Interessenbereiche",
}

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/json",
    "Authorization": f"ApiKey {API_KEY}",
})
# API-Key wird zusätzlich als Query-Parameter gesendet (Fallback)
DEFAULT_PARAMS = {"format": "json", "apikey": API_KEY}


# ── Schritt 1: Alle Registereinträge per V2 API laden ──────────────────────────

def fetch_all_register_entries():
    """
    Lädt alle Registereinträge über die V2 API mit Cursor-Pagination.
    Gibt eine Liste von Registernummern zurück.
    """
    register_numbers = []
    cursor = None
    page = 0

    print("Schritt 1: Registereinträge über V2 API laden...")

    while True:
        params = {**DEFAULT_PARAMS}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = SESSION.get(f"{API_BASE}/registerentries", params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  FEHLER Seite {page}: {e}")
            break

        # data ist eine Liste von Registereinträgen
        entries = data if isinstance(data, list) else data.get("results", data.get("registerEntries", []))

        if not entries:
            break

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            reg_num = entry.get("registerNumber", "")
            if reg_num:
                register_numbers.append(reg_num)

        page += 1

        # Cursor für nächste Seite extrahieren
        # V2 API: cursor kommt als Feld in der Antwort oder als letztes Element
        new_cursor = None
        if isinstance(data, dict):
            new_cursor = data.get("cursor")

        # Cursor-basiert: wenn cursor sich nicht ändert, sind wir fertig
        if new_cursor and new_cursor != cursor:
            cursor = new_cursor
        else:
            # Kein neuer Cursor = letzte Seite erreicht
            if cursor is not None:
                break
            # Erster Aufruf ohne Cursor: prüfe ob es überhaupt Pagination gibt
            if not new_cursor:
                break
            cursor = new_cursor

        if page % 10 == 0:
            print(f"  Seite {page}: {len(register_numbers)} Einträge geladen...")

    print(f"  {len(register_numbers)} Registereinträge geladen.")
    return register_numbers


# ── Schritt 2: Einzelabrufe und Stellungnahmen filtern ─────────────────────────

def fetch_and_filter_statements(register_numbers):
    """
    Ruft jeden Registereintrag einzeln ab und extrahiert relevante Stellungnahmen.
    """
    all_statements = []
    total = len(register_numbers)
    skipped = 0

    print(f"Schritt 2: {total} Einträge einzeln abrufen und filtern...")

    for i, reg_num in enumerate(register_numbers):
        try:
            resp = SESSION.get(
                f"{API_BASE}/registerentries/{reg_num}",
                params=DEFAULT_PARAMS,
                timeout=30
            )
            if resp.status_code == 404:
                skipped += 1
                continue
            resp.raise_for_status()
            entry = resp.json()
        except Exception as e:
            if i < 5:
                print(f"  FEHLER {reg_num}: {e}")
            skipped += 1
            continue

        # Stellungnahmen extrahieren
        statements_data = entry.get("statements", {})
        if not isinstance(statements_data, dict):
            continue

        # Prüfe ob Stellungnahmen vorhanden
        stmts_list = statements_data.get("statements", [])
        if not stmts_list and not statements_data.get("statementsPresent", False):
            continue

        org_name = extract_org_name(entry)
        upload_date = extract_upload_date(entry)

        for stmt in stmts_list:
            result = process_statement(stmt, reg_num, org_name, upload_date)
            if result:
                all_statements.append(result)

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{total} abgerufen ({skipped} übersprungen), "
                  f"{len(all_statements)} Stellungnahmen gefunden...")

    print(f"  {len(all_statements)} relevante Stellungnahmen gefunden "
          f"({skipped} Einträge übersprungen).")
    return all_statements


def extract_org_name(entry):
    """Extrahiert den Organisationsnamen aus einem Registereintrag."""
    identity = entry.get("lobbyistIdentity", {})
    if isinstance(identity, dict):
        name = identity.get("name", "")
        if name:
            return name
    return "Unbekannte Organisation"


def extract_upload_date(entry):
    """Extrahiert das Erstveröffentlichungsdatum."""
    acc = entry.get("accountDetails", {})
    if isinstance(acc, dict):
        pub_date = acc.get("firstPublicationDate", "")
        if pub_date:
            try:
                return date.fromisoformat(str(pub_date)[:10])
            except ValueError:
                pass
    return None


def process_statement(stmt, register_number, org_name, upload_date):
    """Prüft eine einzelne Stellungnahme gegen alle Filter."""
    if not isinstance(stmt, dict):
        return None

    # Datum extrahieren
    sending_date = None
    for rg in stmt.get("recipientGroups", []):
        sd = rg.get("sendingDate", "")
        if sd:
            try:
                sending_date = date.fromisoformat(str(sd)[:10])
                break
            except ValueError:
                pass

    # Datumsfilter: mindestens ein Datum muss ab START_DATE sein
    check_date = upload_date or sending_date
    if check_date and check_date < START_DATE:
        return None

    # Empfänger extrahieren und filtern
    recipients = []
    has_target_recipient = False
    for rg in stmt.get("recipientGroups", []):
        recips = rg.get("recipients", {})
        if isinstance(recips, dict):
            # Bundesregierung
            for fg in recips.get("federalGovernment", []):
                dept = fg.get("department", {})
                if isinstance(dept, dict):
                    short = dept.get("shortTitle", "")
                    long_name = dept.get("name", "")
                    display = short or long_name
                    if display:
                        recipients.append(display)
                    # Prüfe ob BMWE
                    combined = f"{short} {long_name}".upper()
                    if "BMWE" in combined or "WIRTSCHAFT" in combined:
                        has_target_recipient = True
            # Bundestag
            for p in recips.get("parliament", []):
                parl_name = ""
                if isinstance(p, dict):
                    parl_name = p.get("de", "") or p.get("name", "")
                elif isinstance(p, str):
                    parl_name = p
                if parl_name:
                    recipients.append("Bundestag")
                    has_target_recipient = True
                    break

    recipients = list(dict.fromkeys(recipients))  # Deduplizieren
    if not has_target_recipient:
        return None

    # Themenfelder extrahieren und filtern
    fields = []
    field_codes_found = set()

    # Felder können auf verschiedenen Ebenen liegen
    foi_sources = [
        stmt.get("fieldsOfInterest", []),
    ]
    for foi_list in foi_sources:
        for f in foi_list:
            if isinstance(f, dict):
                code = f.get("code", "")
                label = FIELD_LABELS.get(code) or f.get("de", "") or code
                if code:
                    fields.append({"code": code, "label": label})
                    field_codes_found.add(code)
            elif isinstance(f, str):
                fields.append({"code": f, "label": FIELD_LABELS.get(f, f)})
                field_codes_found.add(f)

    if not field_codes_found & TARGET_FIELD_CODES:
        return None

    # Priorität berechnen
    priority = min((FIELD_PRIORITY.get(c, 99) for c in field_codes_found), default=99)

    # Text/Beschreibung
    text_obj = stmt.get("text", {})
    summary = text_obj.get("text", "") if isinstance(text_obj, dict) else ""

    # Statement-Nummer und PDF
    stmt_number = str(stmt.get("statementNumber", ""))
    pdf_url = str(stmt.get("pdfUrl", ""))
    pdf_pages = int(stmt.get("pdfPageCount", 0) or 0)

    return {
        "statement_number": stmt_number,
        "register_number": str(register_number),
        "regulatory_project_title": str(stmt.get("regulatoryProjectTitle", "Kein Titel")),
        "org_name": str(org_name),
        "sending_date": sending_date.isoformat() if sending_date else None,
        "upload_date": upload_date.isoformat() if upload_date else None,
        "pdf_url": pdf_url,
        "pdf_pages": pdf_pages,
        "summary": str(summary)[:600],
        "recipients": recipients,
        "fields": fields,
        "priority": priority,
    }


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
    summary = (stmt.get("summary", "") or "Kein Beschreibungstext verfügbar.")
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
    print("=== Lobbyregister Monitor – Seitengenerierung (V2 API) ===")
    print(f"API-Key: {'vorhanden' if API_KEY else 'FEHLT!'}")
    print(f"Startdatum-Filter: ab {START_DATE.isoformat()}")

    register_numbers = fetch_all_register_entries()

    if not register_numbers:
        print("WARNUNG: Keine Registereinträge geladen.")
        print("Prüfe: Ist der API-Key korrekt? (X-Api-Key Header)")

    statements = fetch_and_filter_statements(register_numbers)

    print(f"Relevante Stellungnahmen gesamt: {len(statements)}")
    if not statements:
        print("WARNUNG: Keine Einträge gefunden. Mögliche Ursachen:")
        print("  - API-Key ungültig oder falsch konfiguriert")
        print("  - API-Antwortformat hat sich geändert")
        print("  - Keine Stellungnahmen mit den gewählten Filtern vorhanden")

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

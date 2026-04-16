"""
fetch_and_build.py
==================
Ruft Stellungnahmen über die offizielle Lobbyregister API V2 ab.

Strategie:
1. Alle Registereinträge per /registerentries mit Cursor-Pagination laden
2. Für jeden Eintrag prüfen:
   a) Hat er relevante Themenfelder? (activitiesAndInterests.fieldsOfInterest)
   b) Hat er Stellungnahmen? (statements.statementsPresent)
   c) Sind Stellungnahmen an BMWE/BMWK/Bundestag adressiert?
   d) Sind sie ab START_DATE?
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

# Empfänger: Schlüsselwörter im shortTitle/title der Bundesregierung
TARGET_DEPT_KEYWORDS = ["BMWE", "BMWK", "Wirtschaft"]

# V2-API Feldcodes (aktualisiert für R2.22)
TARGET_FIELD_CODES = {
    "FOI_ENERGY_OVERALL", "FOI_ENERGY_RENEWABLE", "FOI_ENERGY_FOSSILE",
    "FOI_ENERGY_NET", "FOI_ENERGY_NUCLEAR", "FOI_ENERGY_OTHER",
    "FOI_ENERGY_ELECTRICITY", "FOI_ENERGY_GAS", "FOI_ENERGY_HYDROGEN",
    "FOI_ENERGY",
    "FOI_ENVIRONMENT_CLIMATE",
    "FOI_EU_DOMESTIC_MARKET", "FOI_EU_LAWS",
    "FOI_BUNDESTAG",
    "FOI_ECONOMY_COMPETITION_LAW",
    "FOI_POLITICAL_PARTIES",
    "FOI_OTHER",
}

FIELD_PRIORITY = {
    "FOI_ENERGY_OVERALL": 1, "FOI_ENERGY_RENEWABLE": 1, "FOI_ENERGY_FOSSILE": 1,
    "FOI_ENERGY_NET": 1, "FOI_ENERGY_NUCLEAR": 1, "FOI_ENERGY_OTHER": 1,
    "FOI_ENERGY_ELECTRICITY": 1, "FOI_ENERGY_GAS": 1, "FOI_ENERGY_HYDROGEN": 1,
    "FOI_ENERGY": 1,
    "FOI_ENVIRONMENT_CLIMATE": 2, "FOI_EU_DOMESTIC_MARKET": 2,
    "FOI_EU_LAWS": 2, "FOI_BUNDESTAG": 2,
    "FOI_ECONOMY_COMPETITION_LAW": 3, "FOI_POLITICAL_PARTIES": 3, "FOI_OTHER": 3,
}

FIELD_LABELS = {
    "FOI_ENERGY_OVERALL": "Energie (allgemein)", "FOI_ENERGY_RENEWABLE": "Erneuerbare Energie",
    "FOI_ENERGY_FOSSILE": "Fossile Energie", "FOI_ENERGY_NET": "Energienetze",
    "FOI_ENERGY_NUCLEAR": "Atomenergie", "FOI_ENERGY_OTHER": "Energie (sonstige)",
    "FOI_ENERGY_ELECTRICITY": "Strom", "FOI_ENERGY_GAS": "Gas",
    "FOI_ENERGY_HYDROGEN": "Wasserstoff", "FOI_ENERGY": "Energie",
    "FOI_ENVIRONMENT_CLIMATE": "Klimaschutz",
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
DEFAULT_PARAMS = {"format": "json", "apikey": API_KEY}


# ── Schritt 1: Alle Registereinträge per V2 API laden ──────────────────────────

def fetch_all_register_entries():
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

        entries = data if isinstance(data, list) else data.get("results", data.get("registerEntries", []))
        if not entries:
            break

        for entry in entries:
            if isinstance(entry, dict):
                reg_num = entry.get("registerNumber", "")
                if reg_num:
                    register_numbers.append(reg_num)

        page += 1
        new_cursor = data.get("cursor") if isinstance(data, dict) else None

        if new_cursor and new_cursor != cursor:
            cursor = new_cursor
        else:
            if cursor is not None:
                break
            if not new_cursor:
                break
            cursor = new_cursor

        if page % 10 == 0:
            print(f"  Seite {page}: {len(register_numbers)} Einträge geladen...")

    print(f"  {len(register_numbers)} Registereinträge geladen.")
    return register_numbers


# ── Schritt 2: Einzelabrufe und Stellungnahmen filtern ─────────────────────────

def fetch_and_filter_statements(register_numbers):
    all_statements = []
    total = len(register_numbers)
    skipped = 0
    no_statements = 0
    no_relevant_fields = 0

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

        # Themenfelder auf Registereintrag-Ebene prüfen (Vorfilter)
        entry_fields = extract_entry_fields(entry)
        entry_field_codes = {f["code"] for f in entry_fields}
        if not entry_field_codes & TARGET_FIELD_CODES:
            no_relevant_fields += 1
            continue

        # Stellungnahmen prüfen
        statements_data = entry.get("statements", {})
        if not isinstance(statements_data, dict):
            no_statements += 1
            continue
        if not statements_data.get("statementsPresent", False):
            no_statements += 1
            continue
        stmts_list = statements_data.get("statements", [])
        if not stmts_list:
            no_statements += 1
            continue

        org_name = extract_org_name(entry)
        upload_date = extract_upload_date(entry)

        for stmt in stmts_list:
            result = process_statement(stmt, reg_num, org_name, upload_date, entry_fields)
            if result:
                all_statements.append(result)

        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{total}: {len(all_statements)} SN, "
                  f"{no_relevant_fields} kein Thema, {no_statements} keine SN, "
                  f"{skipped} Fehler")

    print(f"  {len(all_statements)} relevante Stellungnahmen gefunden.")
    print(f"  ({no_relevant_fields} ohne Themenfeld, "
          f"{no_statements} ohne Stellungnahmen, {skipped} Fehler)")
    return all_statements


def extract_entry_fields(entry):
    ai = entry.get("activitiesAndInterests", {})
    if not isinstance(ai, dict):
        return []
    foi_list = ai.get("fieldsOfInterest", [])
    fields = []
    for f in foi_list:
        if isinstance(f, dict):
            code = f.get("code", "")
            label = FIELD_LABELS.get(code) or f.get("de", "") or code
            if code:
                fields.append({"code": code, "label": label})
    return fields


def extract_org_name(entry):
    identity = entry.get("lobbyistIdentity", {})
    if isinstance(identity, dict):
        name = identity.get("name", "")
        if name:
            return name
    return "Unbekannte Organisation"


def extract_upload_date(entry):
    acc = entry.get("accountDetails", {})
    if isinstance(acc, dict):
        pub_date = acc.get("firstPublicationDate", "")
        if pub_date:
            try:
                return date.fromisoformat(str(pub_date)[:10])
            except ValueError:
                pass
    return None


def process_statement(stmt, register_number, org_name, upload_date, entry_fields):
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

    # Datumsfilter
    check_date = sending_date or upload_date
    if check_date and check_date < START_DATE:
        return None

    # Empfänger extrahieren und filtern
    recipients = []
    has_target_recipient = False
    for rg in stmt.get("recipientGroups", []):
        recips = rg.get("recipients", {})
        if not isinstance(recips, dict):
            continue
        # Bundesregierung
        for fg in recips.get("federalGovernment", []):
            dept = fg.get("department", {})
            if isinstance(dept, dict):
                short = dept.get("shortTitle", "")
                title = dept.get("title", "")
                display = short or title
                if display:
                    recipients.append(display)
                combined = f"{short} {title}".upper()
                for kw in TARGET_DEPT_KEYWORDS:
                    if kw.upper() in combined:
                        has_target_recipient = True
                        break
        # Bundestag
        for p in recips.get("parliament", []):
            if isinstance(p, dict):
                parl_name = p.get("de", "") or p.get("name", "")
            elif isinstance(p, str):
                parl_name = p
            else:
                continue
            if parl_name:
                recipients.append("Bundestag")
                has_target_recipient = True
                break

    recipients = list(dict.fromkeys(recipients))
    if not has_target_recipient:
        return None

    # Themenfelder vom Registereintrag
    field_codes = {f["code"] for f in entry_fields}
    relevant_fields = [f for f in entry_fields if f["code"] in TARGET_FIELD_CODES]
    if not relevant_fields:
        relevant_fields = entry_fields[:3]

    priority = min((FIELD_PRIORITY.get(c, 99) for c in field_codes if c in FIELD_PRIORITY), default=99)

    text_obj = stmt.get("text", {})
    summary = text_obj.get("text", "") if isinstance(text_obj, dict) else ""

    project_number = str(stmt.get("regulatoryProjectNumber", ""))
    pdf_url = str(stmt.get("pdfUrl", ""))
    pdf_pages = int(stmt.get("pdfPageCount", 0) or 0)

    return {
        "statement_number": project_number,
        "register_number": str(register_number),
        "regulatory_project_title": str(stmt.get("regulatoryProjectTitle", "Kein Titel")),
        "org_name": str(org_name),
        "sending_date": sending_date.isoformat() if sending_date else None,
        "upload_date": upload_date.isoformat() if upload_date else None,
        "pdf_url": pdf_url,
        "pdf_pages": pdf_pages,
        "summary": str(summary)[:600],
        "recipients": recipients,
        "fields": relevant_fields,
        "priority": priority,
    }


# ── HTML-Generierung ───────────────────────────────────────────────────────────

def build_url(stmt):
    rn = stmt.get("register_number", "")
    base = "https://www.lobbyregister.bundestag.de/suche"
    if rn:
        return f"{base}/{rn}"
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
    reg_number = stmt.get("register_number", "")

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
        <div class="lc"><a href="{stmt_url}" target="_blank">↗ Lobbyregistereintrag ({reg_number})</a></div>
        <div class="lc">{pdf_link}</div>
      </div>
    </div>"""


def generate_html(statements, generated_at):
    by_date = defaultdict(list)
    for stmt in statements:
        key = stmt.get("sending_date") or stmt.get("upload_date") or "unbekannt"
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

    statements = fetch_and_filter_statements(register_numbers)

    print(f"Relevante Stellungnahmen gesamt: {len(statements)}")

    Path("docs").mkdir(exist_ok=True)
    generated_at = datetime.now().isoformat()

    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": generated_at,
            "statements": sorted(statements,
                key=lambda x: (x.get("sending_date") or x.get("upload_date") or "0000-00-00"),
                reverse=True)
        }, f, ensure_ascii=False, indent=2)

    html = generate_html(statements, generated_at)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Seite generiert: docs/index.html ({len(statements)} Einträge)")
    print("=== Fertig ===")


if __name__ == "__main__":
    main()

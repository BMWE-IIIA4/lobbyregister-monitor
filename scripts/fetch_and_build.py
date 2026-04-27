"""
fetch_and_build.py
==================
Ruft Stellungnahmen ueber die offizielle Lobbyregister API V2 ab.

INKREMENTELLER ABRUF + BERLINER ZEITZONE FÜR TIMESTAMPS
"""

import json
import os
import re
import requests
from datetime import datetime, date
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Konfiguration ──────────────────────────────────────────────────────────────

API_BASE = "https://api.lobbyregister.bundestag.de/rest/v2"
API_KEY = os.environ.get("LOBBYREGISTER_API_KEY", "")

SITE_URL = "https://lobbyregister-bot.de"
START_DATE = date(2026, 1, 1)
BERLIN_TZ = ZoneInfo("Europe/Berlin")

TARGET_DEPT_KEYWORDS = ["BMWE", "BMWK", "Wirtschaft", "BKAmt", "Kanzleramt", "BMUKN", "BMUV", "Umwelt", "BMF", "Finanzen"]

TARGET_FIELD_CODES = {
    "FOI_ENERGY_OVERALL", "FOI_ENERGY_RENEWABLE", "FOI_ENERGY_FOSSILE",
    "FOI_ENERGY_NET", "FOI_ENERGY_NUCLEAR", "FOI_ENERGY_OTHER",
    "FOI_ENERGY_ELECTRICITY", "FOI_ENERGY_GAS", "FOI_ENERGY_HYDROGEN",
    "FOI_ENERGY", "FOI_ENVIRONMENT_CLIMATE",
    "FOI_EU_DOMESTIC_MARKET", "FOI_EU_LAWS", "FOI_BUNDESTAG",
    "FOI_ECONOMY_COMPETITION_LAW", "FOI_POLITICAL_PARTIES", "FOI_OTHER",
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

# API-Session: sendet API-Key nur an die Lobbyregister-API
API_SESSION = requests.Session()
API_SESSION.headers.update({
    "Accept": "application/json",
    "Authorization": f"ApiKey {API_KEY}",
})
DEFAULT_PARAMS = {"format": "json", "apikey": API_KEY}

# Separate Session fuer Nicht-API-Requests (PDF-URLs auf bundestag.de)
WEB_SESSION = requests.Session()
WEB_SESSION.headers.update({
    "Accept": "text/html",
    "User-Agent": "LobbyregisterMonitor/1.0",
})

# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def extract_sg_number(pdf_url):
    if not pdf_url: return ""
    match = re.search(r'(SG\d+)', pdf_url)
    return match.group(1) if match else ""

def build_statement_url(sg_number):
    if not sg_number: return ""
    return f"https://www.lobbyregister.bundestag.de/inhalte-der-interessenvertretung/stellungnahmengutachtensuche/{sg_number}"

def fetch_real_pdf_url(page_url):
    """Nutzt WEB_SESSION (ohne API-Key), da die Anfrage an bundestag.de geht."""
    if not page_url: return ""
    try:
        resp = WEB_SESSION.get(page_url, timeout=10)
        if resp.status_code == 200:
            match = re.search(r'href="([^"]+\.pdf)"', resp.text)
            if match:
                path = match.group(1)
                return f"https://www.lobbyregister.bundestag.de{path}" if path.startswith('/') else path
    except Exception:
        pass
    return page_url

def calc_delay_days(sending, upload):
    try:
        if not sending or not upload:
            return ""
        d1 = date.fromisoformat(sending)
        d2 = date.fromisoformat(upload)
        diff = (d2 - d1).days
        if diff > 0:
            return f" (+{diff} Tage)"
        return ""
    except:
        return ""

# ── Vorherige Daten laden ──────────────────────────────────────────────────────

def load_previous_data():
    """Laedt data.json aus dem Cache (vorheriger Lauf).
    Gibt (statements_list, known_register_numbers_set) zurueck."""
    data_path = Path("docs/data.json")
    if not data_path.exists():
        return [], set()

    try:
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
        statements = data.get("statements", [])
        # Alle Registernummern extrahieren, fuer die wir bereits Daten haben
        known_rns = {s["register_number"] for s in statements if s.get("register_number")}
        return statements, known_rns
    except Exception as e:
        print(f"  Vorherige Daten nicht lesbar: {e}")
        return [], set()

# ── Schritt 1: Alle Registernummern laden (schnell) ────────────────────────────

def fetch_all_register_entries():
    register_numbers = []
    cursor = None
    page = 0

    print("Schritt 1: Registernummern ueber V2 API laden...")

    while True:
        params = {**DEFAULT_PARAMS}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = API_SESSION.get(f"{API_BASE}/registerentries", params=params, timeout=60)
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

        # Cursor-Pagination: saubere Abbruchlogik
        new_cursor = data.get("cursor") if isinstance(data, dict) else None
        if not new_cursor:
            break
        if new_cursor == cursor:
            break
        cursor = new_cursor

        if page % 10 == 0:
            print(f"  Seite {page}: {len(register_numbers)} Eintraege geladen...")

    print(f"  {len(register_numbers)} Registernummern geladen.")
    return register_numbers

# ── Schritt 2: Nur NEUE Eintraege einzeln abrufen ─────────────────────────────

def fetch_and_filter_statements(register_numbers):
    """Ruft einzelne Registereintraege ab und extrahiert relevante Stellungnahmen."""
    all_statements = []
    seen_keys = set()  # Deduplizierung
    total = len(register_numbers)
    skipped = 0
    no_statements = 0
    no_relevant_fields = 0
    duplicates = 0

    print(f"Schritt 2: {total} neue Eintraege einzeln abrufen und filtern...")

    for i, reg_num in enumerate(register_numbers):
        try:
            resp = API_SESSION.get(f"{API_BASE}/registerentries/{reg_num}", params=DEFAULT_PARAMS, timeout=30)
            if resp.status_code == 404:
                skipped += 1
                continue
            resp.raise_for_status()
            entry = resp.json()
        except Exception as e:
            if i < 5: print(f"  FEHLER {reg_num}: {e}")
            skipped += 1
            continue

        # Pre-Filter: Orga-Themenfelder pruefen
        entry_fields = extract_entry_fields(entry)
        entry_field_codes = {f["code"] for f in entry_fields}
        if not entry_field_codes & TARGET_FIELD_CODES:
            no_relevant_fields += 1
            continue

        statements_data = entry.get("statements", {})
        if not isinstance(statements_data, dict) or not statements_data.get("statementsPresent", False):
            no_statements += 1
            continue
        stmts_list = statements_data.get("statements", [])
        if not stmts_list:
            no_statements += 1
            continue

        org_name = extract_org_name(entry)
        upload_date = extract_upload_date(entry)
        details_page_url = extract_details_page_url(entry)
        rp_lookup = build_rp_lookup(entry)

        for stmt in stmts_list:
            result = process_statement(stmt, reg_num, org_name, upload_date,
                                       entry_fields, details_page_url, rp_lookup)
            if result:
                # Deduplizierung
                dedup_key = None
                if result.get("sg_number"):
                    dedup_key = result["sg_number"]
                else:
                    dedup_key = (result["register_number"],
                                result["regulatory_project_title"],
                                result.get("sending_date", ""))

                if dedup_key in seen_keys:
                    duplicates += 1
                    continue

                seen_keys.add(dedup_key)
                all_statements.append(result)

        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{total}: {len(all_statements)} SN, {no_relevant_fields} kein Thema, "
                  f"{no_statements} keine SN, {skipped} Fehler, {duplicates} Duplikate")

    print(f"  {len(all_statements)} neue relevante Stellungnahmen gefunden.")
    if duplicates:
        print(f"  ({duplicates} Duplikate entfernt)")
    return all_statements


def extract_entry_fields(entry):
    ai = entry.get("activitiesAndInterests", {})
    if not isinstance(ai, dict): return []
    foi_list = ai.get("fieldsOfInterest", [])
    fields = []
    for f in foi_list:
        if isinstance(f, dict):
            code = f.get("code", "")
            label = FIELD_LABELS.get(code) or f.get("de", "") or code
            if code: fields.append({"code": code, "label": label})
    return fields

def extract_org_name(entry):
    identity = entry.get("lobbyistIdentity", {})
    return identity.get("name", "Unbekannte Organisation") if isinstance(identity, dict) else "Unbekannte Organisation"

def extract_upload_date(entry):
    acc = entry.get("accountDetails", {})
    if isinstance(acc, dict):
        pub_date = acc.get("lastUpdateDate", "")
        if pub_date:
            try: return date.fromisoformat(str(pub_date)[:10])
            except ValueError: pass
    return None

def extract_details_page_url(entry):
    details = entry.get("registerEntryDetails", {})
    return details.get("detailsPageUrl", "") if isinstance(details, dict) else ""

def build_rp_lookup(entry):
    rp_data = entry.get("regulatoryProjects", {})
    if not isinstance(rp_data, dict): return {}
    rp_list = rp_data.get("regulatoryProjects", [])
    lookup = {}
    for rp in rp_list:
        if isinstance(rp, dict):
            num = rp.get("regulatoryProjectNumber", "")
            desc = rp.get("description", "")
            foi_list = rp.get("fieldsOfInterest", [])
            fields = []
            for f in foi_list:
                if isinstance(f, dict):
                    code = f.get("code", "")
                    label = FIELD_LABELS.get(code) or f.get("de", "") or code
                    if code: fields.append({"code": code, "label": label})
            if num:
                lookup[num] = {"description": desc, "fields": fields}
    return lookup

def process_statement(stmt, register_number, org_name, upload_date,
                      entry_fields, details_page_url, rp_lookup):
    if not isinstance(stmt, dict): return None

    sending_date = None
    for rg in stmt.get("recipientGroups", []):
        sd = rg.get("sendingDate", "")
        if sd:
            try:
                sending_date = date.fromisoformat(str(sd)[:10])
                break
            except ValueError: pass

    check_date = sending_date or upload_date
    if check_date and check_date < START_DATE: return None

    recipients = []
    has_target_recipient = False
    for rg in stmt.get("recipientGroups", []):
        recips = rg.get("recipients", {})
        if not isinstance(recips, dict): continue
        for fg in recips.get("federalGovernment", []):
            dept = fg.get("department", {})
            if isinstance(dept, dict):
                short = dept.get("shortTitle", "")
                title = dept.get("title", "")
                display = short or title
                if display: recipients.append(display)
                combined = f"{short} {title}".upper()
                for kw in TARGET_DEPT_KEYWORDS:
                    if kw.upper() in combined:
                        has_target_recipient = True
                        break
        for p in recips.get("parliament", []):
            if isinstance(p, dict): parl_name = p.get("de", "") or p.get("name", "")
            elif isinstance(p, str): parl_name = p
            else: continue
            if parl_name:
                recipients.append("Bundestag")
                has_target_recipient = True
                break

    recipients = list(dict.fromkeys(recipients))
    if not has_target_recipient: return None

    rp_number = stmt.get("regulatoryProjectNumber", "")
    rp_info = rp_lookup.get(rp_number, {})
    stmt_fields = rp_info.get("fields", [])
    summary = rp_info.get("description", "")

    if not stmt_fields:
        foi_list = stmt.get("fieldsOfInterest", [])
        for f in foi_list:
            if isinstance(f, dict):
                code = f.get("code", "")
                label = FIELD_LABELS.get(code) or f.get("de", "") or code
                if code: stmt_fields.append({"code": code, "label": label})

    if stmt_fields:
        stmt_field_codes = {f["code"] for f in stmt_fields}
        if not stmt_field_codes & TARGET_FIELD_CODES: return None
        relevant_fields = [f for f in stmt_fields if f["code"] in TARGET_FIELD_CODES]
        display_fields = relevant_fields if relevant_fields else stmt_fields[:3]
        priority_codes = stmt_field_codes
    else:
        display_fields = [f for f in entry_fields if f["code"] in TARGET_FIELD_CODES]
        if not display_fields: display_fields = entry_fields[:3]
        priority_codes = {f["code"] for f in entry_fields}

    priority = min((FIELD_PRIORITY.get(c, 99) for c in priority_codes if c in FIELD_PRIORITY), default=99)

    page_url = str(stmt.get("pdfUrl", ""))
    pdf_url = fetch_real_pdf_url(page_url)
    pdf_pages = int(stmt.get("pdfPageCount", 0) or 0)
    sg_number = extract_sg_number(pdf_url)
    statement_url = build_statement_url(sg_number)

    return {
        "register_number": str(register_number),
        "statement_number": sg_number,
        "org_name": str(org_name),
        "org_url": details_page_url,
        "regulatory_project_title": str(stmt.get("regulatoryProjectTitle", "Kein Titel")),
        "sending_date": sending_date.isoformat() if sending_date else None,
        "upload_date": upload_date.isoformat() if upload_date else None,
        "pdf_url": pdf_url,
        "pdf_pages": pdf_pages,
        "sg_number": sg_number,
        "statement_url": statement_url,
        "summary": summary,
        "recipients": recipients,
        "fields": display_fields,
        "priority": priority,
    }

# ── Merge & Deduplizierung ────────────────────────────────────────────────────

def merge_statements(previous, new):
    """Merged vorherige und neue Stellungnahmen, entfernt Duplikate.
    Neue Eintraege ueberschreiben vorherige bei gleichem Key."""
    seen = set()
    merged = []

    # Neue zuerst einfuegen (haben Vorrang)
    for stmt in new:
        key = stmt.get("sg_number") or (stmt["register_number"],
              stmt["regulatory_project_title"], stmt.get("sending_date", ""))
        if key not in seen:
            seen.add(key)
            merged.append(stmt)

    # Vorherige nur wenn kein Duplikat
    for stmt in previous:
        key = stmt.get("sg_number") or (stmt["register_number"],
              stmt["regulatory_project_title"], stmt.get("sending_date", ""))
        if key not in seen:
            seen.add(key)
            merged.append(stmt)

    return merged

# ── HTML-Generierung ───────────────────────────────────────────────────────────

def format_date_de(iso_date):
    if not iso_date: return "\u2013"
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return iso_date

def get_weekday_de(iso_date):
    days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    months = ["", "Januar", "Februar", "M\u00e4rz", "April", "Mai", "Juni",
              "Juli", "August", "September", "Oktober", "November", "Dezember"]
    try:
        d = date.fromisoformat(iso_date)
        return f"{days[d.weekday()]}, {d.day}. {months[d.month]} {d.year}"
    except Exception:
        return iso_date

def render_entry_card(stmt):
    title = stmt["regulatory_project_title"].replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
    org = stmt["org_name"].replace('<', '&lt;').replace('>', '&gt;')
    org_url = stmt.get("org_url", "")
    sending = format_date_de(stmt.get("sending_date"))
    upload_raw = stmt.get("upload_date")
    sending_raw = stmt.get("sending_date")

    delay = calc_delay_days(sending_raw, upload_raw)
    upload = format_date_de(upload_raw) + delay

    summary = (stmt.get("summary", "") or "Keine Beschreibung verf\u00fcgbar.")
    summary = re.sub(r'<(?!/?b>)', '&lt;', summary).replace('>', '&gt;').replace('<b&gt;', '<b>').replace('</b&gt;', '</b>')

    recipients = stmt.get("recipients", [])
    fields = stmt.get("fields", [])
    pdf_url = stmt.get("pdf_url", "")
    pdf_pages = stmt.get("pdf_pages", 0)
    sg_number = stmt.get("sg_number", "")
    statement_url = stmt.get("statement_url", "")

    org_html = f'<a href="{org_url}" target="_blank" style="color:#004B87;text-decoration:none">{org}</a>' if org_url else org
    recip_badges = "".join(f'<span class="abadge">{r}</span>' for r in recipients)
    field_tags = "".join(f'<span class="tag">{f["label"]}</span>' for f in fields)

    sg_label = f" ({sg_number})" if sg_number else ""
    stmt_link = f'<a href="{statement_url}" target="_blank">\u2197 Stellungnahme im Lobbyregister{sg_label}</a>' if statement_url else ''
    pdf_link = f'<a href="{pdf_url}" target="_blank">\u2197 PDF herunterladen ({pdf_pages} Seiten)</a>' if pdf_url else '<span style="color:#999">Kein PDF</span>'

    return (
        f'<div class="entry-card" data-vorhaben="{title}">'
        f'<div class="row-title">{title}</div>'
        f'<div class="meta-row">'
        f'<div class="mc grow"><strong>Bereitgestellt von</strong>{org_html}</div>'
        f'<div class="mc fixd"><strong>Datum Stellungnahme</strong>{sending}</div>'
        f'<div class="mc fixd"><strong>Hochgeladen am</strong>{upload}</div>'
        f'</div>'
        f'<div class="meta-row two-col">'
        f'<div class="mc half"><strong>Adressaten</strong>{recip_badges}</div>'
        f'<div class="mc half"><strong>Themenfelder der Stellungnahme</strong>{field_tags}</div>'
        f'</div>'
        f'<div class="row-full"><span class="row-label">Inhalt</span>{summary}</div>'
        f'<div class="link-row">'
        f'<div class="lc">{stmt_link}</div>'
        f'<div class="lc">{pdf_link}</div>'
        f'</div></div>'
    )

def generate_html(statements, generated_at):
    by_date = defaultdict(list)
    for stmt in statements:
        # NUR upload_date verwenden (nicht sending_date als Fallback!)
        key = stmt.get("upload_date") or "unbekannt"
        by_date[key].append(stmt)

    vorhaben_counts = defaultdict(int)
    for stmt in statements:
        vorhaben_counts[stmt["regulatory_project_title"]] += 1

    day_sections_html = ""
    for iso_date, day_stmts in sorted(by_date.items(), reverse=True):
        day_stmts_sorted = sorted(day_stmts, key=lambda x: x.get("priority", 99))
        day_label = get_weekday_de(iso_date)
        cards = "".join(render_entry_card(s) for s in day_stmts_sorted)
        day_sections_html += (
            f'<div class="day-section" data-date="{iso_date}">'
            f'<div class="day-header">{day_label}</div>'
            f'{cards}</div>'
        )

    filter_items = "".join(
        f'<li data-v="{v.replace(chr(34), chr(39))}">'
        f'<span>{v}</span><span class="filter-count">{c}</span></li>'
        for v, c in sorted(vorhaben_counts.items(), key=lambda x: -x[1])
    )

    # BERLINER ZEIT für den Timestamp!
    gen_dt = datetime.fromisoformat(generated_at).astimezone(BERLIN_TZ)
    months_de = ["", "Januar", "Februar", "M\u00e4rz", "April", "Mai", "Juni",
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
    print("=== Lobbyregister Monitor - Seitengenerierung (V2 API, inkrementell) ===")

    # Vorherige Daten laden (aus Cache)
    previous_statements, known_register_numbers = load_previous_data()
    if previous_statements:
        print(f"Cache: {len(previous_statements)} vorherige Eintraege aus "
              f"{len(known_register_numbers)} Registernummern geladen.")
    else:
        print("Kein Cache vorhanden - vollstaendiger Erstabruf.")

    # Schritt 1: Alle Registernummern laden (nur die Liste, schnell)
    all_register_numbers = fetch_all_register_entries()
    if not all_register_numbers:
        print("WARNUNG: Keine Registereintraege geladen.")

    # Neue Registernummern bestimmen
    new_register_numbers = [rn for rn in all_register_numbers if rn not in known_register_numbers]
    skipped_count = len(all_register_numbers) - len(new_register_numbers)

    print(f"\nRegisternummern gesamt: {len(all_register_numbers)}")
    print(f"  Bereits bekannt (uebersprungen): {skipped_count}")
    print(f"  Neu zu pruefen: {len(new_register_numbers)}")

    # Schritt 2: Nur neue Eintraege einzeln abrufen
    if new_register_numbers:
        new_statements = fetch_and_filter_statements(new_register_numbers)
    else:
        new_statements = []
        print("\nKeine neuen Eintraege - nur HTML-Aktualisierung.")

    # Merge: vorherige + neue, mit Deduplizierung
    all_statements = merge_statements(previous_statements, new_statements)
    all_statements.sort(
        key=lambda x: (x.get("upload_date") or x.get("sending_date") or "0000-00-00"),
        reverse=True
    )

    print(f"\nErgebnis: {len(all_statements)} Stellungnahmen gesamt "
          f"({len(new_statements)} neu, {len(previous_statements)} aus Cache)")

    # BERLINER ZEIT für generated_at
    generated_at = datetime.now(BERLIN_TZ).isoformat()
    
    # Speichern
    Path("docs").mkdir(exist_ok=True)

    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": generated_at,
            "statements": sorted(
                all_statements,
                key=lambda x: (x.get("upload_date") or x.get("sending_date") or "0000-00-00"),
                reverse=True
            )
        }, f, ensure_ascii=False, indent=2)

    html = generate_html(all_statements, generated_at)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Seite generiert: docs/index.html ({len(all_statements)} Eintraege)")

if __name__ == "__main__":
    main()

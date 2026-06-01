"""
fetch_and_build.py
==================
Ruft Stellungnahmen ueber die offizielle Lobbyregister API V2 ab.
Vollabgleich: alle Organisationen werden bei jedem Lauf geprueft, die
Deduplizierung erfolgt auf Stellungnahme-Ebene (SG-Nummer). Bestehende
Eintraege bleiben stabil, nur neue Stellungnahmen werden ergaenzt.
Speicherung in docs/data.json. HTML-Generierung erfolgt in gemini_enrich.py.
"""

import json
import os
import re
import requests
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Konfiguration ──────────────────────────────────────────────────────────────

API_BASE = "https://api.lobbyregister.bundestag.de/rest/v2"
API_KEY = os.environ.get("LOBBYREGISTER_API_KEY", "")

START_DATE = date(2026, 1, 1)
BERLIN_TZ = ZoneInfo("Europe/Berlin")

TARGET_DEPT_KEYWORDS = [
    "BMWE", "BMWK",  # BMWK = früherer Name des Ministeriums, Alteinträge im Register möglich
    "Wirtschaft", "BKAmt", "Kanzleramt", "BMUKN", "BMUV", "Umwelt", "BMF", "Finanzen"
]

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

def sg_to_upload_date(sg_number):
    """Leitet das Bereitstellungsdatum ('bereitgestellt am') aus der SG-Nummer ab.

    Format der SG-Nummer: SG + JJMMTT + 4-stelliger Zaehler.
    Beispiel: SG2605220018 -> 2026-05-22.
    Dieses Datum ist pro Stellungnahme eindeutig und stabil – im Gegensatz zum
    organisationsweiten lastUpdateDate. Gibt ein date-Objekt oder None zurueck.
    """
    if not sg_number or not sg_number.startswith("SG"):
        return None
    digits = sg_number[2:]
    if len(digits) < 6:
        return None
    try:
        yy = int(digits[0:2])
        mm = int(digits[2:4])
        dd = int(digits[4:6])
        return date(2000 + yy, mm, dd)
    except ValueError:
        return None

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

# ── Vorherige Daten laden ──────────────────────────────────────────────────────

def load_previous_data():
    """Laedt data.json aus dem Cache (vorheriger Lauf).

    Stellt dabei einmalig das upload_date bestehender Eintraege auf das aus der
    SG-Nummer abgeleitete Bereitstellungsdatum um (sofern abweichend). So werden
    auch Altbestaende auf die korrekte, pro-Stellungnahme stabile Datumsquelle
    migriert. Gibt (statements_list, known_register_numbers_set) zurueck.
    """
    data_path = Path("docs/data.json")
    if not data_path.exists():
        return [], set()

    try:
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
        statements = data.get("statements", [])

        # Einmalige Migration des upload_date auf das SG-Datum
        migrated = 0
        for s in statements:
            sg_date = sg_to_upload_date(s.get("sg_number", ""))
            if sg_date:
                sg_iso = sg_date.isoformat()
                if s.get("upload_date") != sg_iso:
                    s["upload_date"] = sg_iso
                    migrated += 1
        if migrated:
            print(f"  Migration: {migrated} upload_date-Werte auf SG-Datum umgestellt.")

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

# ── Schritt 2: Alle Eintraege einzeln abrufen ─────────────────────────────────

def fetch_and_filter_statements(register_numbers):
    """Ruft einzelne Registereintraege ab und extrahiert relevante Stellungnahmen."""
    all_statements = []
    seen_keys = set()  # Deduplizierung
    total = len(register_numbers)
    skipped = 0
    no_statements = 0
    no_relevant_fields = 0
    duplicates = 0

    print(f"Schritt 2: {total} Organisationen einzeln abrufen und filtern...")

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

    print(f"  {len(all_statements)} relevante Stellungnahmen gefunden.")
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

    # PDF-URL und SG-Nummer fruehzeitig ermitteln, da das Bereitstellungsdatum
    # ('bereitgestellt am') pro Stellungnahme aus der SG-Nummer abgeleitet wird.
    page_url = str(stmt.get("pdfUrl", ""))
    pdf_url = fetch_real_pdf_url(page_url)
    sg_number = extract_sg_number(pdf_url)

    # upload_date = Bereitstellungsdatum aus SG-Nummer; Fallback auf das
    # organisationsweite lastUpdateDate, falls keine SG-Nummer vorhanden ist.
    stmt_upload_date = sg_to_upload_date(sg_number) or upload_date

    check_date = sending_date or stmt_upload_date
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

    pdf_pages = int(stmt.get("pdfPageCount", 0) or 0)
    statement_url = build_statement_url(sg_number)

    return {
        "register_number": str(register_number),
        "org_name": str(org_name),
        "org_url": details_page_url,
        "regulatory_project_title": str(stmt.get("regulatoryProjectTitle", "Kein Titel")),
        "sending_date": sending_date.isoformat() if sending_date else None,
        "upload_date": stmt_upload_date.isoformat() if stmt_upload_date else None,
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

def merge_statements(previous, fetched):
    """Merged bestehende und frisch abgerufene Stellungnahmen.

    Bestehende Eintraege (previous) behalten Vorrang: ihr upload_date und damit
    ihre Position in der Sortierung bleiben stabil. Aus dem frischen Abruf werden
    nur Stellungnahmen mit NEUER SG-Nummer ergaenzt.

    Gibt (merged_list, anzahl_neu_ergaenzt) zurueck.
    """
    def dedup_key(stmt):
        return stmt.get("sg_number") or (
            stmt["register_number"],
            stmt["regulatory_project_title"],
            stmt.get("sending_date", "")
        )

    seen = set()
    merged = []

    # Bestehende zuerst (haben Vorrang -> Datum & Reihenfolge bleiben stabil)
    for stmt in previous:
        key = dedup_key(stmt)
        if key not in seen:
            seen.add(key)
            merged.append(stmt)

    # Frisch abgerufene nur, wenn SG-Nummer noch nicht bekannt
    added = 0
    for stmt in fetched:
        key = dedup_key(stmt)
        if key not in seen:
            seen.add(key)
            merged.append(stmt)
            added += 1

    return merged, added

# ── Hauptprogramm ──────────────────────────────────────────────────────────────

def main():
    print("=== Lobbyregister Monitor - Datenabruf (V2 API, Vollabgleich) ===")

    # Vorherige Daten laden (aus Cache) - dienen zur Statement-Deduplizierung
    previous_statements, known_register_numbers = load_previous_data()
    if previous_statements:
        known_sg = {s.get("sg_number") for s in previous_statements if s.get("sg_number")}
        print(f"Cache: {len(previous_statements)} vorherige Eintraege "
              f"({len(known_sg)} mit SG-Nummer) aus {len(known_register_numbers)} Organisationen geladen.")
    else:
        print("Kein Cache vorhanden - vollstaendiger Erstabruf.")

    # Schritt 1: Alle Registernummern laden (nur die Liste, schnell)
    all_register_numbers = fetch_all_register_entries()
    if not all_register_numbers:
        print("WARNUNG: Keine Registereintraege geladen.")

    # Schritt 2: ALLE Eintraege einzeln abrufen (keine Organisation ueberspringen!)
    # Die Deduplizierung erfolgt anschliessend auf Stellungnahme-Ebene (SG-Nummer),
    # damit neue Stellungnahmen bereits bekannter Organisationen erfasst werden.
    print(f"\nRegisternummern gesamt: {len(all_register_numbers)}")
    print(f"Alle Organisationen werden auf neue Stellungnahmen geprueft.")

    if all_register_numbers:
        fetched_statements = fetch_and_filter_statements(all_register_numbers)
    else:
        fetched_statements = []
        print("\nKeine Eintraege abrufbar.")

    # Merge: bestehende Stellungnahmen behalten Vorrang (Datum + Reihenfolge bleiben
    # stabil), nur neue SG-Nummern werden ergaenzt.
    all_statements, added_count = merge_statements(previous_statements, fetched_statements)
    all_statements.sort(
        key=lambda x: (x.get("upload_date") or x.get("sending_date") or "0000-00-00"),
        reverse=True
    )

    print(f"\nErgebnis: {len(all_statements)} Stellungnahmen gesamt "
          f"({added_count} neu ergaenzt, {len(previous_statements)} bereits bekannt)")

    # BERLINER ZEIT für generated_at
    generated_at = datetime.now(BERLIN_TZ).isoformat()

    # Nur data.json speichern – HTML wird von gemini_enrich.py generiert
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

    print(f"Daten gespeichert: docs/data.json ({len(all_statements)} Eintraege)")

if __name__ == "__main__":
    main()

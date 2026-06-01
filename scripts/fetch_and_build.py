"""
fetch_and_build.py
==================
Ruft alle Stellungnahmen ueber die oeffentliche Lobbyregister-Suchschnittstelle
(sucheDetailJson) in EINEM gestreamten Durchgang ab. Dadurch werden alle
Organisationen samt ihrer Stellungnahmen inline geliefert – kein Einzelabruf
pro Organisation mehr noetig.

Die Deduplizierung erfolgt auf Stellungnahme-Ebene (SG-Nummer): bestehende
Eintraege bleiben stabil (Datum + Reihenfolge eingefroren), nur neue
Stellungnahmen werden ergaenzt. Das Bereitstellungsdatum (upload_date) wird aus
der SG-Nummer abgeleitet.

Speicherung in docs/data.json. HTML-Generierung erfolgt in gemini_enrich.py.

Hinweis: Die Antwort der Suchschnittstelle ist gross (~380 MB inkl. Volltexte).
Sie wird daher mit ijson GESTREAMT (Eintrag fuer Eintrag), sodass der
Speicherbedarf konstant niedrig bleibt (~50 MB statt mehrerer GB).
"""

import json
import os
import re
import requests
import ijson
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Konfiguration ──────────────────────────────────────────────────────────────

# Oeffentliche Suchschnittstelle: liefert alle Eintraege samt Stellungnahmen
# inline, ohne API-Key. pageSize wird serverseitig ignoriert (immer alle).
SEARCH_URL = "https://www.lobbyregister.bundestag.de/sucheDetailJson"

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

# Session fuer den gestreamten Abruf der Suchschnittstelle (ohne API-Key)
WEB_SESSION = requests.Session()
WEB_SESSION.headers.update({
    "Accept": "application/json",
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

def stream_and_filter_statements(known_pdf_by_sg=None):
    """Laedt die gesamte Suchschnittstelle gestreamt und extrahiert relevante
    Stellungnahmen.

    Verarbeitet die ~6900 Organisationen Eintrag fuer Eintrag (ijson), ohne die
    gesamte ~380-MB-Antwort im Speicher zu halten. Pro Organisation werden
    Themenfelder geprueft und – falls relevant – die Stellungnahmen einzeln
    verarbeitet. Die Verarbeitungslogik ist identisch zum frueheren Einzelabruf,
    da die Suchschnittstelle dieselbe Datenstruktur liefert.

    known_pdf_by_sg: Dict {sg_number: pdf_url} bereits bekannter Stellungnahmen.
    Da die Suchschnittstelle die direkte PDF-URL bereits inline liefert, dient
    dieser Cache nur noch als Fallback und zur Statistik.

    Gibt die Liste der relevanten Stellungnahmen zurueck.
    """
    if known_pdf_by_sg is None:
        known_pdf_by_sg = {}

    all_statements = []
    seen_keys = set()
    total_orgs = 0
    relevant_orgs = 0
    no_relevant_fields = 0
    no_statements = 0
    duplicates = 0
    pdf_lookups_skipped = 0

    print("Abruf: Suchschnittstelle (sucheDetailJson) gestreamt laden...")

    try:
        resp = WEB_SESSION.get(SEARCH_URL, params={"pageSize": 10000}, timeout=180, stream=True)
        resp.raise_for_status()
        resp.raw.decode_content = True

        for entry in ijson.items(resp.raw, "results.item"):
            total_orgs += 1
            if not isinstance(entry, dict):
                continue

            reg_num = entry.get("registerNumber", "")

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

            relevant_orgs += 1
            org_name = extract_org_name(entry)
            upload_date = extract_org_lastupdate(entry)
            details_page_url = extract_details_page_url(entry)
            rp_lookup = build_rp_lookup(entry)

            for stmt in stmts_list:
                result, skipped_lookup = process_statement(
                    stmt, reg_num, org_name, upload_date,
                    entry_fields, details_page_url, rp_lookup, known_pdf_by_sg)
                if skipped_lookup:
                    pdf_lookups_skipped += 1
                if result:
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

            if total_orgs % 1000 == 0:
                print(f"  {total_orgs} Organisationen verarbeitet, "
                      f"{len(all_statements)} relevante Stellungnahmen bisher...")

    except Exception as e:
        print(f"  FEHLER beim Streaming-Abruf: {e}")
        raise

    print(f"  {total_orgs} Organisationen gestreamt "
          f"({relevant_orgs} thematisch relevant mit Stellungnahmen).")
    print(f"  {len(all_statements)} relevante Stellungnahmen gefunden.")
    if duplicates:
        print(f"  ({duplicates} Duplikate entfernt)")
    if pdf_lookups_skipped:
        print(f"  ({pdf_lookups_skipped} PDF-Lookups dank direkter URL/Cache uebersprungen)")
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

def extract_org_lastupdate(entry):
    """Liest das organisationsweite lastUpdateDate (nur Fallback fuer das
    upload_date, falls eine Stellungnahme keine SG-Nummer hat)."""
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
                      entry_fields, details_page_url, rp_lookup, known_pdf_by_sg=None):
    """Verarbeitet eine einzelne Stellungnahme.

    Gibt (result_dict_oder_None, skipped_lookup_bool) zurueck. skipped_lookup ist
    True, wenn die PDF-URL aus dem Cache kam und kein HTTP-Request noetig war.
    """
    if known_pdf_by_sg is None:
        known_pdf_by_sg = {}
    if not isinstance(stmt, dict): return None, False

    sending_date = None
    for rg in stmt.get("recipientGroups", []):
        sd = rg.get("sendingDate", "")
        if sd:
            try:
                sending_date = date.fromisoformat(str(sd)[:10])
                break
            except ValueError: pass

    # Die Suchschnittstelle liefert in pdfUrl bereits die DIREKTE PDF-URL
    # (endet auf .pdf) inklusive SG-Nummer. Es ist daher kein HTTP-Request zur
    # Aufloesung mehr noetig. Der Cache/Fallback greift nur fuer den Sonderfall,
    # dass ausnahmsweise eine Seiten-URL statt der direkten URL geliefert wird.
    raw_pdf = str(stmt.get("pdfUrl", ""))
    sg_number = extract_sg_number(raw_pdf)
    skipped_lookup = False

    if raw_pdf.lower().endswith(".pdf"):
        # Direkte PDF-URL bereits vorhanden – kein Lookup noetig.
        pdf_url = raw_pdf
        skipped_lookup = True
    elif sg_number and sg_number in known_pdf_by_sg:
        # Bekannte Stellungnahme: gespeicherte PDF-URL wiederverwenden, kein HTTP.
        pdf_url = known_pdf_by_sg[sg_number]
        skipped_lookup = True
    else:
        # Sonderfall: Seiten-URL -> echte PDF-URL per HTTP aufloesen.
        pdf_url = fetch_real_pdf_url(raw_pdf)
        if not sg_number:
            sg_number = extract_sg_number(pdf_url)

    # upload_date = Bereitstellungsdatum aus SG-Nummer; Fallback auf das
    # organisationsweite lastUpdateDate, falls keine SG-Nummer vorhanden ist.
    stmt_upload_date = sg_to_upload_date(sg_number) or upload_date

    check_date = sending_date or stmt_upload_date
    if check_date and check_date < START_DATE: return None, skipped_lookup

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
    if not has_target_recipient: return None, skipped_lookup

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
        if not stmt_field_codes & TARGET_FIELD_CODES: return None, skipped_lookup
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
    }, skipped_lookup

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
    print("=== Lobbyregister Monitor - Datenabruf (Suchschnittstelle, gestreamt) ===")

    # Vorherige Daten laden (aus Cache) - dienen zur Statement-Deduplizierung
    previous_statements, known_register_numbers = load_previous_data()
    # Lookup bekannter PDF-URLs je SG-Nummer: erspart spaeter HTTP-Requests
    known_pdf_by_sg = {
        s["sg_number"]: s["pdf_url"]
        for s in previous_statements
        if s.get("sg_number") and s.get("pdf_url")
    }
    if previous_statements:
        print(f"Cache: {len(previous_statements)} vorherige Eintraege "
              f"({len(known_pdf_by_sg)} mit SG-Nummer) aus {len(known_register_numbers)} Organisationen geladen.")
    else:
        print("Kein Cache vorhanden - vollstaendiger Erstabruf.")

    # Abruf: gesamte Suchschnittstelle gestreamt laden und relevante
    # Stellungnahmen extrahieren (alle Organisationen in EINEM Durchgang).
    # Die Deduplizierung erfolgt auf Stellungnahme-Ebene (SG-Nummer), damit neue
    # Stellungnahmen bereits bekannter Organisationen erfasst werden.
    try:
        fetched_statements = stream_and_filter_statements(known_pdf_by_sg)
    except Exception:
        # Bei Abbruch des Abrufs: bestehende Daten NICHT ueberschreiben,
        # damit die Webseite nicht leer wird. Lauf kontrolliert beenden.
        print("FEHLER: Abruf fehlgeschlagen - bestehende data.json bleibt unveraendert.")
        return

    if not fetched_statements and not previous_statements:
        print("WARNUNG: Keine Stellungnahmen abrufbar und kein Cache vorhanden.")

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
            "newly_added": added_count,  # heute neu in die DB aufgenommen (fuer Run-Log)
            "statements": sorted(
                all_statements,
                key=lambda x: (x.get("upload_date") or x.get("sending_date") or "0000-00-00"),
                reverse=True
            )
        }, f, ensure_ascii=False, indent=2)

    print(f"Daten gespeichert: docs/data.json ({len(all_statements)} Eintraege)")

if __name__ == "__main__":
    main()

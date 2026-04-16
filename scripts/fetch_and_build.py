"""
fetch_and_build.py
==================
Ruft Stellungnahmen vom Lobbyregister ab (V2 API) und generiert die HTML-Seite.
"""

import json
import os
import requests
from datetime import datetime, date
from collections import defaultdict
from pathlib import Path

# ── Konfiguration ──────────────────────────────────────────────────────────────

API_BASE = "https://api.lobbyregister.bundestag.de/rest/v2"
API_KEY = os.environ.get("LOBBYREGISTER_API_KEY", "5bHB2zrUuHR6YdPoZygQhWfg2CBrjUOi")
SITE_URL = "https://bmwe-iiia4.github.io/lobbyregister-monitor"
START_DATE = date(2026, 1, 1)

# Gesuchte Empfänger (shortTitle aus API)
TARGET_RECIPIENTS = {"BMWE", "Bundestag", "BT"}

# Gesuchte Themenfeld-Codes
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


# ── API-Abfrage ────────────────────────────────────────────────────────────────

def fetch_all_entries():
    """Ruft alle Registereinträge über die V2-API ab (paginiert)."""
    headers = {"Authorization": f"ApiKey {API_KEY}"}
    all_entries = []
    cursor = None
    page = 0

    print("Rufe Registereinträge vom Lobbyregister ab (V2 API)...")

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

        # Diagnose nur auf Seite 0
        if page == 0:
            print(f"  Antwort-Typ: {type(data)}")
            if isinstance(data, dict):
                keys = list(data.keys())
                print(f"  Top-Level-Schlüssel: {keys}")
                for k in keys[:8]:
                    v = data[k]
                    if isinstance(v, list):
                        print(f"    '{k}': Liste mit {len(v)} Einträgen")
                        if len(v) > 0 and isinstance(v[0], dict):
                            print(f"      Erster Eintrag Schlüssel: {list(v[0].keys())}")
                            # Vollständige Struktur des ersten Eintrags ausgeben
                            print(f"      ERSTER EINTRAG KOMPLETT:")
                            print(json.dumps(v[0], ensure_ascii=False, indent=2)[:3000])
                    elif isinstance(v, dict):
                        print(f"    '{k}': Dict mit Schlüsseln {list(v.keys())[:8]}")
                    elif isinstance(v, str) and len(v) < 100:
                        print(f"    '{k}': {v}")
                    else:
                        print(f"    '{k}': {type(v)}")

        # Einträge extrahieren
        entries = []
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            for key in ["results", "content", "items", "data", "entries", "registerEntries"]:
                if key in data and isinstance(data[key], list):
                    entries = data[key]
                    break
            if not entries and "registerNumber" in data:
                entries = [data]

        all_entries.extend(entries)
        page += 1

        # DIAGNOSE: nach erster Seite stoppen um Struktur zu prüfen
        if page >= 1:
            print(f"  DIAGNOSE-STOPP nach Seite {page} ({len(all_entries)} Einträge)")
            break

        new_cursor = data.get("cursor") if isinstance(data, dict) else None
        if not new_cursor or new_cursor == cursor:
            break
        cursor = new_cursor

        if page % 20 == 0:
            print(f"  Seite {page} ({len(all_entries)} Einträge gesamt)...")

    print(f"  Insgesamt {len(all_entries)} Registereinträge abgerufen.")
    return all_entries


def extract_statements_from_entries(entries):
    """Extrahiert relevante Stellungnahmen aus allen Registereinträgen."""
    results = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        register_number = entry.get("registerNumber", "")
        org_name = extract_org_name(entry)

        # Stellungnahmen-Daten suchen
        stmts_container = None
        for key in ["statements", "regulatoryProjects"]:
            val = entry.get(key)
            if val:
                stmts_container = val
                break

        # Auch in registerEntryDetails suchen
        details = entry.get("registerEntryDetails", {})
        if isinstance(details, dict) and not stmts_container:
            for key in ["statements", "regulatoryProjects"]:
                val = details.get(key)
                if val:
                    stmts_container = val
                    break

        if not stmts_container:
            continue

        # stmts_container kann dict mit 'statements'-Liste sein
        if isinstance(stmts_container, dict):
            stmt_list = stmts_container.get("statements", [])
        elif isinstance(stmts_container, list):
            stmt_list = stmts_container
        else:
            continue

        for stmt in stmt_list:
            if not isinstance(stmt, dict):
                continue

            result = process_statement(stmt, register_number, org_name, entry)
            if result:
                results.append(result)

    return results


def extract_org_name(entry):
    """Extrahiert Organisationsname aus verschiedenen möglichen Strukturen."""
    # Direkt im Eintrag
    for key in ["lobbyistIdentity", "identity", "organisation"]:
        val = entry.get(key)
        if isinstance(val, dict):
            name = val.get("name", "")
            if name:
                return name
        elif isinstance(val, str) and val:
            return val

    # Im registerEntryDetails
    details = entry.get("registerEntryDetails", {})
    if isinstance(details, dict):
        for key in ["lobbyistIdentity", "identity"]:
            val = details.get(key)
            if isinstance(val, dict):
                name = val.get("name", "")
                if name:
                    return name

    return entry.get("name", "Unbekannte Organisation")


def process_statement(stmt, register_number, org_name, parent_entry):
    """Verarbeitet eine einzelne Stellungnahme."""
    # Datum
    upload_date = None
    sending_date = None

    # Upload-Datum aus Parent-Eintrag
    account = parent_entry.get("accountDetails", parent_entry.get("account", {}))
    if isinstance(account, dict):
        pub = account.get("firstPublicationDate", "")
        if pub:
            try:
                upload_date = date.fromisoformat(str(pub)[:10])
            except ValueError:
                pass

    # Sending-Datum aus recipientGroups
    for rg in stmt.get("recipientGroups", []):
        sd = rg.get("sendingDate", "")
        if sd:
            try:
                sending_date = date.fromisoformat(str(sd)[:10])
                break
            except ValueError:
                pass

    # Datum-Filter
    check_date = upload_date or sending_date
    if check_date and check_date < START_DATE:
        return None

    # Empfänger prüfen
    recipients = []
    for rg in stmt.get("recipientGroups", []):
        recips = rg.get("recipients", {})
        if isinstance(recips, dict):
            for fg in recips.get("federalGovernment", []):
                dept = fg.get("department", {})
                short = dept.get("shortTitle", "")
                if short:
                    recipients.append(short)
            for p in recips.get("parliament", []):
                recipients.append("Bundestag")
                break

    recipients = list(dict.fromkeys(recipients))

    if not any(r in TARGET_RECIPIENTS for r in recipients):
        return None

    # Themenfelder prüfen
    fields = []
    for f in stmt.get("fieldsOfInterest", []):
        if isinstance(f, dict):
            code = f.get("code", "")
            label = f.get("de", "") or f.get("label", "") or code
            fields.append({"code": code, "label": label})

    field_codes = {f["code"] for f in fields}
    if not field_codes & TARGET_FIELD_CODES:
        return None

    # Priorität
    priority = min((FIELD_PRIORITY.get(c, 99) for c in field_codes), default=99)

    # Summary
    text_obj = stmt.get("text", {})
    summary = text_obj.get("text", "") if isinstance(text_obj, dict) else str(text_obj)

    return {
        "statement_number": str(stmt.get("statementNumber", "")),
        "register_number": str(register_number),
        "regulatory_project_title": str(stmt.get("regulatoryProjectTitle", "Kein Titel")),
        "org_name": str(org_name),
        "sending_date": sending_date.isoformat() if sending_date else None,
        "upload_date": upload_date.isoformat() if upload_date else None,
        "pdf_url": str(stmt.get("pdfUrl", "")),
        "pdf_pages": int(stmt.get("pdfPageCount", 0) or 0),
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
    print("=== Lobbyregister Monitor – Seitengenerierung ===")

    entries = fetch_all_entries()
    statements = extract_statements_from_entries(entries)

    print(f"Relevante Stellungnahmen nach Filterung: {len(statements)}")
    if not statements:
        print("WARNUNG: Keine Einträge. Seite wird trotzdem generiert.")

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

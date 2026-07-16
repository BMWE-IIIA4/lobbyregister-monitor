"""
gemini_enrich.py
================
Reichert die Lobbyregister-Daten mit Gemini 3.1 Flash Lite an.
Grundlage der Zusammenfassung ist der PDF-Volltext der Stellungnahme
(Fallback auf die Metadaten-Beschreibung, wenn die PDF nur aus Bildern
besteht oder nicht abrufbar ist). Einzelverarbeitung: ein API-Call pro
Stellungnahme.

Zusammengehoerige Stellungnahmen (gleiches Regelungsvorhaben, gleiche
Institution, gleiche Themenfelder, gleiches Hochladedatum) werden zu einem
gemeinsamen Block zusammengefasst, um Redundanz auf der Webseite zu
vermeiden. Sortierung innerhalb eines Tages erfolgt alphabetisch nach
Regelungsvorhaben.

Mit Caching, Fail-Fast bei Quota-Limits und automatischem HTML-Rebuild.
"""

import json
import os
import time
import re
import sys
import requests
from io import BytesIO
from pathlib import Path
from collections import defaultdict
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from config import GEMINI_MODEL

try:
    from pypdf import PdfReader
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

BERLIN_TZ = ZoneInfo("Europe/Berlin")

# -- Konfiguration --

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

REQUEST_DELAY = 6.0          # >= 6s -> hoechstens 10 RPM (Free Tier gemini-3.1-flash-lite: 10-15 RPM)
MAX_RETRIES = 3
RETRY_DELAY = 10

# Budget: max. API-Calls pro Lauf (Free Tier gemini-3.1-flash-lite: 500 RPD, mit Puffer)
MAX_AI_PER_RUN = 450

# PDF-Volltext-Extraktion
PDF_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LobbyregisterMonitor/1.0)"}
PDF_TEXT_MAX_CHARS = 40000   # Kappung, um unter dem TPM-Limit (250K/min) zu bleiben
PDF_MIN_CHARS = 200          # darunter: vermutlich Bild-Scan ohne Textebene -> Fallback

DATA_PATH = Path("docs/data.json")
CACHE_PATH = Path("docs/gemini_cache.json")
TEMPLATE_PATH = Path("scripts/template.html")
SITE_URL = "https://lobbyregister-bot.de"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

# -- Relevanzkatalog --

RELEVANZ_KATALOG = (
    "THEMENFELDER DER UNTERABTEILUNG IIIA (BMWE):\n\n"
    "EU-ENERGIEPOLITIK & ENERGIEGESETZGEBUNG:\n"
    "- EU-Energieszenarien, EU-Zielarchitektur 2030/2040\n"
    "- Erneuerbare-Energien-Richtlinie (RED), EU-Notfallverordnungen\n"
    "- EU-Governance-Verordnung, Energieunion, NECP\n"
    "- Clean Industrial Deal, EU Green Deal, RePowerEU, CISAF, Temporary Crisis Framework\n"
    "- Verordnung (EU) 2019/943 Elektrizitaetsbinnenmarkt\n"
    "- Richtlinie (EU) 2019/944 Elektrizitaetsbinnenmarkt\n"
    "- ACER\n"
    "- EU-Beihilferecht EE, EU-Rahmenbedingungen EE-Foerdersysteme\n"
    "- EU-Rahmenbedingungen erneuerbarer Wasserstoff (RFNBO)\n"
    "- Grenzueberschreitende EE-Kooperationsprojekte, Offshore-Kooperationen\n"
    "- Nordseekooperation, Pentalaterales Energieforum\n"
    "- EU-Klimapolitik, EU-Klimagesetz, Fit for 55\n"
    "- EU-Energiepreise, Wettbewerbsfaehigkeit im EU-Binnenmarkt\n"
    "- EU-Wasserstoffmarkt, EU-Energieeffizienz, CCS/CCU\n"
    "- Dekarbonisierung europaeische Energieerzeugung\n\n"
    "BILATERALE ENERGIEBEZIEHUNGEN:\n"
    "- DFBEW, Energiepolitische Beziehungen zu EU-Mitgliedstaaten, Norwegen, Schweiz, UK\n\n"
    "VERSORGUNGSSICHERHEIT STROM:\n"
    "- Monitoring Versorgungssicherheit, Kapazitaetsreserve, Netzreserve\n\n"
    "- bnBm, besondere Netztechnische Betriebsmittel, zeitlich gestrecke Stillegung, zgS\n\n"
    "STROMMARKTDESIGN & -REGULIERUNG:\n"
    "- Kapazitaetsmechanismen, Boersenhandel/OTC-Maerkte, Regelenergien\n"
    "- Stromgebotszonen, Flexibilisierung, REMIT, Netzwerkcodes\n"
    "- Redispatch, Netzoptimierung, Plattform Klimaneutrales Stromsystem\n\n"
    "STROMERZEUGUNG & KRAFTWERKE:\n"
    "- Kohleausstieg, Kraftwerksstrategie, KWK (KWKG), Wasserstoffkraftwerke, StromVKG\n\n"
    "ERNEUERBARE ENERGIEN (NATIONAL):\n"
    "- EEG-Finanzierung, Besondere Ausgleichsregelung, PPA, Eigenverbrauch\n\n"
    "KLIMASCHUTZ & ENERGIEWENDE:\n"
    "- Langfristszenarien, SES, Szenariorahmen NEP, Projektionsbericht\n"
    "- Sektorkopplung, Finanzierungsbedarfe Transformation\n"
    "- Reform klimarelevanter Steuern/Abgaben/Umlagen\n\n"
    "ENERGIEPREISE & -KOSTEN:\n"
    "- Grosshandelspreise, Endverbraucherpreise, internationale Energiepreise\n\n"
    "ENERGIEMONITORING & -STATISTIK:\n"
    "- Monitoring-Berichte Energiewende, AGEE-Stat, Treibhausgasemissionen\n\n"
    "NICHT RELEVANT:\n"
    "- Rein parteipolitische Finanzierung, Medienrecht, Datenschutz ohne Energiebezug\n"
    "- Arbeitsrecht, Verbraucherschutz, Kulturpolitik ohne Energiebezug\n"
    "- Verteidigungspolitik, Gesundheitspolitik, Pharmarecht ohne Energiebezug\n"
    "- Verkehrspolitik ohne Bezug zu Sektorkopplung/E-Mobilitaet/Kraftstoffen\n"
    "- Bauwesen ohne Bezug zu Gebaeudeenergie/Waermewende\n"
    "- Prozesswärme, Wärmenetze, Wärmewende, Fernwärme, BEG, energetische Sanierung\n"
    "- Gasspeicher\n"
    "- Gebäudeenergiegesetz, Gebäudemodernisierungsgesetz"
)

# -- Gemini API & Cache --

def load_cache():
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_cache(cache_data):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)

def call_gemini(prompt, retries=MAX_RETRIES):
    headers = {"Content-Type": "application/json"}
    params = {"key": GEMINI_API_KEY}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    bt = chr(96) * 3
    for attempt in range(retries):
        try:
            resp = requests.post(GEMINI_URL, headers=headers, params=params, json=payload, timeout=90)
            if resp.status_code == 429:
                msg = resp.json().get("error", {}).get("message", "")
                if "exceeded your current quota" in msg.lower():
                    print("  ! QUOTA ERREICHT. Sofortiger Abbruch (Fail-Fast).")
                    return "QUOTA_EXCEEDED"
                wait = RETRY_DELAY * (2 ** attempt)
                print(f"  429 Rate Limit. Warte {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"  API-Fehler {resp.status_code}")
                time.sleep(RETRY_DELAY)
                continue
            data = resp.json()
            text = data.get("candidates", [])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            text = text.strip()
            if text.startswith(bt):
                text = re.sub(r"^" + bt + r"(?:json)?\s*", "", text)
                text = re.sub(r"\s*" + bt + r"$", "", text)
            return json.loads(text)
        except Exception as e:
            print(f"  Fehler: {e}")
            time.sleep(RETRY_DELAY)
    return None

def extract_pdf_text(pdf_url):
    """Laedt die PDF und extrahiert den Text. Gibt "" zurueck bei Fehlern oder
    wenn die PDF ueberwiegend aus (nicht extrahierbaren) Bildern besteht – dann
    greift der Aufrufer auf die Metadaten-Beschreibung zurueck."""
    if not PDF_AVAILABLE or not pdf_url or not pdf_url.lower().endswith(".pdf"):
        return ""
    try:
        resp = requests.get(pdf_url, headers=PDF_HEADERS, timeout=60)
        if resp.status_code != 200:
            return ""
        reader = PdfReader(BytesIO(resp.content))
        parts = []
        total = 0
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t:
                parts.append(t)
                total += len(t)
            if total >= PDF_TEXT_MAX_CHARS:
                break
        text = "\n".join(parts).strip()
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) < PDF_MIN_CHARS:
            return ""   # zu wenig Text -> Bild-Scan -> Fallback auf Metadaten
        return text[:PDF_TEXT_MAX_CHARS]
    except Exception as e:
        print(f"  PDF-Extraktion fehlgeschlagen ({pdf_url}): {e}")
        return ""

def build_single_prompt(stmt, pdf_text):
    title = stmt.get("regulatory_project_title", "Kein Titel")
    fields = ", ".join(f["label"] for f in stmt.get("fields", []))
    recipients = ", ".join(stmt.get("recipients", []))
    meta_desc = stmt.get("summary", "")
    if pdf_text:
        inhalt_block = (
            "VOLLTEXT DER STELLUNGNAHME (aus der PDF extrahiert – "
            "Grundlage der Zusammenfassung):\n" + pdf_text
        )
    else:
        inhalt_block = (
            "HINWEIS: Kein PDF-Volltext verfuegbar (Bild-Scan oder Abruf "
            "fehlgeschlagen). Nutze die folgende Kurzbeschreibung als Grundlage:\n"
            + (meta_desc or "Keine Beschreibung vorhanden.")
        )
    return (
        "Du bist ein Analyst im Bundesministerium fuer Wirtschaft und Energie (BMWE).\n"
        "Pruefe Relevanz und fasse zusammen:\n\n"
        "RELEVANZPRUEFUNG:\n"
        f"{RELEVANZ_KATALOG}\n"
        "Ist der Eintrag relevant? (true/false).\n\n"
        "ZUSAMMENFASSUNG:\n"
        "Schreibe 5 bis 8 praegnante Saetze (ca. 50% mehr als eine kurze "
        "Zusammenfassung). Sachlich. Ohne Wertung.\n\n"
        "ERSTER SATZ: Nennt zwingend (a) die Handlung/Haltung der Organisation "
        "(z.B. fordert, lehnt ab, begruesst, empfiehlt, warnt vor, schlaegt vor, kritisiert, "
        "unterstuetzt, bezweifelt, beantragt, praezisiert) UND (b) den zentralen Gegenstand.\n"
        "FOLGENDE SAETZE: Konkretisieren die wichtigsten Details, Argumente und Forderungen "
        "aus dem Volltext.\n\n"
        "MARKIERUNG mit <b>-Tags:\n"
        "Markiere die Woerter und kurzen Phrasen, die beim schnellen Ueberfliegen den Kern "
        "erfassen lassen (Taetigkeitswort der Organisation, konkrete Forderung/Aussage, "
        "spezifische Kernbegriffe). NICHT markieren: Woerter aus dem Titel, allgemeine "
        "Woerter (Stellungnahme, Unternehmen, Bereich), reine Gesetzesnummern aus dem Titel.\n"
        "Ziel: 4-7 markierte Phrasen.\n"
        "Falls keine inhaltliche Grundlage vorhanden: 'Keine inhaltliche Beschreibung verfuegbar.'\n\n"
        f"Titel: {title}\nThemenfelder: {fields}\nAdressaten: {recipients}\n\n"
        f"{inhalt_block}\n\n"
        'Antworte als JSON-Objekt: {"relevant": true, "relevanz_grund": "...", "zusammenfassung": "..."}\n'
        "NUR das JSON-Objekt."
    )

def notify_admin_error(error_summary):
    if not RESEND_API_KEY or not ADMIN_EMAIL: return
    today = date.today().strftime("%d.%m.%Y")
    html = (
        '<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;background:#f5f5f5;padding:20px;">'
        '<div style="max-width:600px;margin:0 auto;background:#fff;padding:24px;border-top:4px solid #e65100;">'
        '<h2 style="margin-top:0;">Gemini-Fehler</h2>'
        f'<p><strong>Problem:</strong> {error_summary}</p>'
        '</div></div>'
    )
    try:
        requests.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                      json={"from": "Lobbyregister-Monitor Systemcheck <healthcheck@lobbyregister-bot.de>", "to": [ADMIN_EMAIL], "subject": f"Lobbyregister-Monitor: Gemini fehlgeschlagen ({today})", "html": html}, timeout=30)
    except Exception: pass

# -- HTML Generierung --

def format_date_de(iso_date):
    if not iso_date: return "\u2013"
    try: return date.fromisoformat(iso_date).strftime("%d.%m.%Y")
    except ValueError: return iso_date

def get_weekday_de(iso_date):
    days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    months = ["", "Januar", "Februar", "M\u00e4rz", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    try:
        d = date.fromisoformat(iso_date)
        return f"{days[d.weekday()]}, {d.day}. {months[d.month]} {d.year}"
    except Exception: return iso_date

def upload_delay_style(sending_raw, upload_raw):
    """Gibt (css_farbe, tage) zurück: grün ≤7 Tage, gelb ≤30, rot >30.
    Der Verzug bezieht sich immer auf das Hochladedatum (Abstand zum
    Einreichungsdatum beim Empfaenger) – nicht auf das Stellungnahme-Datum
    selbst."""
    if not sending_raw or not upload_raw:
        return None, 0
    try:
        d1 = date.fromisoformat(sending_raw)
        d2 = date.fromisoformat(upload_raw)
        diff = (d2 - d1).days
        if diff <= 0:
            return None, diff
        elif diff <= 7:
            return "#16a34a", diff
        elif diff <= 30:
            return "#d97706", diff
        else:
            return "#dc2626", diff
    except Exception:
        return None, 0

def render_entry_card(stmt):
    raw_title = stmt["regulatory_project_title"]
    raw_org = stmt["org_name"]
    # Sichtbarer Text: HTML-escaped. Attributwerte (data-*): " -> ' ,
    # identisch zur Filterliste (data-v / data-o), damit der JS-Vergleich aufgeht.
    title = raw_title.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
    org = raw_org.replace('<', '&lt;').replace('>', '&gt;')
    title_attr = raw_title.replace(chr(34), chr(39))
    org_attr = raw_org.replace(chr(34), chr(39))
    org_url = stmt.get("org_url", "")
    sending = format_date_de(stmt.get("sending_date"))
    upload_raw = stmt.get("upload_date")
    sending_raw = stmt.get("sending_date")
    color, diff = upload_delay_style(sending_raw, upload_raw)
    delay_str = f" (+{diff} Tage)" if diff > 0 else ""
    if color:
        upload_display = f'<span style="color:{color}">{format_date_de(upload_raw)}{delay_str}</span>'
    else:
        upload_display = f'{format_date_de(upload_raw)}{delay_str}'
    summary = stmt.get("summary", "") or "Keine Beschreibung verf\u00fcgbar."
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
    stmt_link = f'<a href="{statement_url}" target="_blank">\u2197 Stellungnahme im Lobbyregister {sg_number}</a>' if statement_url else ''
    pdf_link = f'<a href="{pdf_url}" target="_blank">\u2197 PDF herunterladen ({pdf_pages} Seiten)</a>' if pdf_url else '<span style="color:#999">Kein PDF</span>'
    pending_badge = ""
    if stmt.get("gemini_status") == "pending":
        pending_badge = '<span style="font-size:0.7rem;font-weight:700;color:#94a3b8;margin-left:10px;">KI-Pr\u00fcfung ausstehend</span>'
    return (
        f'<div class="entry-card" data-vorhaben="{title_attr}" data-org="{org_attr}" data-upload="{stmt.get("upload_date", "")}">'
        f'<div class="row-title">{title}{pending_badge}</div>'
        f'<div class="meta-row">'
        f'<div class="mc grow"><strong>Bereitgestellt von</strong>{org_html}</div>'
        f'<div class="mc fixd"><strong>Datum Stellungnahme</strong>{sending}</div>'
        f'<div class="mc fixd"><strong>Hochgeladen am</strong>{upload_display}</div>'
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

def group_statements(statements):
    """Gruppiert Stellungnahmen mit identischem Regelungsvorhaben, identischer
    Institution, identischen Themenfeldern und identischem Hochladedatum zu
    einem gemeinsamen Block. Gibt eine Liste zurueck, in der jedes Element
    entweder ein einzelnes Statement-Dict (unveraendert) oder ein Dict mit
    Schluessel '_group' (Liste der Mitglieder, nach sending_date sortiert) ist.
    """
    groups = {}
    order = []
    for stmt in statements:
        field_key = tuple(sorted(f["code"] for f in stmt.get("fields", [])))
        key = (
            stmt.get("regulatory_project_title", ""),
            stmt.get("org_name", ""),
            field_key,
            stmt.get("upload_date", ""),
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(stmt)

    result = []
    for key in order:
        members = groups[key]
        if len(members) == 1:
            result.append(members[0])
        else:
            members_sorted = sorted(members, key=lambda m: m.get("sending_date") or "")
            result.append({"_group": members_sorted})
    return result

def group_sort_key(item):
    """Alphabetische Sortierung nach Regelungsvorhaben – ersetzt die bisherige
    (geschaetzte) Prioritaets-Sortierung."""
    if isinstance(item, dict) and "_group" in item:
        return item["_group"][0].get("regulatory_project_title", "").lower()
    return item.get("regulatory_project_title", "").lower()

def render_group_card(members):
    """Rendert mehrere zusammengehoerige Stellungnahmen als einen gemeinsamen
    Block. Felder, die innerhalb der Gruppe identisch sind, werden einzeilig
    dargestellt; Felder, die variieren, werden nummeriert (1, 2, 3, ...).

    Der Verzug (+X Tage) bezieht sich auf das Hochladedatum: das Datum selbst
    ist innerhalb einer Gruppe zwar identisch (Gruppierungskriterium), der
    Verzug zum jeweiligen Stellungnahme-Datum kann aber pro Mitglied
    unterschiedlich sein – daher wird "Hochgeladen am" nummeriert, sobald sich
    die Verzugsanzeige unterscheidet.
    """
    first = members[0]
    raw_title = first["regulatory_project_title"]
    raw_org = first["org_name"]
    title = raw_title.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
    org = raw_org.replace('<', '&lt;').replace('>', '&gt;')
    title_attr = raw_title.replace(chr(34), chr(39))
    org_attr = raw_org.replace(chr(34), chr(39))
    org_url = first.get("org_url", "")
    org_html = f'<a href="{org_url}" target="_blank" style="color:#004B87;text-decoration:none">{org}</a>' if org_url else org
    fields = first.get("fields", [])
    field_tags = "".join(f'<span class="tag">{f["label"]}</span>' for f in fields)
    upload_raw = first.get("upload_date")

    # Datum Stellungnahme: reine Daten, keine Farbmarkierung (wie im Original).
    sending_vals = [format_date_de(m.get("sending_date")) for m in members]
    if len(set(sending_vals)) == 1:
        sending_html = sending_vals[0]
    else:
        sending_html = "<br>".join(f"{i+1}) {v}" for i, v in enumerate(sending_vals))

    # Hochgeladen am: Datum ist gleich, aber die Verzugsanzeige (+X Tage) haengt
    # vom jeweiligen sending_date des Mitglieds ab und kann daher variieren.
    upload_vals = []
    for m in members:
        color, diff = upload_delay_style(m.get("sending_date"), upload_raw)
        delay_str = f" (+{diff} Tage)" if diff > 0 else ""
        v = (f'<span style="color:{color}">{format_date_de(upload_raw)}{delay_str}</span>'
             if color else f'{format_date_de(upload_raw)}{delay_str}')
        upload_vals.append(v)
    if len(set(upload_vals)) == 1:
        upload_html = upload_vals[0]
    else:
        upload_html = "<br>".join(f"{i+1}) {v}" for i, v in enumerate(upload_vals))

    recip_lists = [tuple(m.get("recipients", [])) for m in members]
    if len(set(recip_lists)) == 1:
        recip_html = "".join(f'<span class="abadge">{r}</span>' for r in members[0].get("recipients", []))
    else:
        parts = []
        for i, m in enumerate(members):
            badges = "".join(f'<span class="abadge">{r}</span>' for r in m.get("recipients", []))
            parts.append(f'<div style="margin-bottom:0.2rem">{i+1}) {badges}</div>')
        recip_html = "".join(parts)

    content_parts = []
    link_parts = []
    for i, m in enumerate(members):
        summary = m.get("summary", "") or "Keine Beschreibung verf\u00fcgbar."
        summary = re.sub(r'<(?!/?b>)', '&lt;', summary).replace('>', '&gt;').replace('<b&gt;', '<b>').replace('</b&gt;', '</b>')
        pending_badge = ""
        if m.get("gemini_status") == "pending":
            pending_badge = '<span style="font-size:0.7rem;font-weight:700;color:#94a3b8;margin-left:10px;">KI-Pr\u00fcfung ausstehend</span>'
        content_parts.append(f'<div style="margin-bottom:0.6rem"><strong>{i+1})</strong> {summary}{pending_badge}</div>')

        sg_number = m.get("sg_number", "")
        statement_url = m.get("statement_url", "")
        pdf_url = m.get("pdf_url", "")
        pdf_pages = m.get("pdf_pages", 0)
        stmt_link = f'<a href="{statement_url}" target="_blank">\u2197 Stellungnahme im Lobbyregister {sg_number}</a>' if statement_url else ''
        pdf_link = f'<a href="{pdf_url}" target="_blank">\u2197 PDF herunterladen ({pdf_pages} Seiten)</a>' if pdf_url else '<span style="color:#999">Kein PDF</span>'
        link_parts.append(f'<div class="lc">{i+1}) {stmt_link}</div><div class="lc">{pdf_link}</div>')

    content_html = "".join(content_parts)
    links_html = "".join(link_parts)
    count_badge = f'<span style="font-size:0.75rem;font-weight:600;color:#64748b;margin-left:8px">({len(members)} zusammengeh\u00f6rige Stellungnahmen)</span>'

    return (
        f'<div class="entry-card" data-vorhaben="{title_attr}" data-org="{org_attr}" data-upload="{first.get("upload_date", "")}">'
        f'<div class="row-title">{title}{count_badge}</div>'
        f'<div class="meta-row">'
        f'<div class="mc grow"><strong>Bereitgestellt von</strong>{org_html}</div>'
        f'<div class="mc fixd"><strong>Datum Stellungnahme</strong>{sending_html}</div>'
        f'<div class="mc fixd"><strong>Hochgeladen am</strong>{upload_html}</div>'
        f'</div>'
        f'<div class="meta-row two-col">'
        f'<div class="mc half"><strong>Adressaten</strong>{recip_html}</div>'
        f'<div class="mc half"><strong>Themenfelder der Stellungnahme</strong>{field_tags}</div>'
        f'</div>'
        f'<div class="row-full"><span class="row-label">Inhalt</span>{content_html}</div>'
        f'<div class="link-row">{links_html}</div>'
        f'</div>'
    )

def generate_html(statements, generated_at, pending_dates):
    by_date = defaultdict(list)
    vorhaben_counts = defaultdict(int)
    org_counts_all = defaultdict(int)
    org_counts_6m = defaultdict(int)
    cutoff_6m = (date.today() - timedelta(days=180)).isoformat()
    for stmt in statements:
        key = stmt.get("upload_date") or "unbekannt"
        by_date[key].append(stmt)
        vorhaben_counts[stmt["regulatory_project_title"]] += 1
        org = stmt["org_name"]
        org_counts_all[org] += 1
        if (stmt.get("upload_date") or "") >= cutoff_6m:
            org_counts_6m[org] += 1
    day_sections_html = ""
    if pending_dates:
        dates_str = ", ".join([format_date_de(d) if d != "unbekannt" else "unbekanntem Datum" for d in sorted(pending_dates, reverse=True)])
        day_sections_html += (
            f'<div style="background:#fefce8;color:#854d0e;padding:12px 16px;margin-bottom:20px;border-radius:8px;border:1px solid #fde047;font-size:0.95rem;">'
            f'<b>Hinweis zum API-Limit:</b> Aufgrund hoher Serverauslastung konnten Eintr\u00e4ge vom <b>{dates_str}</b> noch nicht durch die KI auf Relevanz gepr\u00fcft und zusammengefasst werden. Sie werden tempor\u00e4r ungefiltert angezeigt. Die Pr\u00fcfung wird beim n\u00e4chsten Durchlauf automatisch nachgeholt.'
            f'</div>'
        )
    for iso_date, day_stmts in sorted(by_date.items(), reverse=True):
        grouped = group_statements(day_stmts)
        grouped_sorted = sorted(grouped, key=group_sort_key)
        cards_html = "".join(
            render_group_card(item["_group"]) if isinstance(item, dict) and "_group" in item
            else render_entry_card(item)
            for item in grouped_sorted
        )
        day_sections_html += (
            f'<div class="day-section" data-date="{iso_date}">'
            f'<div class="day-header">{get_weekday_de(iso_date)}</div>'
            f'{cards_html}'
            f'</div>'
        )
    filter_items = "".join(f'<li data-v="{v.replace(chr(34), chr(39))}"><span>{v}</span><span class="filter-count">{c}</span></li>' for v, c in sorted(vorhaben_counts.items(), key=lambda x: -x[1]))
    org_items_all = "".join(f'<li data-o="{o.replace(chr(34), chr(39))}" data-c-all="{c}" data-c-6m="{org_counts_6m.get(o,0)}"><span>{o}</span><span class="filter-count">{c}</span></li>' for o, c in sorted(org_counts_all.items(), key=lambda x: -x[1]))
    gen_dt = datetime.fromisoformat(generated_at).astimezone(BERLIN_TZ)
    months_de = ["", "Januar", "Februar", "M\u00e4rz", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{{DAY_SECTIONS}}", day_sections_html)
    html = html.replace("{{FILTER_ITEMS}}", filter_items)
    html = html.replace("{{ORG_ITEMS}}", org_items_all)
    html = html.replace("{{GENERATED_AT}}", f"{gen_dt.day}. {months_de[gen_dt.month]} {gen_dt.year}, {gen_dt.strftime('%H:%M')} Uhr")
    html = html.replace("{{TOTAL_COUNT}}", str(len(statements)))
    html = html.replace("{{FIELDS_SUBTITLE}}", "Energie &amp; Wasserstoff, Klimaschutz, EU-Binnenmarkt, EU-Gesetzgebung, Bundestag, Wettbewerbsrecht, Politisches Leben/Parteien, Sonstige")
    html = html.replace("{{SITE_URL}}", SITE_URL)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

# -- Hauptlogik --

def main():
    print("=== Lobbyregister Monitor - Gemini Anreicherung & Caching ===")
    print(f"Modell: {GEMINI_MODEL} | Budget: max. {MAX_AI_PER_RUN} Calls/Lauf")

    if not DATA_PATH.exists():
        print(f"FEHLER: {DATA_PATH} fehlt.")
        sys.exit(1)

    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    statements = data.get("statements", [])
    if not statements:
        sys.exit(0)

    cache = load_cache()
    to_process = []
    final_statements = []
    filtered_out = []

    # 1. Cache-Abgleich
    for stmt in statements:
        uid = stmt.get("sg_number") or stmt.get("statement_url") or stmt.get("pdf_url")
        if uid and uid in cache:
            cached_data = cache[uid]
            if cached_data.get("relevant", True):
                stmt["summary"] = cached_data.get("zusammenfassung", stmt["summary"])
                stmt["gemini_relevanz"] = cached_data.get("relevanz_grund", "")
                stmt["gemini_status"] = "cached"
                final_statements.append(stmt)
            else:
                stmt["gemini_status"] = "filtered"
                filtered_out.append(stmt)
        else:
            stmt["_uid"] = uid
            to_process.append(stmt)

    print(f"Eintraege gesamt: {len(statements)} | Im Cache: {len(statements)-len(to_process)} | Neu zu pruefen: {len(to_process)}")

    quota_hit = False
    budget_hit = False
    api_calls_made = 0

    # 2. KI-Pruefung fuer neue Eintraege (Einzelverarbeitung mit PDF-Volltext)
    if to_process and GEMINI_API_KEY:
        for idx, stmt in enumerate(to_process):
            if quota_hit or budget_hit:
                break
            if api_calls_made >= MAX_AI_PER_RUN:
                budget_hit = True
                print(f"  Budget-Limit erreicht ({MAX_AI_PER_RUN} Calls). Rest folgt im naechsten Lauf.")
                break

            uid = stmt.pop("_uid", None)
            pdf_text = extract_pdf_text(stmt.get("pdf_url", ""))
            quelle = "PDF" if pdf_text else "Metadaten"
            print(f"  Eintrag {idx+1}/{len(to_process)} (SG {stmt.get('sg_number','?')}, Quelle: {quelle})...")

            result = call_gemini(build_single_prompt(stmt, pdf_text))
            api_calls_made += 1

            if result == "QUOTA_EXCEEDED":
                quota_hit = True
                notify_admin_error("Tageslimit (Quota) der Gemini API erreicht.")
                stmt["gemini_status"] = "pending"
                final_statements.append(stmt)
                break

            if not isinstance(result, dict):
                # Fehlgeschlagen: als pending markieren, spaeter erneut versuchen
                stmt["gemini_status"] = "pending"
                final_statements.append(stmt)
                time.sleep(REQUEST_DELAY)
                continue

            is_relevant = result.get("relevant", True)
            if uid:
                cache[uid] = {
                    "relevant": is_relevant,
                    "relevanz_grund": result.get("relevanz_grund", ""),
                    "zusammenfassung": result.get("zusammenfassung", ""),
                }
                save_cache(cache)

            if is_relevant:
                stmt["summary"] = result.get("zusammenfassung", stmt.get("summary", ""))
                stmt["gemini_relevanz"] = result.get("relevanz_grund", "")
                stmt["gemini_status"] = "processed"
                final_statements.append(stmt)
            else:
                stmt["gemini_status"] = "filtered"
                filtered_out.append(stmt)

            time.sleep(REQUEST_DELAY)

    # 3. Ungepruefte Reste (wegen Quota, Budget oder Fehler)
    pending_dates = set()
    for stmt in to_process:
        # Nur Eintraege die in Schritt 2 NICHT verarbeitet wurden
        if stmt.get("gemini_status"):
            continue

        stmt.pop("_uid", None)
        stmt["gemini_status"] = "pending"
        final_statements.append(stmt)

        d = stmt.get("sending_date") or stmt.get("upload_date")
        pending_dates.add(d if d else "unbekannt")

    final_statements.sort(key=lambda x: (x.get("upload_date") or "0000-00-00"), reverse=True)

    # 4. Daten speichern
    # WICHTIG: Aussortierte Statements ('gemini_status=filtered') bleiben im
    # data.json erhalten, damit fetch_and_build.py sie beim naechsten Lauf
    # nicht erneut als 'neu' erkennt (Cache-Kreislauf-Vermeidung). Sie werden
    # in HTML und E-Mail jedoch ausgeblendet (siehe render_entry_card-Aufrufer).
    all_statements_sorted = sorted(
        final_statements + filtered_out,
        key=lambda x: (x.get("upload_date") or "0000-00-00"),
        reverse=True
    )
    data["statements"] = all_statements_sorted
    data["gemini_filtered_out"] = len(filtered_out)  # Zahl, nicht Liste
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 5. HTML mit aktuellen Daten neu bauen (nur sichtbare Statements)
    generate_html(final_statements, data.get("generated_at", datetime.now().isoformat()), pending_dates)

    print(f"Fertig. {len(final_statements)} sichtbar, {len(filtered_out)} aussortiert (im data.json behalten) | "
          f"{api_calls_made} API-Calls | {len(pending_dates)} Tage pending")

if __name__ == "__main__":
    main()

"""
gemini_enrich.py
================
Reichert die Lobbyregister-Daten mit Gemini 3.1 Flash Lite an.
Grundlage der Zusammenfassung (gespeichert als "quelle"):
  pdf_text  – PDF-Volltext (Textebene der PDF)
  pdf_scan  – Bild-Scan ohne Textebene: die PDF wird als Datei an Gemini geschickt
  register  – Dokument nicht auswertbar: nur kurze, gekennzeichnete Wiedergabe der
              Beschreibung des Regelungsvorhabens (keine erfundenen Inhalte)
Zusaetzlich prueft Gemini, ob das Dokument erkennbar vom angegebenen
Regelungsvorhaben abweicht ("abweichung", "abweichung_hinweis").
Einzelverarbeitung: ein API-Call pro Stellungnahme (beim Scan-Rueckfall zwei).

Cache-Eintraege ohne "quelle" (Altbestand) werden im Restbudget nach den
neuen Eintraegen neu verarbeitet, juengste zuerst.

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
import base64
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
# Das Lobbyregister liefert PDFs nur aus, wenn (1) der Urheberrechtshinweis per
# Cookie bestaetigt ist (im Browser: Klick auf "Akzeptieren & Dokument
# herunterladen", Cookie 24 h gueltig) und (2) ein Referer von
# lobbyregister.bundestag.de mitgeschickt wird. Ohne Cookie wird auf die
# Hinweisseite, ohne Referer auf die Detailseite weitergeleitet (jeweils HTML).
PDF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LobbyregisterMonitor/1.0)",
    "Referer": "https://www.lobbyregister.bundestag.de/",
}
PDF_COOKIES = {"copyrightAcknowledgement": "true"}
PDF_TEXT_MAX_CHARS = 40000   # Kappung, um unter dem TPM-Limit (250K/min) zu bleiben
PDF_MIN_CHARS = 200          # darunter: vermutlich Bild-Scan ohne Textebene
# Bild-Scans werden als Datei an Gemini geschickt (Gemini liest die Seiten selbst).
# Grenzen halten Anfrage (inline max. ca. 20 MB) und Token-Verbrauch klein.
PDF_SCAN_MAX_BYTES = 12 * 1024 * 1024
PDF_SCAN_MAX_PAGES = 30

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

def call_gemini(prompt, pdf_bytes=None, retries=MAX_RETRIES):
    """Ruft Gemini auf. Mit pdf_bytes wird die PDF selbst mitgeschickt (fuer
    Bild-Scans ohne Textebene; Gemini liest die Seiten direkt).
    Rueckgabe: dict (Ergebnis), "QUOTA_EXCEEDED" oder None (Fehler)."""
    headers = {"Content-Type": "application/json"}
    params = {"key": GEMINI_API_KEY}
    parts = []
    if pdf_bytes:
        parts.append({"inline_data": {"mime_type": "application/pdf",
                                      "data": base64.b64encode(pdf_bytes).decode("ascii")}})
    parts.append({"text": prompt})
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    bt = chr(96) * 3
    for attempt in range(retries):
        try:
            resp = requests.post(GEMINI_URL, headers=headers, params=params, json=payload, timeout=120)
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
                try:
                    detail = resp.json().get("error", {}).get("message", "")[:200]
                except Exception:
                    detail = ""
                print(f"  API-Fehler {resp.status_code} {detail}")
                if 400 <= resp.status_code < 500:
                    return None   # Anfragefehler: Wiederholen bringt nichts
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

def fetch_pdf(pdf_url):
    """Laedt die PDF. Gibt die Bytes zurueck oder None (kein PDF erhalten)."""
    if not pdf_url or not pdf_url.lower().endswith(".pdf"):
        return None
    try:
        resp = requests.get(pdf_url, headers=PDF_HEADERS, cookies=PDF_COOKIES, timeout=60)
        if resp.status_code != 200:
            print(f"  PDF nicht abrufbar (HTTP {resp.status_code}): {pdf_url}")
            return None
        if not resp.content.startswith(b"%PDF"):
            # Kein PDF erhalten (z.B. Hinweis- oder Detailseite als HTML)
            print(f"  PDF nicht abrufbar, kein PDF erhalten ({resp.headers.get('Content-Type', '?')}): {pdf_url}")
            return None
        return resp.content
    except Exception as e:
        print(f"  PDF nicht abrufbar ({pdf_url}): {e}")
        return None

def extract_pdf_text(pdf_bytes):
    """Extrahiert den Text aus den PDF-Bytes. Gibt (text, seitenzahl) zurueck;
    text ist "", wenn die PDF keine (ausreichende) Textebene hat (Bild-Scan)."""
    if not PDF_AVAILABLE or not pdf_bytes:
        return "", 0
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        n_pages = len(reader.pages)
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
            return "", n_pages
        return text[:PDF_TEXT_MAX_CHARS], n_pages
    except Exception as e:
        print(f"  PDF-Textextraktion fehlgeschlagen: {e}")
        return "", 0

def build_single_prompt(stmt, quelle, pdf_text=""):
    """Prompt fuer eine Stellungnahme.
    quelle: "pdf_text" (Volltext im Prompt), "pdf_scan" (PDF liegt als Datei bei)
            oder "register" (nur Beschreibung des Regelungsvorhabens verfuegbar)."""
    title = stmt.get("regulatory_project_title", "Kein Titel")
    org = stmt.get("org_name", "")
    fields = ", ".join(f["label"] for f in stmt.get("fields", []))
    recipients = ", ".join(stmt.get("recipients", []))
    register_desc = stmt.get("register_description") or ""

    kopf = (
        "Du bist ein Analyst im Bundesministerium fuer Wirtschaft und Energie (BMWE).\n"
        "Pruefe Relevanz und fasse zusammen.\n\n"
        "RELEVANZPRUEFUNG:\n"
        f"{RELEVANZ_KATALOG}\n"
        "Ist der Eintrag relevant? (true/false). Massgeblich ist der tatsaechliche Inhalt "
        "des Dokuments, sofern vorhanden, nicht nur der Titel.\n\n"
        f"Einreichende Organisation: {org}\n"
        f"Regelungsvorhaben (von der Organisation zugeordnet): {title}\n"
        f"Beschreibung des Regelungsvorhabens (allgemeine Angabe der Organisation fuer alle "
        f"Stellungnahmen zu diesem Vorhaben, NICHT dokumentspezifisch): {register_desc or 'keine'}\n"
        f"Themenfelder: {fields}\nAdressaten: {recipients}\n\n"
    )
    markierung = (
        "MARKIERUNG mit <b>-Tags:\n"
        "Markiere die Woerter und kurzen Phrasen, die beim schnellen Ueberfliegen den Kern "
        "erfassen lassen (Taetigkeitswort der Organisation, konkrete Forderung/Aussage, "
        "spezifische Kernbegriffe). NICHT markieren: Woerter aus dem Titel, allgemeine "
        "Woerter (Stellungnahme, Unternehmen, Bereich), reine Gesetzesnummern aus dem Titel.\n"
    )

    if quelle in ("pdf_text", "pdf_scan"):
        if quelle == "pdf_text":
            inhalt = ("VOLLTEXT DER STELLUNGNAHME (aus der PDF extrahiert – Grundlage der "
                      "Zusammenfassung):\n" + pdf_text + "\n\n")
        else:
            inhalt = ("DOKUMENT: Die Stellungnahme liegt als PDF-Datei bei (Bild-Scan). Lies die "
                      "Seiten und nutze ihren Inhalt als Grundlage der Zusammenfassung.\n\n")
        auftrag = (
            "ZUSAMMENFASSUNG:\n"
            "Schreibe 5 bis 8 praegnante Saetze. Sachlich. Ohne Wertung. Nur Aussagen, die "
            "im Dokument stehen; nichts ergaenzen.\n"
            "ERSTER SATZ: Nennt zwingend (a) die Organisation und ihre Handlung/Haltung "
            "(z.B. fordert, lehnt ab, begruesst, empfiehlt, warnt vor, schlaegt vor, kritisiert, "
            "unterstuetzt, bezweifelt, beantragt, praezisiert) UND (b) den zentralen Gegenstand. "
            "Bezeichne die Organisation korrekt (Unternehmen nicht als 'Verband').\n"
            "FOLGENDE SAETZE: Konkretisieren die wichtigsten Details, Argumente und Forderungen.\n\n"
            + markierung + "Ziel: 4-7 markierte Phrasen.\n\n"
            "ABWEICHUNGSPRUEFUNG:\n"
            "1. Bestimme den HAUPTGEGENSTAND des Dokuments: Betreff/Ueberschrift und das Gesetz "
            "bzw. Vorhaben, um das es im Kern geht (Feld hauptgegenstand, kurz).\n"
            "2. Vergleiche ihn mit dem zugeordneten Regelungsvorhaben (Titel und Beschreibung).\n"
            "abweichung = true, wenn der Hauptgegenstand ein ANDERES Gesetz, Vorhaben oder Thema "
            "ist als das zugeordnete Regelungsvorhaben. Das gilt auch dann, wenn das zugeordnete "
            "Vorhaben im Dokument nur am Rande erwaehnt wird.\n"
            "abweichung = false, wenn der Hauptgegenstand das zugeordnete Vorhaben oder ein "
            "Teilaspekt davon ist (z.B. ein Vorhaben 'Strommarktdesign' und ein Dokument zu "
            "Netzentgelten im Strommarkt).\n"
            "abweichung_hinweis: bei Abweichung ein Satz, was das Dokument tatsaechlich "
            "behandelt (ohne <b>-Tags); sonst leer.\n\n"
        )
    else:
        inhalt = ("HINWEIS: Der Inhalt des Dokuments ist NICHT verfuegbar (PDF nicht abrufbar "
                  "oder nicht lesbar). Es liegt nur die allgemeine Beschreibung des "
                  "Regelungsvorhabens vor.\n\n")
        auftrag = (
            "ZUSAMMENFASSUNG:\n"
            "Hoechstens 2 Saetze. Beginne mit 'Laut Registerangabe zum Regelungsvorhaben'. "
            "Gib nur wieder, was in der Beschreibung des Regelungsvorhabens steht. Erfinde "
            "KEINE Akteure, Haltungen, Forderungen oder Details und stelle die Beschreibung "
            "nicht als Inhalt des Dokuments dar.\n"
            "Falls keine Beschreibung vorhanden: 'Keine inhaltliche Beschreibung verfuegbar.'\n\n"
            + markierung + "Ziel: hoechstens 3 markierte Phrasen.\n\n"
            "ABWEICHUNGSPRUEFUNG: nicht moeglich, setze abweichung = false und "
            "abweichung_hinweis = \"\".\n\n"
        )

    return (
        kopf + inhalt + auftrag +
        'Antworte als JSON-Objekt: {"relevant": true, "relevanz_grund": "...", '
        '"zusammenfassung": "...", "hauptgegenstand": "...", "abweichung": false, '
        '"abweichung_hinweis": ""}\n'
        "NUR das JSON-Objekt."
    )

def analyze_statement(stmt):
    """Fuehrt die KI-Pruefung fuer eine Stellungnahme durch.
    Reihenfolge: PDF-Volltext -> PDF als Datei (Bild-Scan) -> Registerbeschreibung.
    Rueckgabe: (ergebnis, quelle, anzahl_api_calls) mit
    ergebnis = dict | "QUOTA_EXCEEDED" | None."""
    pdf_bytes = fetch_pdf(stmt.get("pdf_url", ""))
    pdf_text, n_pages = extract_pdf_text(pdf_bytes)

    if pdf_text:
        return call_gemini(build_single_prompt(stmt, "pdf_text", pdf_text)), "pdf_text", 1

    if pdf_bytes and len(pdf_bytes) <= PDF_SCAN_MAX_BYTES and 0 < n_pages <= PDF_SCAN_MAX_PAGES:
        result = call_gemini(build_single_prompt(stmt, "pdf_scan"), pdf_bytes=pdf_bytes)
        if result == "QUOTA_EXCEEDED" or isinstance(result, dict):
            return result, "pdf_scan", 1
        print("  Scan-Auswertung fehlgeschlagen – Rueckfall auf Registerbeschreibung")
        time.sleep(REQUEST_DELAY)
        return call_gemini(build_single_prompt(stmt, "register")), "register", 2
    elif pdf_bytes:
        print(f"  Scan zu gross fuer Direktauswertung ({n_pages} Seiten, {len(pdf_bytes)//1024} KB) "
              "– Rueckfall auf Registerbeschreibung")

    return call_gemini(build_single_prompt(stmt, "register")), "register", 1

def apply_result(stmt, entry):
    """Uebertraegt ein (Cache-)Ergebnis auf das Statement."""
    stmt["summary"] = entry.get("zusammenfassung") or stmt.get("summary", "")
    stmt["gemini_relevanz"] = entry.get("relevanz_grund", "")
    if entry.get("quelle"):
        stmt["summary_quelle"] = entry["quelle"]
    else:
        stmt.pop("summary_quelle", None)
    if entry.get("abweichung") and entry.get("abweichung_hinweis"):
        stmt["abweichung"] = True
        stmt["abweichung_hinweis"] = entry["abweichung_hinweis"]
    else:
        stmt.pop("abweichung", None)
        stmt.pop("abweichung_hinweis", None)

def cache_entry_from_result(result, quelle):
    return {
        "relevant": result.get("relevant", True),
        "relevanz_grund": result.get("relevanz_grund", ""),
        "zusammenfassung": result.get("zusammenfassung", ""),
        "quelle": quelle,
        "abweichung": bool(result.get("abweichung")) and quelle != "register",
        "abweichung_hinweis": (result.get("abweichung_hinweis") or "").strip() if quelle != "register" else "",
        "geprueft_am": date.today().isoformat(),
    }

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

def esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

QUELLE_HINWEIS = {
    "pdf_scan": "Grundlage: PDF-Scan (von der KI gelesen)",
    "register": "Grundlage: nur Registerangabe zum Regelungsvorhaben – Dokument nicht auswertbar",
}

def quelle_note_html(stmt):
    note = QUELLE_HINWEIS.get(stmt.get("summary_quelle", ""))
    return f'<span class="quelle-note">{note}</span>' if note else ""

def abweichung_html(stmt, prefix=""):
    if not (stmt.get("abweichung") and stmt.get("abweichung_hinweis")):
        return ""
    return (f'<div class="row-warn">{prefix}⚠ <strong>Dokument weicht vom angegebenen '
            f'Regelungsvorhaben ab:</strong> {esc(stmt["abweichung_hinweis"])}</div>')

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
        f'{abweichung_html(stmt)}'
        f'<div class="row-full"><span class="row-label">Inhalt</span>{summary}{quelle_note_html(stmt)}</div>'
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
    warn_parts = []
    link_parts = []
    for i, m in enumerate(members):
        summary = m.get("summary", "") or "Keine Beschreibung verf\u00fcgbar."
        summary = re.sub(r'<(?!/?b>)', '&lt;', summary).replace('>', '&gt;').replace('<b&gt;', '<b>').replace('</b&gt;', '</b>')
        pending_badge = ""
        if m.get("gemini_status") == "pending":
            pending_badge = '<span style="font-size:0.7rem;font-weight:700;color:#94a3b8;margin-left:10px;">KI-Pr\u00fcfung ausstehend</span>'
        content_parts.append(f'<div style="margin-bottom:0.6rem"><strong>{i+1})</strong> {summary}{pending_badge}{quelle_note_html(m)}</div>')
        warn_parts.append(abweichung_html(m, prefix=f"{i+1}) "))

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
        f'{"".join(warn_parts)}'
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

def bucket(stmt, entry, final_statements, filtered_out, status):
    """Sortiert ein Statement anhand eines (Cache-)Ergebnisses ein."""
    if entry.get("relevant", True):
        apply_result(stmt, entry)
        stmt["gemini_status"] = status
        final_statements.append(stmt)
    else:
        apply_result(stmt, entry)
        stmt["gemini_status"] = "filtered"
        filtered_out.append(stmt)

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
    to_process = []      # neu, noch nie geprueft
    legacy = []          # im Cache, aber vor Einfuehrung der Quellenangabe geprueft
    final_statements = []
    filtered_out = []

    # 1. Cache-Abgleich
    for stmt in statements:
        # Fallback-Schluessel fuer Eintraege ohne SG-Nummer/URL (wie Deduplizierung in
        # fetch_and_build.py), damit auch sie im Cache landen und nicht taeglich neu
        # geprueft werden.
        uid = (stmt.get("sg_number") or stmt.get("statement_url") or stmt.get("pdf_url")
               or f"{stmt.get('register_number','')}|{stmt.get('regulatory_project_title','')}|{stmt.get('sending_date','')}")
        stmt["_uid"] = uid
        if uid and uid in cache:
            if "quelle" in cache[uid]:
                stmt.pop("_uid", None)
                bucket(stmt, cache[uid], final_statements, filtered_out, "cached")
            else:
                legacy.append(stmt)
        else:
            to_process.append(stmt)

    newest_first = lambda x: (x.get("upload_date") or "0000-00-00", x.get("sg_number") or "")
    to_process.sort(key=newest_first, reverse=True)
    legacy.sort(key=newest_first, reverse=True)
    queue = [(s, False) for s in to_process] + [(s, True) for s in legacy]

    print(f"Eintraege gesamt: {len(statements)} | Im Cache: {len(statements)-len(to_process)} | "
          f"Neu zu pruefen: {len(to_process)} | Altbestand zur Neuverarbeitung offen: {len(legacy)}")

    quota_hit = False
    api_calls_made = 0
    reprocessed = 0
    quellen = defaultdict(int)
    done = set()

    # 2. KI-Pruefung: zuerst neue Eintraege, dann Altbestand (jeweils juengste zuerst)
    if queue and GEMINI_API_KEY:
        for idx, (stmt, is_legacy) in enumerate(queue):
            if quota_hit:
                break
            if api_calls_made >= MAX_AI_PER_RUN:
                print(f"  Budget-Limit erreicht ({MAX_AI_PER_RUN} Calls). Rest folgt im naechsten Lauf.")
                break

            uid = stmt.get("_uid")
            art = "Altbestand" if is_legacy else "neu"
            print(f"  Eintrag {idx+1}/{len(queue)} ({art}, SG {stmt.get('sg_number','?')}, "
                  f"hochgeladen {stmt.get('upload_date','?')})...")

            result, quelle, n_calls = analyze_statement(stmt)
            api_calls_made += n_calls
            print(f"    Quelle: {quelle}")

            if result == "QUOTA_EXCEEDED":
                quota_hit = True
                notify_admin_error("Tageslimit (Quota) der Gemini API erreicht.")
                break

            if not isinstance(result, dict):
                # Fehlgeschlagen: neue Eintraege bleiben 'pending', Altbestand behaelt
                # seine bisherige Zusammenfassung (Schritt 3).
                time.sleep(REQUEST_DELAY)
                continue

            entry = cache_entry_from_result(result, quelle)
            if uid:
                cache[uid] = entry
                save_cache(cache)
            stmt.pop("_uid", None)
            bucket(stmt, entry, final_statements, filtered_out, "processed")
            done.add(id(stmt))
            quellen[quelle] += 1
            if is_legacy:
                reprocessed += 1
            if entry["abweichung"]:
                print(f"    ⚠ Abweichung: {entry['abweichung_hinweis']}")

            time.sleep(REQUEST_DELAY)

    # 3. Nicht verarbeitete Reste
    pending_dates = set()
    for stmt, is_legacy in queue:
        if id(stmt) in done:
            continue
        uid = stmt.pop("_uid", None)
        if is_legacy:
            # Bisheriges Ergebnis bleibt bis zur Neuverarbeitung bestehen
            bucket(stmt, cache[uid], final_statements, filtered_out, "cached")
        else:
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
    data["gemini_legacy_open"] = len(legacy) - reprocessed
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 5. HTML mit aktuellen Daten neu bauen (nur sichtbare Statements)
    generate_html(final_statements, data.get("generated_at", datetime.now().isoformat()), pending_dates)

    quellen_str = ", ".join(f"{k}: {v}" for k, v in sorted(quellen.items())) or "keine"
    print(f"Fertig. {len(final_statements)} sichtbar, {len(filtered_out)} aussortiert (im data.json behalten) | "
          f"{api_calls_made} API-Calls | Quellen: {quellen_str} | "
          f"Altbestand neu verarbeitet: {reprocessed}, offen: {len(legacy) - reprocessed} | "
          f"{len(pending_dates)} Tage pending")

if __name__ == "__main__":
    main()

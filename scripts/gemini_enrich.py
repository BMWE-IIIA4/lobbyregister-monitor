"""
gemini_enrich.py
================
Reichert die Lobbyregister-Daten mit Gemini Flash-Lite an.
Mit Caching, Fail-Fast bei Quota-Limits und automatischem HTML-Rebuild.
"""

import json
import os
import time
import re
import sys
import requests
from pathlib import Path
from collections import defaultdict
from datetime import datetime, date

# ── Konfiguration ──────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

REQUEST_DELAY = 5.0
MAX_RETRIES = 3
RETRY_DELAY = 10
BATCH_SIZE = 5

DATA_PATH = Path("docs/data.json")
CACHE_PATH = Path("docs/gemini_cache.json")
TEMPLATE_PATH = Path("scripts/template.html")
SITE_URL = "https://lobbyregister-bot.de"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

# ── Relevanzkatalog ────────────────────────────────────────────────────────────

RELEVANZ_KATALOG = (
    "THEMENFELDER DER UNTERABTEILUNG IIIA (BMWE) – Referenz für Relevanzprüfung:\n\n"
    "EU-ENERGIEPOLITIK & ENERGIEGESETZGEBUNG:\n"
    "- EU-Energieszenarien, EU-Zielarchitektur 2030/2040\n"
    "- Erneuerbare-Energien-Richtlinie (RED), EU-Notfallverordnungen\n"
    "- EU-Governance-Verordnung, Energieunion, NECP\n"
    "- Clean Industrial Deal, EU Green Deal, RePowerEU, CISAF, Temporary Crisis Framework\n"
    "- Verordnung (EU) 2019/943 des Europäischen Parlaments über den Elektrizitätsbinnenmarkt\n"
    "- Richtlinie (EU) 2019/944 des Europäischen Parlaments mit gemeinsamen Vorschriften für den Elektrizitätsbinnenmarkt\n"
    "- ACER\n"
    "- EU-Beihilferecht Erneuerbare Energien, EU-Rahmenbedingungen EE-Fördersysteme\n"
    "- EU-Rahmenbedingungen erneuerbarer Wasserstoff (RFNBO)\n"
    "- Grenzüberschreitende EE-Kooperationsprojekte, Offshore-Kooperationen\n"
    "- Nordseekooperation, Pentalaterales Energieforum\n"
    "- EU-Klimapolitik, EU-Klimagesetz, Fit for 55\n"
    "- EU-Energiepreise, Wettbewerbsfähigkeit im EU-Binnenmarkt, EU-Energiesteuern\n"
    "- EU-Wasserstoffmarkt, EU-Energieeffizienz, CCS/CCU\n"
    "- Dekarbonisierung europäische Energieerzeugung\n\n"
    "BILATERALE ENERGIEBEZIEHUNGEN:\n"
    "- DFBEW, Energiepolitische Beziehungen zu EU-Mitgliedstaaten, Norwegen, Schweiz, UK\n\n"
    "VERSORGUNGSSICHERHEIT STROM:\n"
    "- Monitoring Versorgungssicherheit, Kapazitätsreserve, Netzreserve\n\n"
    "STROMMARKTDESIGN & -REGULIERUNG:\n"
    "- Kapazitätsmechanismen, Börsenhandel/OTC-Märkte, Regelenergiemärkte\n"
    "- Stromgebotszonen, Flexibilisierung, REMIT, Netzwerkcodes\n"
    "- Redispatch, Netzoptimierung, Plattform Klimaneutrales Stromsystem\n\n"
    "STROMERZEUGUNG & KRAFTWERKE:\n"
    "- Kohleausstieg, Kraftwerksstrategie, KWK (KWKG), Wasserstoffkraftwerke\n"
    "- StromVKG\n\n"
    "ERNEUERBARE ENERGIEN (NATIONAL):\n"
    "- EEG-Finanzierung, Besondere Ausgleichsregelung, PPA, Eigenverbrauch\n\n"
    "WASSERSTOFF:\n"
    "- Wasserstoffkernnetz, Elektrolyseure, RFNBO, Sektorkopplung\n\n"
    "KLIMASCHUTZ & ENERGIEWENDE:\n"
    "- Langfristszenarien, SES, Szenariorahmen NEP, Projektionsbericht\n"
    "- Sektorkopplung, Finanzierungsbedarfe Transformation\n"
    "- Reform klimarelevanter Steuern/Abgaben/Umlagen\n\n"
    "ENERGIEPREISE & -KOSTEN:\n"
    "- Großhandelspreise, Endverbraucherpreise, internationale Energiepreise\n\n"
    "ENERGIEMONITORING & -STATISTIK:\n"
    "- Monitoring-Berichte Energiewende, AGEE-Stat, Treibhausgasemissionen\n\n"
    "NICHT RELEVANT (kein Bezug zum Aufgabenportfolio):\n"
    "- Rein parteipolitische Finanzierung, Medienrecht, Datenschutz ohne Energiebezug\n"
    "- Arbeitsrecht, Verbraucherschutz, Kulturpolitik, Sportpolitik ohne Energiebezug\n"
    "- Verteidigungspolitik, Gesundheitspolitik, Pharmarecht ohne Energiebezug\n"
    "- Verkehrspolitik ohne Bezug zu Sektorkopplung/E-Mobilität/Kraftstoffen\n"
    "- Bauwesen ohne Bezug zu Gebäudeenergie/Wärmewende"
)

# ── Gemini API & Cache ─────────────────────────────────────────────────────────

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

def build_batch_prompt(batch):
    entries_text = ""
    for i, stmt in enumerate(batch):
        title = stmt.get("regulatory_project_title", "Kein Titel")
        summary = stmt.get("summary", "")
        fields = ", ".join(f["label"] for f in stmt.get("fields", []))
        recipients = ", ".join(stmt.get("recipients", []))
        entries_text += f"\n--- Eintrag {i + 1} ---\nTitel: {title}\nThemenfelder: {fields}\nAdressaten: {recipients}\nBeschreibung: {summary}\n"

    return (
        "Du bist ein Analyst im Bundesministerium fuer Wirtschaft und Energie (BMWE).\n"
        "Pruefe Relevanz und fasse zusammen:\n\n"
        "RELEVANZPRUEFUNG:\n"
        "Nutze den folgenden Referenzkatalog:\n"
        f"{RELEVANZ_KATALOG}\n"
        "Ist der Eintrag relevant? (true/false).\n\n"
        "ZUSAMMENFASSUNG:\n"
        "- 2 bis 5 Saetze. Sachlich. Ohne Wertung.\n"
        "- Markiere die 2-4 wichtigsten Kernforderungen oder Hauptthemen (Wortgruppen) mit <b>-Tags.\n"
        "- Falls leer: 'Keine inhaltliche Beschreibung verfuegbar.'\n\n"
        f"Eintraege:{entries_text}\n\n"
        f"Antworte als JSON-Array mit {len(batch)} Objekten:\n"
        "[ {\"index\": 1, \"relevant\": true, \"relevanz_grund\": \"...\", \"zusammenfassung\": \"...\"} ]\n"
        "NUR das JSON-Array."
    )

# ── HTML Generierung ───────────────────────────────────────────────────────────

def format_date_de(iso_date):
    if not iso_date: return "–"
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return iso_date

def get_weekday_de(iso_date):
    days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    months = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
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
    upload = format_date_de(stmt.get("upload_date"))
    
    summary = stmt.get("summary", "") or "Keine Beschreibung verfügbar."
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
    stmt_link = f'<a href="{statement_url}" target="_blank">↗ Stellungnahme im Lobbyregister {sg_number}</a>' if statement_url else ''
    pdf_link = f'<a href="{pdf_url}" target="_blank">↗ PDF herunterladen ({pdf_pages} Seiten)</a>' if pdf_url else '<span style="color:#999">Kein PDF</span>'

    unverified_badge = ""
    if stmt.get("gemini_status") == "pending":
        unverified_badge = '<span style="display:inline-block; font-size:11px; background:#fef08a; color:#854d0e; padding:2px 6px; border-radius:4px; border:1px solid #fde047; margin-left:10px;">⏳ KI-Prüfung ausstehend</span>'

    return (
        f'<div class="entry-card" data-vorhaben="{title}">'
        f'<div class="row-title">{title}{unverified_badge}</div>'
        f'<div class="meta-row">'
        f'<div class="mc grow"><strong>Bereitgestellt von</strong>{org_html}</div>'
        f'<div class="mc fixd"><strong>Datum Stellungnahme</strong>{sending}</div>'
        f'<div class="mc fixd"><strong>Hochgeladen am</strong>{upload}</div>'
        f'</div>'
        f'<div class="meta-row two-col">'
        f'<div class="mc half"><strong>Adressaten</strong>{recip_badges}</div>'
        f'<div class="mc half"><strong>Themenfelder der Stellungnahme</strong>{field_tags}</div>'
        f'</div>'
        f'<div class="row-full"><strong>Inhalt</strong>{summary}</div>'
        f'<div class="link-row">'
        f'<div class="lc">{stmt_link}</div>'
        f'<div class="lc">{pdf_link}</div>'
        f'</div></div>'
    )

def generate_html(statements, generated_at, pending_dates):
    by_date = defaultdict(list)
    vorhaben_counts = defaultdict(int)
    
    for stmt in statements:
        key = stmt.get("sending_date") or stmt.get("upload_date") or "unbekannt"
        by_date[key].append(stmt)
        vorhaben_counts[stmt["regulatory_project_title"]] += 1

    day_sections_html = ""
    
    if pending_dates:
        dates_str = ", ".join([format_date_de(d) if d != "unbekannt" else "unbekanntem Datum" for d in sorted(pending_dates, reverse=True)])
        day_sections_html += (
            f'<div style="background:#fefce8; color:#854d0e; padding:12px 16px; margin-bottom:20px; border-radius:8px; border:1px solid #fde047; font-size: 0.95rem;">'
            f'<b>Hinweis zum API-Limit:</b> Aufgrund hoher Serverauslastung konnten Einträge vom <b>{dates_str}</b> noch nicht durch die KI auf Relevanz geprüft und zusammengefasst werden. Sie werden temporär ungefiltert angezeigt. Die Prüfung wird beim nächsten Durchlauf automatisch nachgeholt.'
            f'</div>'
        )

    for iso_date, day_stmts in sorted(by_date.items(), reverse=True):
        day_stmts_sorted = sorted(day_stmts, key=lambda x: x.get("priority", 99))
        day_sections_html += (
            f'<div class="day-section" data-date="{iso_date}">'
            f'<div class="day-header">{get_weekday_de(iso_date)}</div>'
            f'{"".join(render_entry_card(s) for s in day_stmts_sorted)}'
            f'</div>'
        )

    filter_items = "".join(f'<li data-v="{v.replace(chr(34), chr(39))}"><span>{v}</span><span class="filter-count">{c}</span></li>' for v, c in sorted(vorhaben_counts.items(), key=lambda x: -x[1]))
    
    gen_dt = datetime.fromisoformat(generated_at)
    months_de = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    html = html.replace("{{DAY_SECTIONS}}", day_sections_html)
    html = html.replace("{{FILTER_ITEMS}}", filter_items)
    html = html.replace("{{GENERATED_AT}}", f"{gen_dt.day}. {months_de[gen_dt.month]} {gen_dt.year}, {gen_dt.strftime('%H:%M')} Uhr")
    html = html.replace("{{TOTAL_COUNT}}", str(len(statements)))
    html = html.replace("{{FIELDS_SUBTITLE}}", "Energie &amp; Wasserstoff, Klimaschutz, EU-Binnenmarkt, EU-Gesetzgebung, Bundestag, Wettbewerbsrecht, Politisches Leben/Parteien, Sonstige")
    html = html.replace("{{SITE_URL}}", SITE_URL)
    
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

def notify_admin_error(error_summary):
    if not RESEND_API_KEY or not ADMIN_EMAIL: return
    today = date.today().strftime("%d.%m.%Y")
    
    html = (
        f'<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;background:#f5f5f5;padding:20px;">'
        f'<div style="max-width:600px;margin:0 auto;background:#fff;padding:24px;border-top:4px solid #e65100;">'
        f'<h2 style="margin-top:0;">Gemini-Fehler</h2>'
        f'<p><strong>Problem:</strong> {error_summary}</p>'
        f'</div></div>'
    )
    
    try:
        requests.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                      json={"from": "onboarding@resend.dev", "to": [ADMIN_EMAIL], "subject": f"Lobbyregister-Monitor: Gemini fehlgeschlagen ({today})", "html": html}, timeout=30)
    except Exception:
        pass

def process_batch(batch, batch_num, total_batches):
    prompt = build_batch_prompt(batch)
    print(f"  Batch {batch_num}/{total_batches} ({len(batch)} Eintraege)...")

    result = call_gemini(prompt)
    if result is None:
        print(f"  ! Batch {batch_num} fehlgeschlagen")
        return None
    if result == "QUOTA_EXCEEDED":
        return "QUOTA_EXCEEDED"

    if not isinstance(result, list) or len(result) != len(batch):
        print(f"  ! Batch {batch_num}: Format-Fehler")
        return None

    return result

# ── Hauptlogik ─────────────────────────────────────────────────────────────────

def main():
    print("=== Lobbyregister Monitor – Gemini Anreicherung & Caching ===")
    
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
        if uid in cache:
            cached_data = cache[uid]
            if cached_data.get("relevant", True):
                stmt["summary"] = cached_data.get("zusammenfassung", stmt["summary"])
                stmt["gemini_relevanz"] = cached_data.get("relevanz_grund", "")
                stmt["gemini_status"] = "cached"
                final_statements.append(stmt)
            else:
                filtered_out.append(stmt)
        else:
            stmt["uid"] = uid
            to_process.append(stmt)

    print(f"Einträge gesamt: {len(statements)} | Im Cache: {len(statements)-len(to_process)} | Neu zu prüfen: {len(to_process)}")

    quota_hit = False
    
    # 2. KI-Prüfung für neue Einträge
    if to_process and GEMINI_API_KEY:
        batches = [to_process[i:i+BATCH_SIZE] for i in range(0, len(to_process), BATCH_SIZE)]
        
        for bi, batch in enumerate(batches):
            if quota_hit: break
            
            result = process_batch(batch, bi+1, len(batches))
            
            if result == "QUOTA_EXCEEDED":
                quota_hit = True
                notify_admin_error("Tageslimit (Quota) der Gemini API erreicht. Verbleibende Einträge werden übersprungen.")
                break
                
            if result:
                for j, item in enumerate(result):
                    stmt = batch[j]
                    uid = stmt.pop("uid", None)
                    is_relevant = item.get("relevant", True)
                    
                    cache[uid] = {
                        "relevant": is_relevant,
                        "relevanz_grund": item.get("relevanz_grund", ""),
                        "zusammenfassung": item.get("zusammenfassung", "")
                    }
                    
                    if is_relevant:
                        stmt["summary"] = item.get("zusammenfassung", stmt["summary"])
                        stmt["gemini_relevanz"] = item.get("relevanz_grund", "")
                        stmt["gemini_status"] = "processed"
                        final_statements.append(stmt)
                    else:
                        filtered_out.append(stmt)
                        
                save_cache(cache)
            time.sleep(REQUEST_DELAY)

    # 3. Ungeprüfte Reste behandeln (wegen Quota oder Fehler)
    pending_dates = set()
    for stmt in to_process:
        uid = stmt.get("uid")
        if uid not in cache:
            stmt.pop("uid", None)
            stmt["gemini_status"] = "pending"
            final_statements.append(stmt)
            
            d = stmt.get("sending_date") or stmt.get("upload_date")
            if d:
                pending_dates.add(d)
            else:
                pending_dates.add("unbekannt")

    final_statements.sort(key=lambda x: (x.get("upload_date") or "0000-00-00"), reverse=True)
    
    # 4. Daten speichern
    data["statements"] = final_statements
    data["gemini_filtered_out"] = filtered_out
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 5. HTML mit aktuellen Daten neu bauen
    generate_html(final_statements, data.get("generated_at", datetime.now().isoformat()), pending_dates)

    print(f"Fertig. {len(final_statements)} Einträge im finalen HTML.")

if __name__ == "__main__":
    main()
   

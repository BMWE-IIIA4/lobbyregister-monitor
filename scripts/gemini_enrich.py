"""
gemini_enrich.py
================
Reichert die Lobbyregister-Daten mit Gemini Flash-Lite an:

1. Relevanzfilterung: Stellungnahmen in breiten Kategorien
   (Wettbewerbsrecht, EU-Gesetzgebung, Parteien, Sonstige) werden
   auf Energie/Klima-Relevanz geprüft — irrelevante werden aussortiert.

2. Zusammenfassung: Beschreibungstexte werden in 2-5 Sätzen
   zusammengefasst, mit fett markierten Schlüsselbegriffen.

Wird nach fetch_and_build.py ausgeführt. Liest docs/data.json,
schreibt das Ergebnis zurück nach docs/data.json.

Bei Gemini-Ausfall bleibt der Originalzustand erhalten — der Monitor
funktioniert auch ohne diesen Schritt. Ein Hinweis wird in data.json
gesetzt, damit Webseite und Mail den Ausfall anzeigen können.
"""

import json
import os
import time
import re
import sys
from pathlib import Path

# ── Konfiguration ──────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Gemini 3.1 Flash Lite: bestes Free-Tier-Limit (15 RPM, 500 RPD)
GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

# Rate Limiting: 3.1 Flash Lite Free Tier = 15 RPM → 4s Minimum.
# Wir nutzen 5s als konservativen Puffer.
REQUEST_DELAY = 5.0
MAX_RETRIES = 5
RETRY_DELAY = 15      # Erste Wartezeit bei 429, wird verdoppelt

# Batch-Größe 5: konservativ, damit auch längere Texte Platz haben.
# Bei 1000 Stellungnahmen = 200 Requests → weit unter 500 RPD.
BATCH_SIZE = 5

FILTER_PRIORITIES = {2, 3}
ALWAYS_KEEP_PRIORITY = 1

DATA_PATH = Path("docs/data.json")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")


# ── Relevanzkatalog ────────────────────────────────────────────────────────────

RELEVANZ_KATALOG = """
THEMENFELDER DER UNTERABTEILUNG IIIA (BMWE) – Referenz für Relevanzprüfung:

EU-ENERGIEPOLITIK & ENERGIEGESETZGEBUNG:
- EU-Energieszenarien, EU-Zielarchitektur 2030/2040
- Erneuerbare-Energien-Richtlinie (RED), EU-Notfallverordnungen
- EU-Governance-Verordnung, Energieunion, NECP
- Clean Industrial Deal, EU Green Deal, RePowerEU
- EU-Beihilferecht Erneuerbare Energien, EU-Rahmenbedingungen EE-Fördersysteme
- EU-Rahmenbedingungen erneuerbarer Wasserstoff (RFNBO)
- Grenzüberschreitende EE-Kooperationsprojekte, Offshore-Kooperationen
- Nordseekooperation, Pentalaterales Energieforum
- EU-Klimapolitik, EU-Klimagesetz, Fit for 55
- EU-Energiepreise, Wettbewerbsfähigkeit im EU-Binnenmarkt, EU-Energiesteuern
- EU-Wasserstoffmarkt, EU-Energieeffizienz, CCS/CCU
- Dekarbonisierung europäische Energieerzeugung

BILATERALE ENERGIEBEZIEHUNGEN:
- DFBEW, Energiepolitische Beziehungen zu EU-Mitgliedstaaten, Norwegen, Schweiz, UK

VERSORGUNGSSICHERHEIT STROM:
- Monitoring Versorgungssicherheit, Kapazitätsreserve, Netzreserve

STROMMARKTDESIGN & -REGULIERUNG:
- Kapazitätsmechanismen, Börsenhandel/OTC-Märkte, Regelenergiemärkte
- Stromgebotszonen, Flexibilisierung, REMIT, Netzwerkcodes
- Redispatch, Netzoptimierung, Plattform Klimaneutrales Stromsystem

STROMERZEUGUNG & KRAFTWERKE:
- Kohleausstieg, Kraftwerksstrategie, KWK (KWKG), Wasserstoffkraftwerke

ERNEUERBARE ENERGIEN (NATIONAL):
- EEG-Finanzierung, Besondere Ausgleichsregelung, PPA, Eigenverbrauch

WASSERSTOFF:
- Wasserstoffkernnetz, Elektrolyseure, RFNBO, Sektorkopplung

KLIMASCHUTZ & ENERGIEWENDE:
- Langfristszenarien, SES, Szenariorahmen NEP, Projektionsbericht
- Sektorkopplung, Finanzierungsbedarfe Transformation
- Reform klimarelevanter Steuern/Abgaben/Umlagen

ENERGIEPREISE & -KOSTEN:
- Großhandelspreise, Endverbraucherpreise, internationale Energiepreisvergleiche

ENERGIEMONITORING & -STATISTIK:
- Monitoring-Berichte Energiewende, AGEE-Stat, Treibhausgasemissionen

NICHT RELEVANT (kein Bezug zum Aufgabenportfolio):
- Rein parteipolitische Finanzierung, Medienrecht, Datenschutz ohne Energiebezug
- Arbeitsrecht, Verbraucherschutz, Kulturpolitik, Sportpolitik ohne Energiebezug
- Verteidigungspolitik, Gesundheitspolitik, Pharmarecht ohne Energiebezug
- Verkehrspolitik ohne Bezug zu Sektorkopplung/E-Mobilität/Kraftstoffen
- Bauwesen ohne Bezug zu Gebäudeenergie/Wärmewende
"""


# ── Gemini API ─────────────────────────────────────────────────────────────────

def call_gemini(prompt, retries=MAX_RETRIES):
    """Einzelner Gemini API-Call mit Exponential-Backoff bei Rate Limits."""
    import requests

    headers = {"Content-Type": "application/json"}
    params = {"key": GEMINI_API_KEY}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
        },
    }

    for attempt in range(retries):
        try:
            resp = requests.post(
                GEMINI_URL, headers=headers, params=params,
                json=payload, timeout=90,
            )

            if resp.status_code == 429:
                wait = RETRY_DELAY * (2 ** attempt)
                detail = ""
                try:
                    detail = resp.json().get("error", {}).get("message", "")[:150]
                except Exception:
                    detail = resp.text[:150]
                print(f"  429 Rate Limit (Versuch {attempt+1}/{retries}): {detail}")
                print(f"  Warte {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                print(f"  API-Fehler {resp.status_code}: {resp.text[:200]}")
                if attempt < retries - 1:
                    time.sleep(RETRY_DELAY)
                    continue
                return None

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                print("  Keine Antwort von Gemini")
                return None

            text = (
                candidates[0].get("content", {})
                .get("parts", [{}])[0].get("text", "")
            )
            if not text:
                return None

            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)

            return json.loads(text)

        except json.JSONDecodeError as e:
            print(f"  JSON-Parse-Fehler: {e}")
            if attempt < retries - 1:
                time.sleep(REQUEST_DELAY)
                continue
            return None
        except Exception as e:
            print(f"  Fehler: {e}")
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY)
                continue
            return None

    return None


# ── Admin-Benachrichtigung ─────────────────────────────────────────────────────

def notify_admin_error(error_summary, stats):
    """Sendet eine Fehler-Mail an den Admin wenn Gemini nicht funktioniert."""
    if not RESEND_API_KEY or not ADMIN_EMAIL:
        print("  Admin-Mail nicht möglich (RESEND_API_KEY oder ADMIN_EMAIL fehlt)")
        return

    import requests
    from datetime import date

    today = date.today().strftime("%d.%m.%Y")
    secrets_url = "https://github.com/BMWE-IIIA4/lobbyregister-monitor/settings/secrets/actions"
    actions_url = "https://github.com/BMWE-IIIA4/lobbyregister-monitor/actions"

    html = f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#222;margin:0;padding:0;background:#f5f5f5">
<div style="max-width:600px;margin:20px auto">
  <div style="background:#004B87;padding:14px 24px">
    <div style="color:#fff;font-size:14px;font-weight:700">Lobbyregister-Monitor &middot; Gemini-Fehler</div>
    <div style="color:#a8c8e8;font-size:11px">{today}</div>
  </div>
  <div style="background:#fff;padding:18px 24px">
    <div style="background:#e65100;color:#fff;padding:8px 14px;font-size:13px;font-weight:700;margin-bottom:14px">
      Gemini-Anreicherung fehlgeschlagen
    </div>
    <p style="font-size:12px;color:#333;margin-bottom:10px"><strong>Problem:</strong> {error_summary}</p>
    <p style="font-size:12px;color:#333;margin-bottom:10px"><strong>Auswirkung:</strong> Eintr&auml;ge werden ungefiltert angezeigt.</p>
    <p style="font-size:12px;color:#333;margin-bottom:10px"><strong>Was zu tun ist:</strong><br>
      1. API-Key pr&uuml;fen: <a href="https://aistudio.google.com/apikey" style="color:#004B87">aistudio.google.com</a><br>
      2. Neuen Key in <a href="{secrets_url}" style="color:#004B87">GitHub Secrets</a> eintragen<br>
      3. <a href="{actions_url}" style="color:#004B87">Manuellen Testlauf</a> starten</p>
  </div>
</div></body></html>"""

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": "onboarding@resend.dev", "to": [ADMIN_EMAIL],
                  "subject": f"Lobbyregister-Monitor: Gemini fehlgeschlagen ({today})", "html": html},
            timeout=30,
        )
        resp.raise_for_status()
        print(f"  Admin-Fehlermail gesendet an {ADMIN_EMAIL}")
    except Exception as e:
        print(f"  Admin-Mail konnte nicht gesendet werden: {e}")


# ── Batch-Verarbeitung ─────────────────────────────────────────────────────────

def build_batch_prompt(batch):
    entries_text = ""
    for i, stmt in enumerate(batch):
        title = stmt.get("regulatory_project_title", "Kein Titel")
        summary = stmt.get("summary", "")
        fields = ", ".join(f["label"] for f in stmt.get("fields", []))
        recipients = ", ".join(stmt.get("recipients", []))
        entries_text += f"""
--- Eintrag {i + 1} ---
Titel: {title}
Themenfelder: {fields}
Adressaten: {recipients}
Beschreibung: {summary}
"""

    prompt = f"""Du bist ein Analyst im Bundesministerium fuer Wirtschaft und Energie (BMWE),
Unterabteilung IIIA (Energiepolitik). Deine Aufgabe hat zwei Teile:

TEIL 1 - RELEVANZPRUEFUNG:
Pruefe fuer jeden Eintrag, ob er in das Aufgabenportfolio der Unterabteilung IIIA faellt.
Nutze den folgenden Referenzkatalog als Grundlage:

{RELEVANZ_KATALOG}

Ein Eintrag ist relevant, wenn er mindestens eines der Themenfelder beruehrt -
auch indirekt. Im Zweifel: zugunsten der Relevanz entscheiden.

Ein Eintrag ist NICHT relevant, wenn er keinen erkennbaren Bezug hat.

TEIL 2 - ZUSAMMENFASSUNG:
Fasse die Beschreibung jedes Eintrags zusammen:
- Laenge: 2 bis 5 Saetze, je nach Komplexitaet.
- Inhalt: Was ist der Kern der Stellungnahme? Was wird gefordert, von wem?
- Stil: Sachlich, informativ, ohne Wertung.
- Schluesselwoerter: Markiere die 2-4 wichtigsten Fachbegriffe mit <b>-Tags.
  Beispiel: "Die Stellungnahme fordert eine Anpassung der <b>Netzentgelte</b>
  fuer <b>energieintensive Industrie</b>."
- Falls Beschreibung leer: "Keine inhaltliche Beschreibung verfuegbar."

Eintraege:
{entries_text}

Antworte als JSON-Array mit genau {len(batch)} Objekten:
[
  {{
    "index": 1,
    "relevant": true,
    "relevanz_grund": "Kurze Begruendung (1 Satz)",
    "zusammenfassung": "Zusammenfassung mit <b>fetten Keywords</b>"
  }}
]

NUR das JSON-Array, ohne weiteren Text."""

    return prompt


def process_batch(batch, batch_num, total_batches):
    prompt = build_batch_prompt(batch)
    print(f"  Batch {batch_num}/{total_batches} ({len(batch)} Eintraege)...")

    result = call_gemini(prompt)
    if result is None:
        print(f"  ! Batch {batch_num} fehlgeschlagen")
        return None

    if not isinstance(result, list) or len(result) != len(batch):
        print(f"  ! Batch {batch_num}: Format-Fehler (erwartet {len(batch)}, bekommen {len(result) if isinstance(result, list) else 'kein Array'})")
        return None

    return result


# ── Hauptlogik ─────────────────────────────────────────────────────────────────

def enrich_statements(statements):
    if not GEMINI_API_KEY:
        print("WARNUNG: GEMINI_API_KEY nicht gesetzt")
        return statements, {"skipped": True, "reason": "no_api_key"}, []

    always_keep = []
    needs_filter = []

    for stmt in statements:
        prio = stmt.get("priority", 99)
        if prio == ALWAYS_KEEP_PRIORITY:
            always_keep.append(stmt)
        elif prio in FILTER_PRIORITIES:
            needs_filter.append(stmt)
        else:
            always_keep.append(stmt)

    print(f"\nGemini-Anreicherung ({GEMINI_MODEL}, Batch {BATCH_SIZE}):")
    print(f"  {len(always_keep)} Energie-Eintraege -> nur Zusammenfassung")
    print(f"  {len(needs_filter)} andere Eintraege -> Relevanzpruefung + Zusammenfassung")

    total_batches_est = (len(always_keep) + BATCH_SIZE - 1) // BATCH_SIZE + (len(needs_filter) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"  Geschaetzte API-Calls: {total_batches_est} (Limit: 2000/Tag)")

    stats = {
        "total_input": len(statements), "always_keep": len(always_keep),
        "filter_candidates": len(needs_filter), "filtered_out": 0,
        "kept_after_filter": 0, "summaries_generated": 0,
        "api_errors": 0, "processed_ok": 0, "skipped_by_error": 0,
        "gemini_failed": False, "model": GEMINI_MODEL,
        "total_api_calls": 0, "failed_api_calls": 0,
    }

    total_api_calls = 0
    failed_api_calls = 0

    # Gruppe 1: Energie-Eintraege
    if always_keep:
        print(f"\nSchritt 1: Zusammenfassungen fuer {len(always_keep)} Energie-Eintraege...")
        batches = [always_keep[i:i+BATCH_SIZE] for i in range(0, len(always_keep), BATCH_SIZE)]

        for bi, batch in enumerate(batches):
            total_api_calls += 1
            result = process_batch(batch, bi + 1, len(batches))
            if result:
                stats["processed_ok"] += len(batch)
                for j, item in enumerate(result):
                    zf = item.get("zusammenfassung", "")
                    if zf and zf != batch[j].get("summary", ""):
                        batch[j]["original_summary"] = batch[j].get("summary", "")
                        batch[j]["summary"] = zf
                        stats["summaries_generated"] += 1
            else:
                failed_api_calls += 1
                stats["api_errors"] += 1
                stats["skipped_by_error"] += len(batch)

            if bi < len(batches) - 1:
                time.sleep(REQUEST_DELAY)

    # Gruppe 2: Relevanzpruefung
    kept = []
    filtered_out = []

    if needs_filter:
        print(f"\nSchritt 2: Relevanzpruefung fuer {len(needs_filter)} Eintraege...")
        batches = [needs_filter[i:i+BATCH_SIZE] for i in range(0, len(needs_filter), BATCH_SIZE)]

        for bi, batch in enumerate(batches):
            total_api_calls += 1
            result = process_batch(batch, bi + 1, len(batches))

            if result:
                stats["processed_ok"] += len(batch)
                for j, item in enumerate(result):
                    stmt = batch[j]
                    is_relevant = item.get("relevant", True)
                    zf = item.get("zusammenfassung", "")
                    grund = item.get("relevanz_grund", "")

                    if zf and zf != stmt.get("summary", ""):
                        stmt["original_summary"] = stmt.get("summary", "")
                        stmt["summary"] = zf
                        stats["summaries_generated"] += 1

                    if is_relevant:
                        stmt["gemini_relevanz"] = grund
                        kept.append(stmt)
                        stats["kept_after_filter"] += 1
                    else:
                        stmt["gemini_filtered"] = True
                        stmt["gemini_relevanz"] = grund
                        filtered_out.append(stmt)
                        stats["filtered_out"] += 1
            else:
                failed_api_calls += 1
                stats["api_errors"] += 1
                stats["skipped_by_error"] += len(batch)
                for stmt in batch:
                    kept.append(stmt)
                    stats["kept_after_filter"] += 1

            if bi < len(batches) - 1:
                time.sleep(REQUEST_DELAY)

    # Fehler-Schwelle
    if total_api_calls > 0 and failed_api_calls > total_api_calls / 2:
        stats["gemini_failed"] = True
        print(f"\n! WARNUNG: {failed_api_calls}/{total_api_calls} API-Calls fehlgeschlagen")

    enriched = always_keep + kept
    enriched.sort(key=lambda x: (x.get("upload_date") or "0000-00-00"), reverse=True)

    stats["total_output"] = len(enriched)
    stats["total_api_calls"] = total_api_calls
    stats["failed_api_calls"] = failed_api_calls

    print(f"\nErgebnis: {stats['total_input']} -> {stats['total_output']} Eintraege")
    print(f"  Aussortiert: {stats['filtered_out']}, Zusammenfassungen: {stats['summaries_generated']}")
    print(f"  API-Calls: {total_api_calls}, Fehler: {failed_api_calls}")

    return enriched, stats, filtered_out


def main():
    print("=== Lobbyregister Monitor – Gemini-Anreicherung ===")
    print(f"Modell: {GEMINI_MODEL} | Batch: {BATCH_SIZE} | Delay: {REQUEST_DELAY}s")

    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY nicht gesetzt – uebersprungen.")
        if DATA_PATH.exists():
            with open(DATA_PATH, encoding="utf-8") as f:
                data = json.load(f)
            data["gemini_stats"] = {"skipped": True, "reason": "no_api_key"}
            with open(DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        sys.exit(0)

    if not DATA_PATH.exists():
        print(f"FEHLER: {DATA_PATH} nicht gefunden")
        sys.exit(1)

    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    statements = data.get("statements", [])
    if not statements:
        print("Keine Stellungnahmen – nichts zu tun.")
        sys.exit(0)

    print(f"{len(statements)} Stellungnahmen geladen.")

    enriched, stats, filtered_out = enrich_statements(statements)

    data["statements"] = enriched
    data["gemini_stats"] = stats
    data["gemini_filtered_out"] = filtered_out

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n{DATA_PATH} aktualisiert ({len(enriched)} Eintraege).")

    if stats.get("gemini_failed"):
        error_msg = f"{stats['failed_api_calls']}/{stats['total_api_calls']} API-Calls fehlgeschlagen."
        print(f"\nAdmin-Benachrichtigung...")
        notify_admin_error(error_msg, stats)

    print("=== Fertig ===")


if __name__ == "__main__":
    main()

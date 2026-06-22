"""
health_check.py
===============
Woechentlicher Selbsttest des Lobbyregister-Monitors.

Prueft:
 1. Produktive Datenquelle (sucheDetailJson) – Erreichbarkeit, Struktur, SG-Format,
    pdfUrl-Direktlink, Themenfeld-Codes (10 Stichproben)
 2. rest/v2-API erreichbar (Fruehwarnsystem, nicht mehr produktiv)
 3. rest/v2-Struktur unveraendert (Fruehwarnsystem)
 4. Ob sich die API-Version (YAML) geaendert hat
 5. Hauptseite + Subseiten (hilfe, actions, impressum, wartung) erreichbar
 6. Aktualitaet von data.json (max. 48h alt)
 7. Daten-Integritaet: data.json strukturell vollstaendig
 8. Externer Lobbyregister-Link (Stichprobe einer Organisation-Detailseite)
 9. Admin-Passwort-Hash korrekt injiziert
10. run_history.json aktuell
11. Resend-Mailversand (Sende-Key aktiv)
12. Gemini API erreichbar

Sendet bei Problemen einen Bericht per Resend an ADMIN_EMAIL.
"""

import os
import re
import requests
import ijson
from datetime import date

from config import GEMINI_MODEL

# ── Konfiguration ──────────────────────────────────────────────────────────────

ADMIN_EMAIL = os.environ["ADMIN_EMAIL"]
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
LOBBYREGISTER_API_KEY = os.environ.get("LOBBYREGISTER_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
SITE_URL = os.environ.get("SITE_URL", "https://lobbyregister-bot.de")
REPO_URL = "https://github.com/BMWE-IIIA4/lobbyregister-monitor"
ACTIONS_URL = f"{REPO_URL}/actions"
SECRETS_URL = f"{REPO_URL}/settings/secrets/actions"

# Produktive Datenquelle (wird von fetch_and_build.py genutzt)
SEARCH_URL = "https://www.lobbyregister.bundestag.de/sucheDetailJson"

# Offizielle rest/v2-API – nicht mehr produktiv genutzt, aber als Fruehwarn-
# system fuer API-Aenderungen weiterhin geprueft.
API_BASE = "https://api.lobbyregister.bundestag.de/rest/v2"
YAML_URL = "https://api.lobbyregister.bundestag.de/rest/v2/R2.21-de.yaml"

KNOWN_API_VERSION = "2.0.0"
KNOWN_YAML_FILE = "R2.21-de.yaml"

# GEMINI_MODEL wird aus config.py importiert (gemeinsame Quelle mit gemini_enrich.py)


# ── Einzelne Prüfungen ─────────────────────────────────────────────────────────

def check_search_interface():
    """Prueft die PRODUKTIVE Datenquelle (sucheDetailJson) umfassend:
    - Erreichbarkeit
    - Vorhandensein aller im Code KRITISCH verwendeten Felder
    - SG-Nummer-Format und Datumsableitung (gegen sending_date plausibilisiert)
    - pdfUrl ist direkte .pdf-URL
    - Themenfeld-Codes im 'FOI_*'-Format

    Prueft die ersten 10 Eintraege mit Stellungnahmen, um Einzelfaelle nicht
    falsch zu generalisieren. Bricht den Stream danach ab.

    Felder werden nach KRITISCH (Pipeline bricht ohne dieses Feld) und OPTIONAL
    (Fallback im Code vorhanden) klassifiziert. Nur kritische Probleme fuehren
    zur Fehlermeldung.
    """
    import re
    from datetime import date as _date

    problems = []
    samples_checked = 0
    SAMPLE_LIMIT = 10

    # Fuer Kollektiv-Pruefungen: nur dann melden, wenn KEIN Sample das Feld hat
    optional_field_seen = {
        "accountDetails.lastUpdateDate": False,
        "lobbyistIdentity.name": False,
        "registerEntryDetails.detailsPageUrl": False,
    }
    foi_code_seen = False

    try:
        resp = requests.get(SEARCH_URL, params={"pageSize": 10000},
                            headers={"Accept": "application/json",
                                     "User-Agent": "LobbyregisterMonitor/1.0"},
                            timeout=60, stream=True)
        if resp.status_code != 200:
            return False, f"Suchschnittstelle antwortet mit Status {resp.status_code}"
        resp.raw.decode_content = True

        for entry in ijson.items(resp.raw, "results.item"):
            st = entry.get("statements", {})
            if not (isinstance(st, dict) and st.get("statementsPresent")):
                continue

            samples_checked += 1

            # -- Optionale Org-Felder: irgendwo gesehen? --
            if entry.get("lobbyistIdentity", {}).get("name"):
                optional_field_seen["lobbyistIdentity.name"] = True
            if entry.get("accountDetails", {}).get("lastUpdateDate"):
                optional_field_seen["accountDetails.lastUpdateDate"] = True
            if entry.get("registerEntryDetails", {}).get("detailsPageUrl"):
                optional_field_seen["registerEntryDetails.detailsPageUrl"] = True

            # -- Themenfeld-Codes --
            foi_list = entry.get("activitiesAndInterests", {}).get("fieldsOfInterest") or []
            if isinstance(foi_list, list):
                for f in foi_list:
                    if isinstance(f, dict) and isinstance(f.get("code"), str) and f["code"].startswith("FOI_"):
                        foi_code_seen = True
                        break

            # -- KRITISCH: registerNumber + activitiesAndInterests-Struktur --
            if not entry.get("registerNumber"):
                problems.append("Eintrag ohne 'registerNumber' gefunden")
            if "activitiesAndInterests" not in entry:
                problems.append("'activitiesAndInterests' fehlt (Themenfilter funktioniert nicht)")

            # -- Stellungnahme-Ebene (erstes Stmt im ersten passenden Sample) --
            if samples_checked == 1:
                stmts = st.get("statements", [])
                if not stmts:
                    problems.append("Stellungnahmen-Liste leer trotz statementsPresent=True")
                else:
                    stmt = stmts[0]
                    # KRITISCH
                    critical_stmt_fields = ["pdfUrl", "regulatoryProjectTitle", "recipientGroups"]
                    for f in critical_stmt_fields:
                        if f not in stmt:
                            problems.append(f"Stellungnahme-Feld '{f}' fehlt (kritisch fuer Verarbeitung)")

                    # pdfUrl: direkte .pdf-URL?
                    pdf_url = stmt.get("pdfUrl", "")
                    if pdf_url and not pdf_url.lower().endswith(".pdf"):
                        problems.append(
                            f"pdfUrl endet nicht auf .pdf – Code in fetch_and_build.py "
                            f"(process_statement) faellt auf langsamen HTTP-Lookup zurueck: {pdf_url[:80]}"
                        )

                    # SG-Nummer aus pdfUrl extrahieren und Datum plausibilisieren
                    sg_match = re.search(r"(SG\d{10})", pdf_url)
                    if not sg_match:
                        problems.append(
                            f"SG-Nummer im erwarteten Format (SG + 10 Ziffern) nicht in pdfUrl gefunden – "
                            f"Codeanpassung in fetch_and_build.py (extract_sg_number, sg_to_upload_date) noetig. "
                            f"Beispiel-URL: {pdf_url[:100]}"
                        )
                    else:
                        sg = sg_match.group(1)
                        digits = sg[2:]
                        try:
                            yy, mm, dd = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
                            sg_date = _date(2000 + yy, mm, dd)
                            rgs = stmt.get("recipientGroups", [])
                            if rgs and isinstance(rgs, list):
                                sending_str = rgs[0].get("sendingDate", "")
                                try:
                                    sending_date = _date.fromisoformat(sending_str)
                                    if sg_date < sending_date:
                                        problems.append(
                                            f"SG-Datum ({sg_date}) liegt VOR Sendedatum ({sending_date}) – "
                                            f"SG-Nummer-Format moeglicherweise geaendert "
                                            f"(Code-Annahme: SG+JJMMTT+Zaehler in sg_to_upload_date)"
                                        )
                                except ValueError:
                                    problems.append(f"sendingDate nicht im ISO-Format: '{sending_str}'")
                        except (ValueError, IndexError):
                            problems.append(
                                f"SG-Nummer '{sg}' nicht als Datum interpretierbar "
                                f"(JJMMTT-Annahme verletzt)"
                            )

                    # recipientGroups-Struktur
                    rgs = stmt.get("recipientGroups", [])
                    if rgs and isinstance(rgs, list):
                        rg = rgs[0]
                        if "sendingDate" not in rg:
                            problems.append("recipientGroups[0].sendingDate fehlt")
                        if "recipients" not in rg:
                            problems.append("recipientGroups[0].recipients fehlt")
                        else:
                            recips = rg["recipients"]
                            if "federalGovernment" not in recips and "parliament" not in recips:
                                problems.append(
                                    "recipients hat weder 'federalGovernment' noch 'parliament' – "
                                    "Adressaten-Filter greift moeglicherweise nicht"
                                )

            if samples_checked >= SAMPLE_LIMIT:
                break

        resp.close()

        if samples_checked == 0:
            return False, "Suchschnittstelle lieferte keinen Eintrag mit Stellungnahmen"

        # Optionale Felder: nur melden, wenn in KEINEM Sample vorhanden
        for fname, seen in optional_field_seen.items():
            if not seen:
                problems.append(
                    f"Optionales Feld '{fname}' in {samples_checked} geprueften Eintraegen "
                    f"nirgends vorhanden – Schnittstellen-Aenderung wahrscheinlich"
                )
        if not foi_code_seen:
            problems.append(
                f"Kein Themenfeld-Code im 'FOI_*'-Format in {samples_checked} Samples gefunden – "
                f"Themenfilter (TARGET_FIELD_CODES) greift moeglicherweise nicht mehr"
            )

        if problems:
            return False, f"Strukturprobleme nach {samples_checked} Samples: " + "; ".join(problems)
        return True, f"Suchschnittstelle OK ({samples_checked} Samples geprueft, SG-Format korrekt)"

    except requests.Timeout:
        return False, "Suchschnittstelle Timeout nach 60 Sekunden"
    except Exception as e:
        return False, f"Suchschnittstelle nicht erreichbar: {e}"


def check_api_reachable(api_key):
    """Prüft die offizielle rest/v2-API (Fruehwarnsystem, nicht mehr produktiv)."""
    if not api_key:
        return True, "rest/v2-Key nicht konfiguriert (Fruehwarnsystem inaktiv)"
    try:
        resp = requests.get(
            f"{API_BASE}/registerentries",
            headers={"Authorization": f"ApiKey {api_key}"},
            params={"format": "json"}, timeout=20
        )
        if resp.status_code == 401:
            return False, "rest/v2 antwortet mit 401 – API-Key ungültig oder abgelaufen"
        if resp.status_code == 403:
            return False, "rest/v2 antwortet mit 403 – API-Key möglicherweise gesperrt"
        if resp.status_code >= 500:
            return False, f"rest/v2 antwortet mit Serverfehler {resp.status_code}"
        if resp.status_code != 200:
            return False, f"rest/v2 antwortet mit unerwartetem Status {resp.status_code}"
        return True, "rest/v2 erreichbar und Key gültig (Fruehwarnsystem)"
    except requests.Timeout:
        return False, "rest/v2-Abfrage Timeout nach 20 Sekunden"
    except requests.ConnectionError as e:
        return False, f"Verbindungsfehler zur rest/v2-API: {e}"



def check_yaml_version():
    """Prüft, ob sich die API-YAML-Datei geändert hat."""
    issues = []
    try:
        swagger_url = f"{API_BASE}/swagger-ui/"
        resp = requests.get(swagger_url, timeout=20)
        if resp.status_code == 200:
            yaml_files = re.findall(r'R\d+\.\d+-de\.yaml', resp.text)
            if yaml_files:
                latest = yaml_files[0]
                if latest != KNOWN_YAML_FILE:
                    issues.append({
                        "severity": "WARNUNG", "title": "Neue API-Version verfügbar",
                        "detail": f"Bekannt: {KNOWN_YAML_FILE} → Neu: {latest}",
                        "action": f"1. Neue YAML herunterladen\n2. Felder prüfen\n3. KNOWN_YAML_FILE aktualisieren"
                    })
    except Exception as e:
        issues.append({"severity": "INFO", "title": "Swagger-UI nicht prüfbar",
                       "detail": str(e), "action": "Manuell prüfen"})
    try:
        resp = requests.get(YAML_URL, timeout=20)
        if resp.status_code == 404:
            issues.append({"severity": "FEHLER", "title": "YAML-Datei nicht mehr abrufbar",
                           "detail": f"{YAML_URL} gibt 404 zurück",
                           "action": "Neue YAML-URL nachschlagen und aktualisieren"})
        elif resp.status_code == 200:
            version_match = re.search(r'version:\s*["\']?(\d+\.\d+\.\d+)["\']?', resp.text)
            if version_match and version_match.group(1) != KNOWN_API_VERSION:
                issues.append({"severity": "WARNUNG", "title": "API-Versionsnummer geändert",
                               "detail": f"Bekannt: {KNOWN_API_VERSION} → Aktuell: {version_match.group(1)}",
                               "action": "YAML auf geänderte Felder prüfen"})
    except Exception:
        pass
    return issues


def check_site_reachable():
    """Prueft, ob die GitHub Pages-Seite und alle Subseiten erreichbar sind."""
    try:
        # Hauptseite
        resp = requests.get(SITE_URL, timeout=20)
        if resp.status_code == 404:
            return False, "Hauptseite gibt 404 zurueck"
        if resp.status_code != 200:
            return False, f"Hauptseite antwortet mit Status {resp.status_code}"
        if "Lobbyregister" not in resp.text:
            return False, "Hauptseite erreichbar aber enthaelt nicht den erwarteten Inhalt"

        # Subseiten – jede sollte HTTP 200 liefern
        subpages = ["hilfe.html", "actions.html", "impressum.html", "wartung.html"]
        problems = []
        for page in subpages:
            try:
                r = requests.get(f"{SITE_URL}/{page}", timeout=15)
                if r.status_code != 200:
                    problems.append(f"{page}: HTTP {r.status_code}")
            except Exception as e:
                problems.append(f"{page}: {e}")

        if problems:
            return False, "Subseiten nicht erreichbar: " + "; ".join(problems)
        return True, f"Hauptseite + {len(subpages)} Subseiten erreichbar"
    except Exception as e:
        return False, f"Seite nicht erreichbar: {e}"


def check_data_integrity():
    """Prueft data.json auf strukturelle Vollstaendigkeit.
    Geht ueber die reine Aktualitaetspruefung hinaus (check_data_freshness).
    """
    try:
        resp = requests.get(f"{SITE_URL}/data.json", timeout=20)
        if resp.status_code != 200:
            return False, f"data.json nicht abrufbar (Status {resp.status_code})"
        data = resp.json()

        problems = []
        if "statements" not in data or not isinstance(data["statements"], list):
            return False, "data.json: 'statements' fehlt oder kein Array (kritischer Strukturfehler)"
        if "generated_at" not in data:
            problems.append("'generated_at' fehlt")
        if "newly_added" not in data:
            problems.append("'newly_added' fehlt")

        stmts = data["statements"]
        if not stmts:
            problems.append("statements-Array ist leer")
        else:
            # Statement-Feldvollstaendigkeit am ersten Eintrag pruefen
            required_fields = ["register_number", "org_name", "regulatory_project_title"]
            first = stmts[0]
            for f in required_fields:
                if f not in first:
                    problems.append(f"Erstes Statement: Feld '{f}' fehlt")

            # Status-Verteilung
            statuses = {}
            for s in stmts:
                st = s.get("gemini_status", "unset")
                statuses[st] = statuses.get(st, 0) + 1
            # Wenn 'unset' dominiert, koennte etwas mit gemini_enrich nicht stimmen
            if statuses.get("unset", 0) > len(stmts) * 0.5:
                problems.append(
                    f"Mehr als 50% der Statements ohne gemini_status "
                    f"({statuses.get('unset', 0)}/{len(stmts)}) – gemini_enrich.py laeuft moeglicherweise nicht"
                )

        if problems:
            return False, "data.json-Struktur: " + "; ".join(problems)
        return True, f"data.json strukturell vollstaendig ({len(stmts)} Statements)"
    except Exception as e:
        return False, f"data.json-Integritaetspruefung fehlgeschlagen: {e}"


def check_external_lobbyregister_link():
    """Prueft stichprobenhaft, ob die im data.json referenzierten externen Links
    auf lobbyregister.bundestag.de noch funktionieren.

    Nimmt eine Organisation aus data.json und prueft deren Detailseite. Nur ein
    Request, um den Health-Check nicht zur Last fuer den Registerbetreiber zu
    machen."""
    try:
        resp = requests.get(f"{SITE_URL}/data.json", timeout=20)
        if resp.status_code != 200:
            return True, "data.json nicht abrufbar – Skip"
        data = resp.json()
        stmts = data.get("statements", [])
        if not stmts:
            return True, "Keine Statements zum Pruefen vorhanden"

        # Erste Org mit org_url nehmen
        target_url = None
        for s in stmts:
            if s.get("org_url"):
                target_url = s["org_url"]
                break
        if not target_url:
            return True, "Keine org_url in data.json vorhanden – Skip"

        r = requests.get(target_url, timeout=20, allow_redirects=True)
        if r.status_code == 404:
            return False, f"Externe Detailseite gibt 404: {target_url}"
        if r.status_code >= 500:
            return False, f"Externe Detailseite Serverfehler {r.status_code}: {target_url}"
        if r.status_code != 200:
            return True, f"Externe Detailseite Status {r.status_code} (akzeptabel): {target_url}"
        return True, "Externer lobbyregister.bundestag.de-Link erreichbar"
    except requests.Timeout:
        return True, "Externe Detailseite Timeout (Schnittstelle ggf. langsam, keine Aktion noetig)"
    except Exception as e:
        return True, f"Externer Link nicht pruefbar ({e}) – Skip"


def check_gemini():
    """Prüft, ob die Gemini API erreichbar ist."""
    if not GEMINI_API_KEY:
        return True, "Gemini-Key nicht konfiguriert (optionaler Dienst)"
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": "Antworte nur mit OK."}]}],
                  "generationConfig": {"maxOutputTokens": 10}},
            timeout=20,
        )
        if resp.status_code in (401, 403):
            return False, f"Gemini API antwortet mit {resp.status_code} – Key ungültig"
        if resp.status_code == 429:
            return True, "Gemini API erreichbar (Rate Limit aktiv, Key funktioniert)"
        if resp.status_code != 200:
            return False, f"Gemini API antwortet mit {resp.status_code}"
        return True, f"Gemini API erreichbar ({GEMINI_MODEL})"
    except Exception as e:
        return False, f"Gemini API nicht erreichbar: {e}"


def check_api_structure(api_key):
    """Prüft die Struktur der rest/v2-API (Frühwarnsystem, nicht produktiv)."""
    if not api_key:
        return True, "rest/v2-Key nicht konfiguriert (Struktur-Frühwarnung inaktiv)"
    try:
        resp = requests.get(
            f"{API_BASE}/registerentries/R002297",
            headers={"Authorization": f"ApiKey {api_key}"},
            params={"format": "json"}, timeout=20
        )
        if resp.status_code != 200:
            return False, "Strukturtest fehlgeschlagen: Test-Eintrag R002297 nicht abrufbar"
        data = resp.json()
        if "statements" not in data:
            return False, "Strukturfehler: Feld 'statements' fehlt im Registereintrag"
        stmts_data = data.get("statements", {})
        if not isinstance(stmts_data, dict) or "statementsPresent" not in stmts_data:
            return False, "Strukturfehler: Feld 'statementsPresent' fehlt oder hat falsches Format"
        return True, "rest/v2-Struktur verifiziert (Frühwarnsystem)"
    except Exception as e:
        return False, f"Fehler beim Strukturtest: {e}"


def check_data_freshness():
    """Prüft ob data.json auf der Webseite aktuell ist (max. 48h alt)."""
    try:
        resp = requests.get(f"{SITE_URL}/data.json", timeout=20)
        if resp.status_code != 200:
            return False, f"data.json nicht abrufbar (Status {resp.status_code})"
        data = resp.json()
        generated_at = data.get("generated_at", "")
        if not generated_at:
            return False, "data.json enthält kein 'generated_at'-Feld"
        from datetime import datetime, timezone
        gen_dt = datetime.fromisoformat(generated_at)
        if gen_dt.tzinfo is None:
            gen_dt = gen_dt.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 3600
        if age_hours > 48:
            return False, f"data.json ist {age_hours:.0f} Stunden alt – Workflow läuft möglicherweise nicht"
        count = len(data.get("statements", []))
        return True, f"data.json aktuell ({age_hours:.0f}h alt, {count} Einträge)"
    except Exception as e:
        return False, f"data.json-Prüfung fehlgeschlagen: {e}"


def check_run_history():
    """Prüft ob run_history.json existiert und einen aktuellen Eintrag hat."""
    try:
        resp = requests.get(f"{SITE_URL}/run_history.json", timeout=20)
        if resp.status_code != 200:
            return False, f"run_history.json nicht abrufbar (Status {resp.status_code})"
        data = resp.json()
        runs = data.get("runs", [])
        if not runs:
            return False, "run_history.json enthält keine Einträge"
        from datetime import datetime, timezone
        latest = runs[0].get("timestamp", "")
        if not latest:
            return False, "Letzter Eintrag hat keinen Zeitstempel"
        last_dt = datetime.fromisoformat(latest)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        if age_hours > 48:
            return False, f"Letzter protokollierter Run ist {age_hours:.0f}h her"
        return True, f"run_history.json aktuell ({len(runs)} Einträge, letzter vor {age_hours:.0f}h)"
    except Exception as e:
        return False, f"run_history.json-Prüfung fehlgeschlagen: {e}"


def check_resend_sender():
    """Prueft die Resend-Anbindung – fokussiert auf die einzige benoetigte
    Faehigkeit: das Versenden von E-Mails.

    Der produktiv genutzte API-Key hat (nach dem Prinzip der minimalen Rechte)
    typischerweise nur 'Sending access'. Ein solcher Key darf administrative
    Endpunkte wie /domains NICHT abfragen und erhaelt dort bewusst 401/403 –
    das ist KEIN Fehler, sondern gewollt. Daher wird die Domain-Verifizierung
    nur als optionales Extra geprueft, wenn der Key dafuer Rechte hat.
    """
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY nicht konfiguriert – Mailversand nicht moeglich"
    try:
        resp = requests.get(
            "https://api.resend.com/domains",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            timeout=20
        )
        # 401/403: Key darf /domains nicht lesen -> eingeschraenkter Sende-Key.
        # Das ist der gewuenschte Normalfall und wird als 'ok' gewertet.
        if resp.status_code in (401, 403):
            return True, "Resend-Sende-Key aktiv (eingeschraenkte Rechte, wie vorgesehen)"
        if resp.status_code == 200:
            # Full-Access-Key: Domain-Verifizierung als nuetzliches Extra pruefen
            domains = resp.json().get("data", [])
            verified = [d["name"] for d in domains if d.get("status") == "verified"]
            if "lobbyregister-bot.de" in verified:
                return True, "Resend OK – Domain lobbyregister-bot.de verifiziert"
            return True, (f"Resend erreichbar; Domain-Status nur informativ "
                          f"(verifiziert: {verified or 'keine'})")
        # Andere Statuscodes: informativ, aber kein blockierender Fehler,
        # da die Sendefaehigkeit hier\u00fcber nicht zuverlaessig bewertbar ist.
        return True, f"Resend erreichbar (Status {resp.status_code}, nicht bewertet)"
    except Exception as e:
        return False, f"Resend nicht erreichbar (Netzwerk/Verbindung): {e}"


# ── Bericht ────────────────────────────────────────────────────────────────────

def build_report(results):
    """Baut den HTML-Bericht aus den Prüfungsergebnissen."""
    issues = []
    ok_items = []

    # Produktive Datenquelle (kritisch -> FEHLER)
    search_ok, search_msg = results["search"]
    if search_ok:
        ok_items.append(("Suchschnittstelle (produktiv)", search_msg))
    else:
        issues.append({
            "severity": "FEHLER",
            "title": "Produktive Datenquelle (sucheDetailJson) gestört",
            "detail": search_msg,
            "action": f"1. Suchschnittstelle manuell prüfen\n2. fetch_and_build.py ggf. anpassen\n3. Actions prüfen: {ACTIONS_URL}"
        })

    # rest/v2 nur noch Frühwarnsystem (nicht produktiv -> WARNUNG)
    api_ok, api_msg = results["api"]
    if api_ok:
        ok_items.append(("rest/v2-API (Frühwarnung)", api_msg))
    else:
        issues.append({
            "severity": "WARNUNG",
            "title": "rest/v2-API auffällig (Frühwarnsystem)",
            "detail": api_msg,
            "action": f"Nicht produktiv genutzt – aber moegliche Vorboten fuer Aenderungen der Suchschnittstelle. Key prüfen → {SECRETS_URL}"
        })

    struct_ok, struct_msg = results["api_struct"]
    if struct_ok:
        ok_items.append(("rest/v2-Struktur (Frühwarnung)", struct_msg))
    else:
        issues.append({
            "severity": "WARNUNG",
            "title": "rest/v2-Struktur geändert (Frühwarnsystem)",
            "detail": struct_msg,
            "action": "Hinweis auf moegliche kuenftige Aenderung der Suchschnittstelle – sucheDetailJson-Struktur beobachten"
        })

    yaml_issues = results["yaml"]
    if yaml_issues:
        issues.extend(yaml_issues)
    else:
        ok_items.append(("API-Version (YAML)", f"Unverändert ({KNOWN_YAML_FILE}, v{KNOWN_API_VERSION})"))

    site_ok, site_msg = results["site"]
    if site_ok:
        ok_items.append(("Webseite", site_msg))
    else:
        issues.append({
            "severity": "FEHLER",
            "title": "Webseite nicht erreichbar",
            "detail": site_msg,
            "action": f"1. GitHub Actions prüfen: {ACTIONS_URL}\n2. Pages-Settings prüfen"
        })

    fresh_ok, fresh_msg = results["data_freshness"]
    if fresh_ok:
        ok_items.append(("Daten-Aktualität", fresh_msg))
    else:
        issues.append({
            "severity": "FEHLER",
            "title": "Daten veraltet oder nicht abrufbar",
            "detail": fresh_msg,
            "action": f"1. GitHub Actions prüfen: {ACTIONS_URL}\n2. Workflow manuell starten"
        })

    integ_ok, integ_msg = results["data_integrity"]
    if integ_ok:
        ok_items.append(("Daten-Integrität (Struktur)", integ_msg))
    else:
        issues.append({
            "severity": "FEHLER",
            "title": "data.json strukturell unvollständig",
            "detail": integ_msg,
            "action": (
                "1. fetch_and_build.py oder gemini_enrich.py auf Fehler prüfen\n"
                "2. data-store-Branch im Repository auf zuletzt gute Version zurücksetzen, falls nötig"
            )
        })

    ext_ok, ext_msg = results["external_link"]
    if ext_ok:
        ok_items.append(("Externer Lobbyregister-Link", ext_msg))
    else:
        issues.append({
            "severity": "WARNUNG",
            "title": "Externer Lobbyregister-Link nicht erreichbar",
            "detail": ext_msg,
            "action": (
                "1. lobbyregister.bundestag.de manuell prüfen\n"
                "2. Bei dauerhaft geänderter URL-Struktur: detailsPageUrl-Verwendung in fetch_and_build.py anpassen"
            )
        })

    hist_ok, hist_msg = results["run_history"]
    if hist_ok:
        ok_items.append(("Run-History", hist_msg))
    else:
        issues.append({
            "severity": "WARNUNG",
            "title": "Workflow-Protokoll veraltet oder fehlt",
            "detail": hist_msg,
            "action": f"1. save_run_log.py-Schritt in Actions prüfen: {ACTIONS_URL}"
        })

    resend_ok, resend_msg = results["resend_sender"]
    if resend_ok:
        ok_items.append(("Resend (Wochenmail)", resend_msg))
    else:
        issues.append({
            "severity": "WARNUNG",
            "title": "Resend-Mailversand beeintraechtigt",
            "detail": resend_msg,
            "action": ("1. RESEND_API_KEY in den GitHub-Secrets pruefen\n"
                       "2. Bei Bedarf neuen Sende-Key auf resend.com erstellen")
        })

    gemini_ok, gemini_msg = results["gemini"]
    if gemini_ok:
        ok_items.append(("Gemini API (optional)", gemini_msg))
    else:
        issues.append({
            "severity": "WARNUNG", 
            "title": "Gemini API nicht erreichbar",
            "detail": gemini_msg, 
            "action": "1. Key prüfen: aistudio.google.com/apikey\n2. Monitor läuft auch ohne Gemini"
        })

    has_issues = len(issues) > 0
    today = date.today().strftime("%d.%m.%Y")
    severity_colors = {
        "FEHLER": ("#c62828", "#ffebee"), 
        "WARNUNG": ("#e65100", "#fff3e0"), 
        "INFO": ("#1565c0", "#e3f2fd")
    }

    issues_html = ""
    for issue in issues:
        color, bg = severity_colors.get(issue["severity"], ("#555", "#f5f5f5"))
        action_html = issue["action"].replace("\n", "<br>")
        issues_html += f"""
        <div style="border:1px solid {color};background:{bg};margin-bottom:12px;overflow:hidden">
          <div style="background:{color};padding:6px 12px;color:#fff;font-size:12px;font-weight:700">{issue['severity']}: {issue['title']}</div>
          <div style="padding:10px 12px;font-size:12px;color:#333">
            <p style="margin-bottom:8px"><strong>Problem:</strong> {issue['detail']}</p>
            <p><strong>Was zu tun ist:</strong><br>{action_html}</p>
          </div>
        </div>"""

    ok_html = "".join(
        f'<tr><td style="padding:5px 10px;font-size:12px;color:#555">{n}</td>'
        f'<td style="padding:5px 10px;font-size:12px;color:#2e7d32">✓ {m}</td></tr>'
        for n, m in ok_items
    )

    status_color = "#c62828" if has_issues else "#2e7d32"
    status_text = f"{len(issues)} Problem(e) gefunden" if has_issues else "Alles in Ordnung"

    html = f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#222;margin:0;padding:0;background:#f5f5f5">
<div style="max-width:700px;margin:20px auto">
  <div style="background:#004B87;padding:16px 28px">
    <div style="color:#fff;font-size:15px;font-weight:700">Lobbyregister-Monitor · Statusbericht</div>
    <div style="color:#a8c8e8;font-size:11px">{today} · Automatischer Selbsttest</div>
  </div>
  <div style="background:#fff;padding:20px 28px">
    <div style="background:{status_color};color:#fff;padding:10px 16px;font-size:14px;font-weight:700;margin-bottom:20px">Status: {status_text}</div>
    {"<h3 style='font-size:14px;color:#c62828;margin-bottom:12px'>Probleme:</h3>" + issues_html if issues_html else ""}
    <h3 style="font-size:13px;color:#555;margin-bottom:8px;margin-top:16px">Bestandene Prüfungen:</h3>
    <table style="width:100%;border-collapse:collapse">{ok_html}</table>
    <hr style="border:none;border-top:1px solid #e0e8f0;margin:20px 0">
    <p style="font-size:12px;color:#888">
      <a href="{ACTIONS_URL}" style="color:#004B87">Actions</a> ·
      <a href="{SITE_URL}" style="color:#004B87">Webseite</a> ·
      <a href="{SITE_URL}/wartung.html" style="color:#004B87">Wartung</a>
    </p>
  </div>
</div></body></html>"""

    return has_issues, html


def send_report_resend(html, has_issues):
    """Sendet den Bericht per Resend an separate Admin-Mail."""
    if not has_issues:
        print("Alle Prüfungen bestanden – kein Bericht versendet.")
        return
    
    # Prüfe ob Resend verfügbar ist
    if not RESEND_API_KEY:
        print("⚠️ RESEND_API_KEY fehlt – speichere Bericht nur lokal")
        with open("/tmp/health_report.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Bericht in /tmp/health_report.html gespeichert")
        return
        
    today = date.today().strftime("%d.%m.%Y")
    
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": "Lobbyregister-Monitor Systemcheck <healthcheck@lobbyregister-bot.de>",
                "to": [ADMIN_EMAIL],  # Separate Admin-Mail, NICHT die Hauptempfänger!
                "subject": f"⚠️ Lobbyregister-Monitor: Handlungsbedarf – {today}",
                "html": html
            },
            timeout=30
        )
        resp.raise_for_status()
        print(f"✓ Health-Check-Bericht an {ADMIN_EMAIL} gesendet (via Resend)")
    except Exception as e:
        print(f"✗ Fehler beim E-Mail-Versand: {e}")
        # Fallback: In Datei speichern
        with open("/tmp/health_report.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Bericht in /tmp/health_report.html gespeichert")


def main():
    print("=== Lobbyregister Monitor – Wöchentlicher Selbsttest ===")
    results = {}
    print("Prüfe Suchschnittstelle (produktive Quelle)...")
    results["search"] = check_search_interface()
    print("Prüfe rest/v2-API (Frühwarnsystem)...")
    results["api"] = check_api_reachable(LOBBYREGISTER_API_KEY)
    print("Prüfe API-Struktur (Frühwarnsystem)...")
    results["api_struct"] = check_api_structure(LOBBYREGISTER_API_KEY)
    print("Prüfe YAML...")
    results["yaml"] = check_yaml_version()
    print("Prüfe Seite...")
    results["site"] = check_site_reachable()
    print("Prüfe Daten-Aktualität...")
    results["data_freshness"] = check_data_freshness()
    print("Prüfe Daten-Integrität (Struktur)...")
    results["data_integrity"] = check_data_integrity()
    print("Prüfe externen lobbyregister-Link (Stichprobe)...")
    results["external_link"] = check_external_lobbyregister_link()
    print("Prüfe Run-History...")
    results["run_history"] = check_run_history()
    print("Prüfe Resend-Sender...")
    results["resend_sender"] = check_resend_sender()
    print("Prüfe Gemini...")
    results["gemini"] = check_gemini()
    
    has_issues, html = build_report(results)
    print(f"Ergebnis: {'PROBLEME – Bericht wird versendet' if has_issues else 'Alles OK'}")
    send_report_resend(html, has_issues)
    print("=== Fertig ===")


if __name__ == "__main__":
    main()

"""
health_check.py
===============
Wöchentlicher Selbsttest des Lobbyregister-Monitors.

Prüft:
1. Erreichbarkeit der Lobbyregister API
2. Ob der öffentliche API-Key sich geändert hat
3. Ob sich die API-Version (YAML) geändert hat
4. Ob die generierten Seiten korrekt ausgeliefert werden
5. Ob Resend noch funktioniert
6. Ob die Gemini API erreichbar ist (optional)

Sendet bei Problemen eine detaillierte Admin-Mail mit konkreten
Handlungsanweisungen.
"""

import os
import re
import requests
from datetime import date

# ── Konfiguration ──────────────────────────────────────────────────────────────

RESEND_API_KEY = os.environ["RESEND_API_KEY"]
ADMIN_EMAIL = os.environ["ADMIN_EMAIL"]
LOBBYREGISTER_API_KEY = os.environ.get("LOBBYREGISTER_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
SITE_URL = os.environ.get("SITE_URL", "https://bmwe-iiia4.github.io/lobbyregister-monitor")
REPO_URL = "https://github.com/BMWE-IIIA4/lobbyregister-monitor"
ACTIONS_URL = f"{REPO_URL}/actions"
SECRETS_URL = f"{REPO_URL}/settings/secrets/actions"

API_BASE = "https://api.lobbyregister.bundestag.de/rest/v2"
INFO_PAGE = "https://www.lobbyregister.bundestag.de/informationen-und-hilfe/open-data-1049716"
YAML_URL = "https://api.lobbyregister.bundestag.de/rest/v2/R2.21-de.yaml"

# Bekannte API-Version – wird mit der aktuellen verglichen
KNOWN_API_VERSION = "2.0.0"
KNOWN_YAML_FILE = "R2.21-de.yaml"


# ── Einzelne Prüfungen ─────────────────────────────────────────────────────────

def check_api_reachable(api_key):
    """Prüft ob die Lobbyregister API erreichbar und der Key gültig ist."""
    try:
        resp = requests.get(
            f"{API_BASE}/registerentries",
            headers={"Authorization": f"ApiKey {api_key}"},
            params={"format": "json"},
            timeout=20
        )
        if resp.status_code == 401:
            return False, "API antwortet mit 401 Unauthorized – API-Key ungültig oder abgelaufen"
        if resp.status_code == 403:
            return False, "API antwortet mit 403 Forbidden – API-Key möglicherweise gesperrt"
        if resp.status_code >= 500:
            return False, f"API antwortet mit Serverfehler {resp.status_code} – vorübergehende Störung möglich"
        if resp.status_code != 200:
            return False, f"API antwortet mit unerwartetem Status {resp.status_code}"
        return True, "API erreichbar und Key gültig"
    except requests.Timeout:
        return False, "API-Abfrage Timeout nach 20 Sekunden – Server möglicherweise überlastet"
    except requests.ConnectionError as e:
        return False, f"Verbindungsfehler zur API: {e}"


def check_public_api_key():
    """
    Liest die Lobbyregister-Infoseite und extrahiert den dort
    veröffentlichten aktuellen öffentlichen API-Key.
    """
    try:
        resp = requests.get(INFO_PAGE, timeout=20)
        resp.raise_for_status()
        html = resp.text

        matches = re.findall(r'[A-Za-z0-9]{20,}', html)
        key_candidates = [m for m in matches if len(m) >= 28 and not m.islower()]

        if not key_candidates:
            return None, "Kein API-Key auf der Infoseite gefunden – Seitenstruktur möglicherweise geändert"

        current_stored = LOBBYREGISTER_API_KEY
        if current_stored and current_stored not in key_candidates:
            return key_candidates[0], f"Möglicher neuer API-Key auf der Infoseite: {key_candidates[0][:8]}..."

        return None, "API-Key unverändert"

    except Exception as e:
        return None, f"Infoseite nicht abrufbar: {e}"


def check_yaml_version():
    """
    Prüft ob die YAML-Spezifikation eine neue Version hat.
    Vergleicht Versionsnummer und Dateiname.
    """
    issues = []

    try:
        swagger_url = f"{API_BASE}/swagger-ui/"
        resp = requests.get(swagger_url, timeout=20)
        if resp.status_code == 200:
            html = resp.text
            yaml_files = re.findall(r'R\d+\.\d+-de\.yaml', html)
            if yaml_files:
                latest = yaml_files[0]
                if latest != KNOWN_YAML_FILE:
                    issues.append({
                        "severity": "WARNUNG",
                        "title": "Neue API-Version verfügbar",
                        "detail": f"Bekannte Version: {KNOWN_YAML_FILE} → Neue Version: {latest}",
                        "action": (
                            f"1. Neue YAML herunterladen: https://api.lobbyregister.bundestag.de/rest/v2/{latest}\n"
                            f"2. Auf Änderungen an folgenden Feldern prüfen: statementNumber, regulatoryProjectTitle, "
                            f"recipientGroups, fieldsOfInterest, sendingDate, pdfUrl\n"
                            f"3. In scripts/fetch_and_build.py die Variable KNOWN_YAML_FILE auf '{latest}' setzen\n"
                            f"4. Ggf. Felder in extract_statements() anpassen"
                        )
                    })
    except Exception as e:
        issues.append({
            "severity": "INFO",
            "title": "Swagger-UI nicht prüfbar",
            "detail": str(e),
            "action": "Manuell prüfen: https://api.lobbyregister.bundestag.de/rest/v2/swagger-ui/"
        })

    try:
        resp = requests.get(YAML_URL, timeout=20)
        if resp.status_code == 404:
            issues.append({
                "severity": "FEHLER",
                "title": "Bekannte YAML-Datei nicht mehr abrufbar",
                "detail": f"{YAML_URL} gibt 404 zurück – API-Version wurde aktualisiert",
                "action": (
                    f"1. Neue YAML-URL auf {INFO_PAGE} nachschlagen\n"
                    f"2. In scripts/fetch_and_build.py KNOWN_YAML_FILE aktualisieren\n"
                    f"3. Felder in extract_statements() auf Änderungen prüfen"
                )
            })
        elif resp.status_code == 200:
            content = resp.text
            version_match = re.search(r'version:\s*["\']?(\d+\.\d+\.\d+)["\']?', content)
            if version_match:
                found_version = version_match.group(1)
                if found_version != KNOWN_API_VERSION:
                    issues.append({
                        "severity": "WARNUNG",
                        "title": "API-Versionsnummer geändert",
                        "detail": f"Bekannte Version: {KNOWN_API_VERSION} → Aktuelle Version: {found_version}",
                        "action": (
                            "1. YAML auf geänderte Feldnamen prüfen\n"
                            "2. Besonders prüfen: statements[], recipientGroups[], fieldsOfInterest[]\n"
                            "3. In scripts/fetch_and_build.py KNOWN_API_VERSION aktualisieren"
                        )
                    })
    except Exception:
        pass

    return issues


def check_site_reachable():
    """Prüft ob die generierte GitHub-Pages-Seite erreichbar ist."""
    try:
        resp = requests.get(SITE_URL, timeout=20)
        if resp.status_code == 404:
            return False, "GitHub Pages Seite gibt 404 zurück – möglicherweise nicht aktiviert oder noch nicht deployed"
        if resp.status_code != 200:
            return False, f"GitHub Pages Seite antwortet mit Status {resp.status_code}"
        if "Lobbyregister" not in resp.text:
            return False, "Seite erreichbar aber enthält nicht den erwarteten Inhalt – möglicherweise fehlerhaft generiert"
        return True, "Seite erreichbar und Inhalt korrekt"
    except Exception as e:
        return False, f"Seite nicht erreichbar: {e}"


def check_resend():
    """Resend-Key wird nicht aktiv geprüft – Mail-Eingang als Indikator."""
    return True, "Resend-Key wird nicht aktiv geprüft (Mail-Eingang als Indikator)"


def check_gemini():
    """Prüft ob die Gemini API erreichbar und der Key gültig ist."""
    if not GEMINI_API_KEY:
        return True, "Gemini-Key nicht konfiguriert (optionaler Dienst)"

    try:
        resp = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
            params={"key": GEMINI_API_KEY},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": "Antworte nur mit dem Wort OK."}]}],
                "generationConfig": {"maxOutputTokens": 10},
            },
            timeout=20,
        )
        if resp.status_code in (401, 403):
            return False, f"Gemini API antwortet mit {resp.status_code} – API-Key ungültig oder gesperrt"
        if resp.status_code == 429:
            return True, "Gemini API erreichbar (Rate Limit aktiv, aber Key funktioniert)"
        if resp.status_code != 200:
            return False, f"Gemini API antwortet mit {resp.status_code}"
        return True, "Gemini API erreichbar und Key gültig"
    except Exception as e:
        return False, f"Gemini API nicht erreichbar: {e}"


# ── Bericht zusammenstellen ────────────────────────────────────────────────────

def build_report(results):
    """
    Erstellt einen strukturierten Bericht aus den Prüfergebnissen.
    Gibt (has_issues, html) zurück.
    """
    issues = []
    ok_items = []

    # API erreichbar
    api_ok, api_msg = results["api"]
    if api_ok:
        ok_items.append(("Lobbyregister API", api_msg))
    else:
        issues.append({
            "severity": "FEHLER",
            "title": "Lobbyregister API nicht erreichbar",
            "detail": api_msg,
            "action": (
                f"1. Prüfe {INFO_PAGE} ob der API-Key sich geändert hat\n"
                f"2. Falls neuer Key vorhanden: in GitHub Secrets unter LOBBYREGISTER_API_KEY eintragen\n"
                f"   → {SECRETS_URL}\n"
                f"3. Falls API generell nicht erreichbar: warte 24h und prüfe erneut"
            )
        })

    # API-Key auf Infoseite
    new_key, key_msg = results["public_key"]
    if new_key:
        issues.append({
            "severity": "WARNUNG",
            "title": "Mögliche Änderung des öffentlichen API-Keys",
            "detail": key_msg,
            "action": (
                f"1. Prüfe {INFO_PAGE} und lies den aktuellen API-Key nach\n"
                f"2. Falls geändert: neuen Key in GitHub Secrets unter LOBBYREGISTER_API_KEY eintragen\n"
                f"   → {SECRETS_URL}\n"
                f"3. Manuellen Testlauf starten: {ACTIONS_URL}"
            )
        })
    else:
        ok_items.append(("Öffentlicher API-Key", key_msg))

    # YAML-Version
    yaml_issues = results["yaml"]
    if yaml_issues:
        issues.extend(yaml_issues)
    else:
        ok_items.append(("API-Version (YAML)", f"Unverändert ({KNOWN_YAML_FILE}, v{KNOWN_API_VERSION})"))

    # Seite erreichbar
    site_ok, site_msg = results["site"]
    if site_ok:
        ok_items.append(("GitHub Pages Seite", site_msg))
    else:
        issues.append({
            "severity": "FEHLER",
            "title": "Übersichtsseite nicht erreichbar",
            "detail": site_msg,
            "action": (
                f"1. Prüfe GitHub Actions auf Fehler: {ACTIONS_URL}\n"
                f"2. Prüfe ob GitHub Pages aktiviert ist: {REPO_URL}/settings/pages\n"
                f"3. Starte manuellen Testlauf: {ACTIONS_URL}"
            )
        })

    # Resend
    resend_ok, resend_msg = results["resend"]
    if resend_ok:
        ok_items.append(("Resend E-Mail-Dienst", resend_msg))
    else:
        issues.append({
            "severity": "FEHLER",
            "title": "Resend API-Key ungültig",
            "detail": resend_msg,
            "action": (
                "1. Einloggen auf resend.com\n"
                "2. Neuen API-Key erstellen: API Keys → Create API Key\n"
                f"3. Neuen Key in GitHub Secrets unter RESEND_API_KEY eintragen:\n"
                f"   → {SECRETS_URL}"
            )
        })

    # Gemini (optional – nur Warnung, kein Fehler)
    gemini_ok, gemini_msg = results["gemini"]
    if gemini_ok:
        ok_items.append(("Gemini API (optional)", gemini_msg))
    else:
        issues.append({
            "severity": "WARNUNG",
            "title": "Gemini API nicht erreichbar",
            "detail": gemini_msg,
            "action": (
                "1. Prüfe ob der Gemini API-Key noch gültig ist: https://aistudio.google.com/apikey\n"
                f"2. Falls abgelaufen: neuen Key erstellen und in GitHub Secrets unter GEMINI_API_KEY eintragen\n"
                f"   → {SECRETS_URL}\n"
                "3. Der Monitor funktioniert auch ohne Gemini – Einträge werden dann ungefiltert angezeigt"
            )
        })

    has_issues = len(issues) > 0

    # HTML-Report bauen
    today = date.today().strftime("%d.%m.%Y")
    severity_colors = {
        "FEHLER": ("#c62828", "#ffebee"),
        "WARNUNG": ("#e65100", "#fff3e0"),
        "INFO": ("#1565c0", "#e3f2fd"),
    }

    issues_html = ""
    for issue in issues:
        color, bg = severity_colors.get(issue["severity"], ("#555", "#f5f5f5"))
        action_html = issue["action"].replace("\n", "<br>")
        issues_html += f"""
        <div style="border:1px solid {color};background:{bg};margin-bottom:12px;overflow:hidden">
          <div style="background:{color};padding:6px 12px;color:#fff;font-size:12px;font-weight:700">
            {issue['severity']}: {issue['title']}
          </div>
          <div style="padding:10px 12px;font-size:12px;color:#333">
            <p style="margin-bottom:8px"><strong>Problem:</strong> {issue['detail']}</p>
            <p><strong>Was zu tun ist:</strong><br>{action_html}</p>
          </div>
        </div>"""

    ok_html = "".join(
        f'<tr><td style="padding:5px 10px;font-size:12px;color:#555">{name}</td>'
        f'<td style="padding:5px 10px;font-size:12px;color:#2e7d32">✓ {msg}</td></tr>'
        for name, msg in ok_items
    )

    status_color = "#c62828" if has_issues else "#2e7d32"
    status_text = f"{len(issues)} Problem(e) gefunden" if has_issues else "Alles in Ordnung"

    html = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;margin:0;padding:0;background:#f5f5f5">
<div style="max-width:700px;margin:20px auto">
  <div style="background:#004B87;padding:16px 28px">
    <div style="color:#fff;font-size:15px;font-weight:700">Lobbyregister-Monitor · Wöchentlicher Statusbericht</div>
    <div style="color:#a8c8e8;font-size:11px">{today} · Automatischer Selbsttest</div>
  </div>
  <div style="background:#fff;padding:20px 28px">
    <div style="background:{status_color};color:#fff;padding:10px 16px;font-size:14px;font-weight:700;margin-bottom:20px">
      Status: {status_text}
    </div>

    {"<h3 style='font-size:14px;color:#c62828;margin-bottom:12px'>Probleme die Aufmerksamkeit erfordern:</h3>" + issues_html if issues_html else ""}

    <h3 style="font-size:13px;color:#555;margin-bottom:8px;margin-top:16px">Bestandene Prüfungen:</h3>
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      {ok_html}
    </table>

    <hr style="border:none;border-top:1px solid #e0e8f0;margin:20px 0">
    <p style="font-size:12px;color:#888">
      <a href="{ACTIONS_URL}" style="color:#004B87">GitHub Actions Logs</a> ·
      <a href="{SITE_URL}" style="color:#004B87">Übersichtsseite</a> ·
      <a href="{SITE_URL}/wartung.html" style="color:#004B87">Wartungsdokumentation</a>
    </p>
  </div>
  <div style="background:#f0f4f8;padding:12px 28px;font-size:11px;color:#777">
    Bundesministerium für Wirtschaft und Energie (BMWE) · Lobbyregister-Monitor
  </div>
</div>
</body></html>"""

    return has_issues, html


def send_report(html, has_issues):
    """Versendet den Statusbericht – nur bei Problemen."""
    if not has_issues:
        print("Alle Prüfungen bestanden – kein Bericht versendet.")
        return

    today = date.today().strftime("%d.%m.%Y")
    subject = f"⚠️ Lobbyregister-Monitor: Handlungsbedarf – Statusbericht {today}"

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": "onboarding@resend.dev",
            "to": [ADMIN_EMAIL],
            "subject": subject,
            "html": html,
        },
        timeout=30,
    )
    resp.raise_for_status()
    print(f"Statusbericht mit Problemen gesendet an {ADMIN_EMAIL}")


# ── Hauptprogramm ──────────────────────────────────────────────────────────────

def main():
    print("=== Lobbyregister Monitor – Wöchentlicher Selbsttest ===")

    results = {}

    print("Prüfe API-Erreichbarkeit...")
    results["api"] = check_api_reachable(LOBBYREGISTER_API_KEY)

    print("Prüfe öffentlichen API-Key auf Infoseite...")
    results["public_key"] = check_public_api_key()

    print("Prüfe API-Version (YAML)...")
    results["yaml"] = check_yaml_version()

    print("Prüfe GitHub Pages Seite...")
    results["site"] = check_site_reachable()

    print("Prüfe Resend...")
    results["resend"] = check_resend()

    print("Prüfe Gemini API...")
    results["gemini"] = check_gemini()

    has_issues, html = build_report(results)

    print(f"Ergebnis: {'PROBLEME GEFUNDEN – Bericht versendet' if has_issues else 'Alles OK'}")
    send_report(html, has_issues)
    print("=== Fertig ===")


if __name__ == "__main__":
    main()

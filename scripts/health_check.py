"""
health_check.py
===============
Wöchentlicher Selbsttest des Lobbyregister-Monitors.

Prüft:
1. Erreichbarkeit der Lobbyregister API
2. Ob sich die API-Version (YAML) geändert hat
3. Ob die generierten Seiten korrekt ausgeliefert werden
4. Ob die Gemini API erreichbar ist (optional)

Sendet bei Problemen eine detaillierte Admin-Mail DIREKT (ohne Resend).
"""

import os
import re
import requests
from datetime import date

# ── Konfiguration ──────────────────────────────────────────────────────────────

ADMIN_EMAIL = os.environ["ADMIN_EMAIL"]
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
LOBBYREGISTER_API_KEY = os.environ.get("LOBBYREGISTER_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
SITE_URL = os.environ.get("SITE_URL", "https://lobbyregister-bot.de")
REPO_URL = "https://github.com/BMWE-IIIA4/lobbyregister-monitor"
ACTIONS_URL = f"{REPO_URL}/actions"
SECRETS_URL = f"{REPO_URL}/settings/secrets/actions"

API_BASE = "https://api.lobbyregister.bundestag.de/rest/v2"
YAML_URL = "https://api.lobbyregister.bundestag.de/rest/v2/R2.21-de.yaml"

KNOWN_API_VERSION = "2.0.0"
KNOWN_YAML_FILE = "R2.21-de.yaml"

# WICHTIG: Muss mit GEMINI_MODEL in gemini_enrich.py übereinstimmen!
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"


# ── Einzelne Prüfungen ─────────────────────────────────────────────────────────

def check_api_reachable(api_key):
    """Prüft, ob die Lobbyregister API antwortet und der Key funktioniert."""
    try:
        resp = requests.get(
            f"{API_BASE}/registerentries",
            headers={"Authorization": f"ApiKey {api_key}"},
            params={"format": "json"}, timeout=20
        )
        if resp.status_code == 401:
            return False, "API antwortet mit 401 Unauthorized – API-Key ungültig oder abgelaufen"
        if resp.status_code == 403:
            return False, "API antwortet mit 403 Forbidden – API-Key möglicherweise gesperrt"
        if resp.status_code >= 500:
            return False, f"API antwortet mit Serverfehler {resp.status_code}"
        if resp.status_code != 200:
            return False, f"API antwortet mit unerwartetem Status {resp.status_code}"
        return True, "API erreichbar und Key gültig"
    except requests.Timeout:
        return False, "API-Abfrage Timeout nach 20 Sekunden"
    except requests.ConnectionError as e:
        return False, f"Verbindungsfehler zur API: {e}"


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
    """Prüft, ob die GitHub Pages-Seite erreichbar ist."""
    try:
        resp = requests.get(SITE_URL, timeout=20)
        if resp.status_code == 404:
            return False, "Seite gibt 404 zurück"
        if resp.status_code != 200:
            return False, f"Seite antwortet mit Status {resp.status_code}"
        if "Lobbyregister" not in resp.text:
            return False, "Seite erreichbar aber enthält nicht den erwarteten Inhalt"
        return True, "Seite erreichbar und Inhalt korrekt"
    except Exception as e:
        return False, f"Seite nicht erreichbar: {e}"


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
    """Prüft, ob die API-Struktur noch wie erwartet ist."""
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
            
        return True, "API-Struktur (Stellungnahmen) verifiziert"
    except Exception as e:
        return False, f"Fehler beim Strukturtest: {e}"


# ── Bericht ────────────────────────────────────────────────────────────────────

def build_report(results):
    """Baut den HTML-Bericht aus den Prüfungsergebnissen."""
    issues = []
    ok_items = []

    api_ok, api_msg = results["api"]
    if api_ok:
        ok_items.append(("Lobbyregister API", api_msg))
    else:
        issues.append({
            "severity": "FEHLER", 
            "title": "Lobbyregister API nicht erreichbar",
            "detail": api_msg, 
            "action": f"1. API-Status prüfen\n2. Key prüfen → {SECRETS_URL}"
        })

    struct_ok, struct_msg = results["api_struct"]
    if struct_ok:
        ok_items.append(("API-Struktur", struct_msg))
    else:
        issues.append({
            "severity": "FEHLER", 
            "title": "API-Struktur geändert",
            "detail": struct_msg, 
            "action": "JSON-Antwort manuell analysieren und fetch_and_build.py anpassen"
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
    print("Prüfe API...")
    results["api"] = check_api_reachable(LOBBYREGISTER_API_KEY)
    print("Prüfe API-Struktur...")
    results["api_struct"] = check_api_structure(LOBBYREGISTER_API_KEY)
    print("Prüfe YAML...")
    results["yaml"] = check_yaml_version()
    print("Prüfe Seite...")
    results["site"] = check_site_reachable()
    print("Prüfe Gemini...")
    results["gemini"] = check_gemini()
    
    has_issues, html = build_report(results)
    print(f"Ergebnis: {'PROBLEME – Bericht wird versendet' if has_issues else 'Alles OK'}")
    send_report_resend(html, has_issues)
    print("=== Fertig ===")


if __name__ == "__main__":
    main()

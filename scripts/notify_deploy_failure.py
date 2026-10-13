"""
notify_deploy_failure.py
========================
Admin-Mail, wenn der GitHub-Pages-Deploy des Tageslaufs auch nach dem
zweiten Versuch fehlschlaegt (siehe .github/workflows/update.yml).

Aufbau und Absender entsprechen dem woechentlichen Statusbericht
(health_check.py): Problem-Beschreibung plus "Was zu tun ist".

Umgebungsvariablen:
  RESEND_API_KEY, ADMIN_EMAIL  – Mailversand (fehlt eines: nur Hinweis im Log)
  RUN_URL                      – Link auf den fehlgeschlagenen Workflow-Lauf
  ACTIONS_URL                  – Link auf die Actions-Uebersicht (optional)
  SITE_URL                     – Webseite (optional)
"""

import os
import sys
from datetime import date

import requests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
RUN_URL = os.environ.get("RUN_URL", "")
ACTIONS_URL = os.environ.get("ACTIONS_URL", "https://github.com/BMWE-IIIA4/lobbyregister-monitor/actions")
SITE_URL = os.environ.get("SITE_URL", "https://lobbyregister-bot.de")


def build_issue(run_url, is_monday):
    """Liefert (detail, action) fuer den Problem-Block."""
    detail = (
        "Der letzte Schritt des Tageslaufs („Auf GitHub Pages deployen“) ist auch nach einem "
        "zweiten Versuch fehlgeschlagen. Die Daten wurden vorher gesichert (data-store-Branch), "
        "die Webseite wurde aber nicht aktualisiert und zeigt weiter den Stand des letzten "
        "erfolgreichen Deploys. Häufigste Ursache ist eine kurzzeitige Störung auf GitHub-Seite "
        "(Pages/Actions-API), z. B. die Meldung „No artifacts named github-pages were found“."
    )
    steps = [
        f'Lauf öffnen und den Step „Auf GitHub Pages deployen“ prüfen: <a href="{run_url}" style="color:#004B87">{run_url}</a>'
        if run_url else "Lauf in der Actions-Übersicht öffnen und den Step „Auf GitHub Pages deployen“ prüfen",
        "Auf githubstatus.com prüfen, ob eine Störung bei Pages oder Actions gemeldet ist",
        "Im Lauf „Re-run failed jobs“ klicken (kein Datenverlust: Bestand kommt aus dem data-store-Branch, "
        "bereits geprüfte Einträge aus dem Gemini-Cache)",
        "Alternativ nichts tun: Der nächste Tageslauf aktualisiert die Seite automatisch (bis dahin veralteter Stand)",
    ]
    if is_monday:
        steps.append(
            "Heute ist Montag: Wochenmail und Selbsttest hängen am fehlgeschlagenen Job und wurden "
            "übersprungen. Ein Re-run am selben Tag holt beide nach"
        )
    action = "<br>".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    return detail, action


def build_html(run_url=RUN_URL, today=None, is_monday=None):
    if today is None:
        today = date.today()
    if is_monday is None:
        is_monday = today.weekday() == 0
    detail, action_html = build_issue(run_url, is_monday)
    today_str = today.strftime("%d.%m.%Y")
    color, bg = "#c62828", "#ffebee"
    return f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#222;margin:0;padding:0;background:#f5f5f5">
<div style="max-width:700px;margin:20px auto">
  <div style="background:#004B87;padding:16px 28px">
    <div style="color:#fff;font-size:15px;font-weight:700">Lobbyregister-Monitor · Statusbericht</div>
    <div style="color:#a8c8e8;font-size:11px">{today_str} · Tageslauf (Seiten-Deploy)</div>
  </div>
  <div style="background:#fff;padding:20px 28px">
    <div style="background:{color};color:#fff;padding:10px 16px;font-size:14px;font-weight:700;margin-bottom:20px">Status: 1 Problem(e) gefunden</div>
    <h3 style="font-size:14px;color:#c62828;margin-bottom:12px">Probleme:</h3>
    <div style="border:1px solid {color};background:{bg};margin-bottom:12px;overflow:hidden">
      <div style="background:{color};padding:6px 12px;color:#fff;font-size:12px;font-weight:700">FEHLER: Seiten-Deploy fehlgeschlagen</div>
      <div style="padding:10px 12px;font-size:12px;color:#333">
        <p style="margin-bottom:8px"><strong>Problem:</strong> {detail}</p>
        <p><strong>Was zu tun ist:</strong><br>{action_html}</p>
      </div>
    </div>
    <hr style="border:none;border-top:1px solid #e0e8f0;margin:20px 0">
    <p style="font-size:12px;color:#888">
      <a href="{ACTIONS_URL}" style="color:#004B87">Actions</a> ·
      <a href="{SITE_URL}" style="color:#004B87">Webseite</a> ·
      <a href="{SITE_URL}/wartung.html" style="color:#004B87">Wartung</a>
    </p>
  </div>
</div></body></html>"""


def main():
    if not RESEND_API_KEY or not ADMIN_EMAIL:
        print("WARNUNG: RESEND_API_KEY oder ADMIN_EMAIL fehlt – keine Admin-Mail versendet")
        return 0
    today = date.today()
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": "Lobbyregister-Monitor Systemcheck <healthcheck@lobbyregister-bot.de>",
                "to": [ADMIN_EMAIL],  # Separate Admin-Mail, NICHT die Hauptempfaenger
                "subject": f"⚠️ Lobbyregister-Monitor: Seiten-Deploy fehlgeschlagen – {today.strftime('%d.%m.%Y')}",
                "html": build_html(today=today),
            },
            timeout=30,
        )
        resp.raise_for_status()
        print(f"✓ Deploy-Alarm an {ADMIN_EMAIL} gesendet (via Resend)")
    except Exception as e:
        print(f"✗ Fehler beim E-Mail-Versand: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
send_email.py
=============
Versendet die wöchentliche Übersichts-Mail mit neuen Stellungnahmen.
"""

import json
import os
import requests
from datetime import datetime, timedelta, date
from collections import defaultdict

# ── Konfiguration ──────────────────────────────────────────────────────────────

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "")
SITE_URL = os.environ.get("SITE_URL", "https://lobbyregister-bot.de")

# ── HTML-Template ──────────────────────────────────────────────────────────────

def format_date_de(iso_date):
    """Formatiert ISO-Datum zu deutschem Format."""
    if not iso_date:
        return "–"
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return iso_date


def render_entry_card(stmt):
    """Rendert eine einzelne Stellungnahme als HTML-Karte."""
    title = stmt["regulatory_project_title"]
    org = stmt["org_name"]
    org_url = stmt.get("org_url", "")
    sending = format_date_de(stmt.get("sending_date"))
    upload = format_date_de(stmt.get("upload_date"))
    
    summary = stmt.get("summary", "") or "Keine Beschreibung verfügbar."
    
    # HTML-Tags erlauben (für <b> von Gemini), aber andere Zeichen escapen
    summary = summary.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    # <b> und </b> wieder herstellen
    summary = summary.replace('&lt;b&gt;', '<b style="background:#fef9c3;padding:0.1rem 0.2rem;border-radius:2px;font-weight:700">')
    summary = summary.replace('&lt;/b&gt;', '</b>')
    
    recipients = stmt.get("recipients", [])
    fields = stmt.get("fields", [])
    pdf_url = stmt.get("pdf_url", "")
    pdf_pages = stmt.get("pdf_pages", 0)
    statement_url = stmt.get("statement_url", "")
    
    # Prüfen ob KI-Prüfung fehlt
    has_ai_summary = bool(stmt.get("summary") and len(stmt.get("summary", "")) > 50)
    ai_warning = "" if has_ai_summary else '<div style="background:#fff3cd;border-left:3px solid #ffc107;padding:0.4rem 0.75rem;margin-top:0.5rem;font-size:0.75rem;color:#856404">⚠️ KI-Prüfung steht noch aus</div>'
    
    org_html = f'<a href="{org_url}" style="color:#004B87;text-decoration:none">{org}</a>' if org_url else org
    
    recip_badges = "".join(
        f'<span style="display:inline-block;font-size:0.7rem;font-weight:600;padding:0.2rem 0.5rem;'
        f'margin:0.1rem 0.2rem 0.1rem 0;background:#eff6ff;color:#1e40af;border-radius:12px;'
        f'border:1px solid #bfdbfe">{r}</span>'
        for r in recipients
    )
    
    field_tags = "".join(
        f'<span style="display:inline-block;font-size:0.7rem;font-weight:500;padding:0.2rem 0.5rem;'
        f'margin:0.1rem 0.2rem 0.1rem 0;background:#f1f5f9;color:#475569;border-radius:12px;'
        f'border:1px solid #e2e8f0">{f["label"]}</span>'
        for f in fields
    )
    
    sg_label = f' ({stmt.get("sg_number", "")})' if stmt.get("sg_number") else ""
    stmt_link = f'<a href="{statement_url}" style="color:#004B87;text-decoration:none;font-size:0.8rem">↗ Stellungnahme im Lobbyregister{sg_label}</a>' if statement_url else ''
    pdf_link = f'<a href="{pdf_url}" style="color:#004B87;text-decoration:none;font-size:0.8rem">↗ PDF ({pdf_pages} Seiten)</a>' if pdf_url else '<span style="color:#999;font-size:0.8rem">Kein PDF</span>'
    
    # KOMPAKTERES DESIGN: kleinere Abstände, weniger Padding
    return f'''
    <div style="background:#fff;border-radius:8px;margin-bottom:0.75rem;box-shadow:0 1px 3px rgba(0,0,0,0.1);border:1px solid #e2e8f0;overflow:hidden">
      <div style="padding:0.85rem 1rem;font-size:1rem;font-weight:700;color:#0f172a;line-height:1.3;border-left:4px solid #004B87">{title}</div>
      {ai_warning}
      <div style="display:flex;flex-wrap:wrap;border-top:1px solid #f1f5f9;background:#fafafa">
        <div style="flex:1;min-width:200px;padding:0.6rem 1rem;font-size:0.8rem;border-right:1px solid #f1f5f9">
          <div style="font-size:0.65rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.25rem">Bereitgestellt von</div>
          {org_html}
        </div>
        <div style="padding:0.6rem 1rem;font-size:0.8rem;border-right:1px solid #f1f5f9">
          <div style="font-size:0.65rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.25rem">Datum Stellungnahme</div>
          {sending}
        </div>
        <div style="padding:0.6rem 1rem;font-size:0.8rem">
          <div style="font-size:0.65rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.25rem">Hochgeladen am</div>
          {upload}
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;border-top:1px solid #f1f5f9;background:#fff">
        <div style="padding:0.6rem 1rem;font-size:0.8rem;border-right:1px solid #f1f5f9">
          <div style="font-size:0.65rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.25rem">Adressaten</div>
          {recip_badges}
        </div>
        <div style="padding:0.6rem 1rem;font-size:0.8rem">
          <div style="font-size:0.65rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.25rem">Themenfelder</div>
          {field_tags}
        </div>
      </div>
      <div style="padding:0.85rem 1rem;font-size:0.85rem;color:#334155;border-top:1px solid #f1f5f9;line-height:1.5;background:#fff">
        <div style="font-size:0.65rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.25rem">Inhalt</div>
        {summary}
      </div>
      <div style="display:flex;border-top:1px solid #f1f5f9;background:#f8fafc">
        <div style="padding:0.6rem 1rem;flex:1;border-right:1px solid #f1f5f9">{stmt_link}</div>
        <div style="padding:0.6rem 1rem;flex:1">{pdf_link}</div>
      </div>
    </div>
    '''


def build_email_html(statements, week_start, week_end):
    """Baut die komplette E-Mail als HTML."""
    
    # Nach Datum gruppieren
    by_date = defaultdict(list)
    for stmt in statements:
        key = stmt.get("upload_date") or stmt.get("sending_date") or "unbekannt"
        by_date[key].append(stmt)
    
    # Einträge nach Datum sortiert rendern
    day_sections = ""
    for iso_date in sorted(by_date.keys(), reverse=True):
        day_stmts = sorted(by_date[iso_date], key=lambda x: x.get("priority", 99))
        
        try:
            d = date.fromisoformat(iso_date)
            days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
            months = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
                     "Juli", "August", "September", "Oktober", "November", "Dezember"]
            day_label = f"{days[d.weekday()]}, {d.day}. {months[d.month]} {d.year}"
        except:
            day_label = iso_date
        
        cards = "".join(render_entry_card(s) for s in day_stmts)
        
        day_sections += f'''
        <div style="margin-bottom:1.5rem">
          <div style="font-size:0.8rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;padding-bottom:0.4rem;border-bottom:2px solid #e2e8f0;margin-bottom:1rem">{day_label}</div>
          {cards}
        </div>
        '''
    
    week_label = f"{week_start.strftime('%d.%m.')} – {week_end.strftime('%d.%m.%Y')}"
    
    html = f'''<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lobbyregister-Monitor – Wochenübersicht</title>
</head>
<body style="font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;font-size:14px;color:#222;margin:0;padding:0;background:#f5f5f5">
<div style="max-width:800px;margin:20px auto;background:#fff">
  <div style="background:#004B87;padding:18px 24px">
    <div style="color:#fff;font-size:16px;font-weight:700">Lobbyregister-Monitor – Wochenübersicht der KW {week_start.isocalendar()[1]}</div>
    <div style="color:#a8c8e8;font-size:12px;margin-top:4px">Neue Stellungnahmen der letzten 7 Tage ({week_label})</div>
  </div>
  
  <div style="background:#e3f2fd;border-left:3px solid #1976d2;padding:12px 20px;font-size:12px;color:#0d47a1;line-height:1.5">
    <strong>Hinweis:</strong> Dies ist eine automatisch generierte Übersicht. 
    Für alle Details besuchen Sie bitte die <a href="{SITE_URL}" style="color:#004B87;font-weight:600">vollständige Webseite</a>.
  </div>
  
  <div style="padding:20px 24px">
    {day_sections}
  </div>
  
  <div style="background:#f8fafc;padding:16px 24px;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b">
    <div style="margin-bottom:8px">
      <a href="{SITE_URL}" style="color:#004B87;text-decoration:none">Zur Webseite</a> · 
      <a href="{SITE_URL}/hilfe.html" style="color:#004B87;text-decoration:none">Nutzungsanleitung</a> · 
      <a href="{SITE_URL}/impressum.html" style="color:#004B87;text-decoration:none">Impressum</a>
    </div>
    <div style="color:#94a3b8">Daten: <a href="https://www.lobbyregister.bundestag.de" style="color:#64748b">Lobbyregister des Deutschen Bundestages</a></div>
  </div>
</div>
</body>
</html>'''
    
    return html


# ── Hauptprogramm ──────────────────────────────────────────────────────────────

def main():
    print("=== Lobbyregister Monitor – E-Mail-Versand ===")
    
    # Daten laden
    try:
        with open("docs/data.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("FEHLER: docs/data.json nicht gefunden")
        return
    
    statements = data.get("statements", [])
    
    # Letzte 7 Tage filtern
    today = date.today()
    week_start = today - timedelta(days=7)
    week_end = today
    
    recent = []
    for stmt in statements:
        upload_str = stmt.get("upload_date") or stmt.get("sending_date")
        if not upload_str:
            continue
        try:
            upload_date = date.fromisoformat(upload_str)
            if upload_date >= week_start:
                recent.append(stmt)
        except ValueError:
            continue
    
    if not recent:
        print("Keine neuen Einträge in den letzten 7 Tagen – keine Mail versendet.")
        return
    
    print(f"{len(recent)} neue Einträge gefunden ({week_start.strftime('%d.%m.')} – {week_end.strftime('%d.%m.%Y')})")
    
    # E-Mail generieren
    html = build_email_html(recent, week_start, week_end)
    
    # Versenden
    if not RESEND_API_KEY or not EMAIL_RECIPIENT:
        print("WARNUNG: RESEND_API_KEY oder EMAIL_RECIPIENT fehlt – Mail wird nicht versendet")
        # Zu Testzwecken: HTML speichern
        with open("/tmp/email_preview.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("E-Mail-Vorschau gespeichert: /tmp/email_preview.html")
        return
    
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": "Lobbyregister-Monitor <noreply@lobbyregister-bot.de>",
                "to": [EMAIL_RECIPIENT],
                "subject": f"Lobbyregister-Monitor – Wochenübersicht der KW {week_start.isocalendar()[1]}",
                "html": html
            },
            timeout=30
        )
        resp.raise_for_status()
        print(f"✓ E-Mail erfolgreich an {EMAIL_RECIPIENT} versendet")
    except Exception as e:
        print(f"✗ Fehler beim E-Mail-Versand: {e}")


if __name__ == "__main__":
    main()

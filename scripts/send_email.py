"""
send_email.py
=============
Outlook-kompatible Wochenmail mit verbessertem Layout
"""

import json
import os
import requests
from datetime import date, timedelta
from pathlib import Path

RESEND_API_KEY = os.environ["RESEND_API_KEY"]
EMAIL_RECIPIENT = os.environ["EMAIL_RECIPIENT"]

# Absender (WICHTIG: Domain muss bei Resend verifiziert sein)
EMAIL_SENDER = "Lobbyregister Monitor <update@lobbyregister-bot.de>"


# ──────────────────────────────────────────────────────────────
# Daten laden
# ──────────────────────────────────────────────────────────────

def load_data():
    path = Path("docs/data.json")
    if not path.exists():
        raise FileNotFoundError("docs/data.json nicht gefunden")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_week_statements(statements):
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    return [
        s for s in statements
        if (s.get("upload_date") or s.get("sending_date") or "0000-00-00") >= cutoff
    ]


def format_date(iso_date):
    if not iso_date:
        return "–"
    try:
        return date.fromisoformat(iso_date).strftime("%d.%m.%Y")
    except:
        return iso_date


def calc_delay_days(sending, upload):
    try:
        if not sending or not upload:
            return ""
        d1 = date.fromisoformat(sending)
        d2 = date.fromisoformat(upload)
        diff = (d2 - d1).days
        if diff > 0:
            return f" (+{diff} Tage)"
        return ""
    except:
        return ""


# ──────────────────────────────────────────────────────────────
# Rendering-Helfer
# ──────────────────────────────────────────────────────────────

def render_badges(items):
    html = ""
    for item in items:
        html += f"""
        <span style="
            display:inline-block;
            font-size:10px;
            padding:2px 6px;
            margin-right:6px;
            margin-bottom:4px;
            background:#dbeafe;
            color:#1e3a8a;
            border:1px solid #bfdbfe;
            border-radius:3px;
        ">{item}</span>
        """
    return html or "–"


def render_fields(fields):
    html = ""
    for f in fields:
        label = f.get("label", "")
        html += f"""
        <span style="
            display:inline-block;
            margin-right:6px;
            margin-bottom:4px;
        ">{label}</span>
        """
    return html or "–"


# ──────────────────────────────────────────────────────────────
# Eintrag rendern
# ──────────────────────────────────────────────────────────────

def render_entry(stmt):
    title = stmt["regulatory_project_title"]
    org = stmt["org_name"]
    org_url = stmt.get("org_url", "")

    sending_raw = stmt.get("sending_date")
    upload_raw = stmt.get("upload_date")

    sending = format_date(sending_raw)
    upload = format_date(upload_raw)
    delay = calc_delay_days(sending_raw, upload_raw)

    recipients = stmt.get("recipients", [])
    fields = stmt.get("fields", [])
    summary = stmt.get("summary") or "Keine Beschreibung verfügbar."

    stmt_url = stmt.get("statement_url", "")
    pdf_url = stmt.get("pdf_url", "")
    pdf_pages = stmt.get("pdf_pages", 0)

    org_html = f'<a href="{org_url}" style="color:#004B87;text-decoration:none;">{org}</a>' if org_url else org

    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #d0d8e4;margin-bottom:18px;font-family:Arial,Helvetica,sans-serif;font-size:12px;">
    
    <!-- Zeile 1 -->
    <tr>
        <td colspan="2" style="background:#eef3f9;padding:6px 10px;font-weight:bold;color:#003366;">
            {title}
        </td>
    </tr>

    <!-- Zeile 2 -->
    <tr>
        <td width="65%" style="padding:6px 10px;vertical-align:top;">
            <div style="font-size:9px;color:#888;font-weight:bold;">Bereitgestellt von</div>
            <div>{org_html}</div>
        </td>
        <td width="35%" style="padding:6px 10px;vertical-align:top;">
            <div style="font-size:9px;color:#888;font-weight:bold;">Stellungnahme</div>
            <div>{sending}</div>
            <div style="font-size:9px;color:#888;font-weight:bold;margin-top:3px;">Hochgeladen</div>
            <div>{upload}{delay}</div>
        </td>
    </tr>

    <!-- Zeile 3 (getauscht) -->
    <tr>
        <td style="padding:6px 10px;vertical-align:top;">
            <div style="font-size:9px;color:#888;font-weight:bold;">Themenfelder</div>
            {render_fields(fields)}
        </td>
        <td style="padding:6px 10px;vertical-align:top;">
            <div style="font-size:9px;color:#888;font-weight:bold;">Adressaten</div>
            {render_badges(recipients)}
        </td>
    </tr>

    <!-- Zeile 4 -->
    <tr>
        <td colspan="2" style="padding:6px 10px;">
            <div style="font-size:9px;color:#888;font-weight:bold;">Inhalt</div>
            <div style="line-height:1.4;">{summary}</div>
        </td>
    </tr>

    <!-- Zeile 5 -->
    <tr>
        <td style="padding:6px 10px;">
            {"<a href='" + stmt_url + "' style='color:#004B87;text-decoration:none;'>↗ Stellungnahme</a>" if stmt_url else ""}
        </td>
        <td style="padding:6px 10px;">
            {"<a href='" + pdf_url + "' style='color:#004B87;text-decoration:none;'>↗ PDF (" + str(pdf_pages) + " S.)</a>" if pdf_url else ""}
        </td>
    </tr>

</table>
"""


# ──────────────────────────────────────────────────────────────
# Mail bauen
# ──────────────────────────────────────────────────────────────

def build_email(statements):
    week_stmts = get_week_statements(statements)

    today = date.today()
    kw = today.isocalendar()[1]

    start = (today - timedelta(days=7)).strftime("%d.%m.")
    end = today.strftime("%d.%m.%Y")

    entries_html = "".join(render_entry(s) for s in week_stmts)

    return f"""
<html>
<body style="background:#f5f5f5;margin:0;padding:20px;font-family:Arial,Helvetica,sans-serif;">
<div style="max-width:700px;margin:auto;background:#ffffff;padding:16px;">

<h2 style="color:#003366;margin-top:0;">
Lobbyregister-Monitor – Wochenübersicht der KW {kw}
</h2>

<p style="font-size:12px;color:#555;margin-bottom:6px;">
Neue Stellungnahmen der letzten 7 Tage ({start}–{end})
</p>

<p style="font-size:13px;margin-bottom:16px;">
<a href="https://lobbyregister-bot.de" style="color:#004B87;font-weight:bold;text-decoration:none;">
Übersicht aller Einträge: lobbyregister-bot.de
</a>
</p>

{entries_html}

</div>
</body>
</html>
"""


# ──────────────────────────────────────────────────────────────
# Versand
# ──────────────────────────────────────────────────────────────

def send_email(html):
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": EMAIL_SENDER,
            "to": EMAIL_RECIPIENT,
            "subject": "Lobbyregister-Monitor – Wochenupdate",
            "html": html,
        },
    )

    if response.status_code >= 300:
        raise RuntimeError(f"E-Mail Versand fehlgeschlagen: {response.text}")


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def main():
    data = load_data()
    html = build_email(data["statements"])
    send_email(html)
    print("E-Mail erfolgreich versendet.")


if __name__ == "__main__":
    main()

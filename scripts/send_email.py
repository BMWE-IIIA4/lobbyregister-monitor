"""
send_email.py
=============
Liest die gespeicherten Daten und versendet die woechentliche
Zusammenfassungs-Mail ueber Resend.
"""

import json
import os
import re
import requests
from datetime import datetime, date, timedelta
from collections import defaultdict
from pathlib import Path

RESEND_API_KEY = os.environ["RESEND_API_KEY"]
EMAIL_RECIPIENT = os.environ["EMAIL_RECIPIENT"]
SITE_URL = os.environ.get("SITE_URL", "https://lobbyregister-bot.de")
SENDER = "Lobbyregister Monitor <update@lobbyregister-bot.de>"

THEME_ORDER = [
    ("Energie & Wasserstoff", ["FOI_ENERGY", "FOI_ENERGY_OVERALL", "FOI_ENERGY_RENEWABLE",
                               "FOI_ENERGY_FOSSILE", "FOI_ENERGY_NET", "FOI_ENERGY_NUCLEAR",
                               "FOI_ENERGY_OTHER", "FOI_ENERGY_ELECTRICITY", "FOI_ENERGY_GAS",
                               "FOI_ENERGY_HYDROGEN"]),
    ("Klimaschutz",           ["FOI_ENVIRONMENT_CLIMATE"]),
    ("EU-Binnenmarkt & EU-Gesetzgebung", ["FOI_EU_DOMESTIC_MARKET", "FOI_EU_LAWS"]),
    ("Bundestag",             ["FOI_BUNDESTAG"]),
    ("Wettbewerbsrecht",      ["FOI_ECONOMY_COMPETITION_LAW"]),
    ("Politisches Leben, Parteien", ["FOI_POLITICAL_PARTIES"]),
    ("Sonstige",              ["FOI_OTHER"]),
]


def load_data():
    path = Path("docs/data.json")
    if not path.exists():
        raise FileNotFoundError("docs/data.json nicht gefunden")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_week_statements(statements):
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    return [s for s in statements
            if (s.get("upload_date") or s.get("sending_date") or "0000-00-00") >= cutoff]


def format_date_de(iso_date):
    if not iso_date:
        return "\u2013"
    try:
        return date.fromisoformat(iso_date).strftime("%d.%m.%Y")
    except ValueError:
        return iso_date


def sanitize_summary_html(summary):
    if not summary:
        return ""
    summary = re.sub(r'<(?!/?b>)', '&lt;', summary)
    summary = summary.replace('>', '&gt;').replace('<b&gt;', '<b>').replace('</b&gt;', '</b>')
    return summary


def assign_theme(stmt):
    field_codes = {f["code"] for f in stmt.get("fields", [])}
    for theme_name, codes in THEME_ORDER:
        if field_codes & set(codes):
            return theme_name
    return "Sonstige"


def render_entry_html(stmt):
    title = stmt["regulatory_project_title"]
    org = stmt["org_name"]
    sending = format_date_de(stmt.get("sending_date"))
    upload = format_date_de(stmt.get("upload_date"))
    summary = sanitize_summary_html(stmt.get("summary", ""))
    if len(summary) > 400:
        summary = summary[:400] + "..."
    recipients = stmt.get("recipients", [])
    pdf_url = stmt.get("pdf_url", "")
    pdf_pages = stmt.get("pdf_pages", 0)
    sn = stmt.get("statement_number") or stmt.get("sg_number", "")
    stmt_url = stmt.get("statement_url") or ""

    badges = "".join(
        f'<span style="display:inline-block;font-size:9px;padding:1px 5px;margin:1px 2px 1px 0;'
        f'background:#dbeafe;color:#1e3a8a;border:1px solid #bfdbfe;font-weight:600">{r}</span>'
        for r in recipients)

    field_tags = "".join(
        f'<span style="display:inline-block;font-size:9px;padding:1px 5px;margin:1px 2px 1px 0;'
        f'background:#f1f5f9;color:#475569;border:1px solid #e2e8f0">{f["label"]}</span>'
        for f in stmt.get("fields", []))

    pdf_link = (f'<a href="{pdf_url}" style="color:#004B87;text-decoration:none">PDF ({pdf_pages} S.)</a>'
                if pdf_url else "Kein PDF")

    sn_label = f" ({sn})" if sn else ""
    stmt_link = (f'<a href="{stmt_url}" style="color:#004B87;text-decoration:none;margin-right:16px">'
                 f'Registereintrag{sn_label}</a>') if stmt_url else ""

    label_style = 'font-size:9px;font-weight:700;text-transform:uppercase;color:#94a3b8;letter-spacing:0.03em'

    return f"""
    <div style="border:1px solid #e2e8f0;margin-bottom:10px;overflow:hidden;border-radius:6px">
      <div style="padding:10px 14px;font-size:14px;font-weight:700;color:#0f172a;border-left:4px solid #004B87;background:#fff">{title}</div>
      <div style="padding:8px 14px;font-size:12px;color:#334155;border-top:1px solid #f1f5f9;background:#fafafa">
        <div style="margin-bottom:6px"><span style="{label_style}">Bereitgestellt von</span><br>{org}</div>
        <div style="display:inline-block;margin-right:20px"><span style="{label_style}">Stellungnahme</span><br>{sending}</div>
        <div style="display:inline-block"><span style="{label_style}">Hochgeladen</span><br>{upload}</div>
      </div>
      <div style="padding:8px 14px;font-size:12px;border-top:1px solid #f1f5f9;background:#fff">
        <div style="margin-bottom:4px"><span style="{label_style}">Adressaten</span><br>{badges}</div>
        <div><span style="{label_style}">Themenfelder</span><br>{field_tags}</div>
      </div>
      {'<div style="padding:8px 14px;font-size:12px;color:#334155;border-top:1px solid #f1f5f9;line-height:1.55;background:#fff"><span style="' + label_style + '">Inhalt</span><br>' + summary + '</div>' if summary else ''}
      <div style="padding:6px 14px;font-size:11px;border-top:1px solid #f1f5f9;background:#f8fafc">
        {stmt_link} {pdf_link}
      </div>
    </div>"""


def build_email_html(statements, generated_at):
    week_stmts = get_week_statements(statements)
    total = len(week_stmts)

    # Pending-Hinweis
    pending_count = sum(1 for s in week_stmts if s.get("gemini_status") == "pending")
    pending_warning = ""
    if pending_count > 0:
        pending_warning = f"""
    <div style="background:#fefce8;border:1px solid #fde047;border-radius:6px;
                padding:10px 14px;margin-bottom:16px;font-size:12px;color:#854d0e;line-height:1.5">
      <strong>Hinweis:</strong> {pending_count} Eintrag{"e" if pending_count != 1 else ""} konnte{"n" if pending_count != 1 else ""}
      noch nicht per KI gepr&uuml;ft werden und wird ungefiltert angezeigt.
    </div>"""

    by_theme = defaultdict(list)
    for stmt in week_stmts:
        by_theme[assign_theme(stmt)].append(stmt)

    theme_blocks = ""
    for theme_name, _ in THEME_ORDER:
        stmts = by_theme.get(theme_name, [])
        if not stmts:
            continue
        entries_html = "".join(render_entry_html(s) for s in stmts)
        theme_blocks += f"""
        <div style="margin-bottom:20px">
          <div style="margin-bottom:8px"><span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:#fff;background:#004B87;padding:4px 10px;display:inline-block;border-radius:3px">{theme_name}</span></div>
          {entries_html}
        </div>"""

    today = date.today()
    kw = today.isocalendar()[1]
    year = today.year
    week_start = (today - timedelta(days=today.weekday() + 7)).strftime("%d.%m.")
    week_end = (today - timedelta(days=today.weekday() + 1)).strftime("%d.%m.%Y")

    no_entries_msg = ""
    if total == 0:
        no_entries_msg = '<div style="padding:20px;text-align:center;color:#94a3b8;font-size:13px">Keine neuen Eintr&auml;ge diese Woche.</div>'

    themen_subtitle = "Energie &amp; Wasserstoff &middot; Klimaschutz &middot; EU-Binnenmarkt &middot; EU-Gesetzgebung &middot; Wettbewerbsrecht &middot; Politisches Leben/Parteien &middot; Sonstige"

    html = f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,Helvetica,sans-serif">
<div style="max-width:700px;margin:20px auto">
  <div style="background:#004B87;padding:18px 28px;border-radius:8px 8px 0 0">
    <div style="color:#fff;font-size:16px;font-weight:700;margin-bottom:3px">Lobbyregister-Monitor &middot; KW {kw}/{year}</div>
    <div style="color:#a8c8e8;font-size:11px">Neue Stellungnahmen &amp; Gutachten &middot; {week_start}&ndash;{week_end}</div>
  </div>
  <div style="background:#f0f4f8;padding:10px 28px;border-bottom:1px solid #e2e8f0;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">
    <div style="font-size:12px;color:#64748b">{total} neue Eintrag{"" if total == 1 else "e"} diese Woche</div>
  </div>
  <div style="padding:20px 28px;background:#fff">
    {pending_warning}
    <p style="font-size:13px;color:#64748b;margin-bottom:4px;line-height:1.5">
      Stellungnahmen und Gutachten mit Adressat BMWE oder Bundestag.</p>
    <p style="font-size:11px;color:#94a3b8;margin-bottom:18px;padding-bottom:14px;border-bottom:1px solid #f1f5f9">
      Themenfelder: {themen_subtitle}</p>
    {no_entries_msg}{theme_blocks}
    <div style="text-align:center;margin:24px 0 8px">
      <a href="{SITE_URL}" style="display:inline-block;background:#004B87;color:#fff;font-size:14px;font-weight:700;padding:12px 28px;border-radius:6px;text-decoration:none">
        Vollst&auml;ndige &Uuml;bersicht auf lobbyregister-bot.de
      </a>
    </div>
  </div>
  <div style="padding:14px 28px;background:#f0f4f8;border-top:1px solid #e2e8f0;font-size:11px;color:#94a3b8;line-height:1.6;border-radius:0 0 8px 8px">
    Automatisch generiert. Daten: <a href="https://www.lobbyregister.bundestag.de" style="color:#004B87;text-decoration:none">Lobbyregister des Deutschen Bundestages</a>.
  </div>
</div></body></html>"""
    return html, total


def send_email(html_body, total):
    today = date.today()
    kw = today.isocalendar()[1]
    year = today.year
    subject = f"Lobbyregister-Monitor KW {kw}/{year}: {total} neue Eintrag{'e' if total != 1 else ''}"
    if total == 0:
        subject = f"Lobbyregister-Monitor KW {kw}/{year}: Keine neuen Eintr\u00e4ge"

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={"from": SENDER, "to": [EMAIL_RECIPIENT], "subject": subject, "html": html_body},
        timeout=30,
    )
    resp.raise_for_status()
    print(f"Mail versendet an {EMAIL_RECIPIENT} (ID: {resp.json().get('id')})")


def main():
    print("=== Lobbyregister Monitor - E-Mail-Versand ===")
    data = load_data()
    statements = data.get("statements", [])
    generated_at = data.get("generated_at", datetime.now().isoformat())
    html_body, total = build_email_html(statements, generated_at)
    send_email(html_body, total)
    print(f"=== Fertig ({total} Eintraege) ===")

if __name__ == "__main__":
    main()

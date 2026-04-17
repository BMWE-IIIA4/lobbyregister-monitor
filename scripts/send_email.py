"""
send_email.py
=============
Liest die gespeicherten Daten und versendet die wöchentliche
Zusammenfassungs-Mail über Resend.
"""

import json
import os
import requests
from datetime import datetime, date, timedelta
from collections import defaultdict
from pathlib import Path

RESEND_API_KEY = os.environ["RESEND_API_KEY"]
EMAIL_RECIPIENT = os.environ["EMAIL_RECIPIENT"]
SITE_URL = os.environ.get("SITE_URL", "https://bmwe-iiia4.github.io/lobbyregister-monitor")

THEME_ORDER = [
    ("Energie & Wasserstoff", ["FOI_ENERGY_OVERALL", "FOI_ENERGY_RENEWABLE", "FOI_ENERGY_FOSSILE",
                               "FOI_ENERGY_NET", "FOI_ENERGY_NUCLEAR", "FOI_ENERGY_OTHER",
                               "FOI_ENERGY_ELECTRICITY", "FOI_ENERGY_GAS", "FOI_ENERGY_HYDROGEN",
                               "FOI_ENERGY"]),
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
    return [
        s for s in statements
        if (s.get("sending_date") or s.get("upload_date") or "0000-00-00") >= cutoff
    ]


def format_date_de(iso_date):
    if not iso_date:
        return "–"
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return iso_date


def assign_theme(stmt):
    field_codes = {f["code"] for f in stmt.get("fields", [])}
    for theme_name, codes in THEME_ORDER:
        if field_codes & set(codes):
            return theme_name
    return "Sonstige"


def render_entry_html(stmt):
    title = stmt["regulatory_project_title"]
    org = stmt["org_name"]
    org_url = stmt.get("org_url", "")
    sending = format_date_de(stmt.get("sending_date"))
    upload = format_date_de(stmt.get("upload_date"))
    summary = stmt.get("summary") or ""
    recipients = stmt.get("recipients", [])
    pdf_url = stmt.get("pdf_url", "")
    pdf_pages = stmt.get("pdf_pages", 0)
    sg_number = stmt.get("sg_number", "")
    statement_url = stmt.get("statement_url", "")

    org_html = f'<a href="{org_url}" style="color:#004B87;text-decoration:none">{org}</a>' if org_url else org

    badges = "".join(
        f'<span style="display:inline-block;font-size:11px;padding:1px 5px;margin:1px 2px 1px 0;'
        f'background:#dbeafe;color:#1e3a8a;border:1px solid #bfdbfe;font-weight:600">{r}</span>'
        for r in recipients
    )

    sg_label = f" ({sg_number})" if sg_number else ""
    stmt_link = f'<a href="{statement_url}" style="color:#004B87;text-decoration:none">↗ Stellungnahme{sg_label}</a>' if statement_url else ""
    pdf_link = f'<a href="{pdf_url}" style="color:#004B87;text-decoration:none">↗ PDF ({pdf_pages} S.)</a>' if pdf_url else "Kein PDF"

    return f"""
    <div style="border:1px solid #d0d8e4;margin-bottom:10px;overflow:hidden">
      <div style="background:#eef3f9;padding:9px 14px;font-size:15px;font-weight:700;color:#003366;border-left:3px solid #004B87">{title}</div>
      <table style="width:100%;border-collapse:collapse;border-top:1px solid #e0e8f0">
        <tr>
          <td style="padding:6px 14px;font-size:13px;color:#555;vertical-align:top">
            <span style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#888;display:block;margin-bottom:1px">Von</span>
            {org_html}
          </td>
          <td style="padding:6px 14px;font-size:13px;color:#555;vertical-align:top;white-space:nowrap">
            <span style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#888;display:block;margin-bottom:1px">Stellungnahme</span>
            {sending}
          </td>
          <td style="padding:6px 14px;font-size:13px;color:#555;vertical-align:top;white-space:nowrap">
            <span style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#888;display:block;margin-bottom:1px">Hochgeladen</span>
            {upload}
          </td>
          <td style="padding:6px 14px;font-size:13px;color:#555;vertical-align:top">
            <span style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#888;display:block;margin-bottom:1px">Adressaten</span>
            {badges}
          </td>
        </tr>
      </table>
      {'<div style="padding:8px 14px;font-size:14px;color:#333;border-top:1px solid #e0e8f0;line-height:1.55">' + summary + '</div>' if summary else ''}
      <div style="padding:6px 14px;font-size:13px;border-top:1px solid #e0e8f0;background:#f9fbfd">
        {stmt_link}
        {'<span style="margin:0 8px;color:#ccc">|</span>' if stmt_link and pdf_link else ''}
        {pdf_link}
      </div>
    </div>"""


def build_email_html(statements, generated_at):
    week_stmts = get_week_statements(statements)
    total = len(week_stmts)

    by_theme = defaultdict(list)
    for stmt in week_stmts:
        theme = assign_theme(stmt)
        by_theme[theme].append(stmt)

    theme_blocks = ""
    for theme_name, _ in THEME_ORDER:
        stmts = by_theme.get(theme_name, [])
        if not stmts:
            continue
        entries_html = "".join(render_entry_html(s) for s in stmts)
        theme_blocks += f"""
        <div style="margin-bottom:20px">
          <div style="margin-bottom:10px">
            <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
                         color:#fff;background:#004B87;padding:4px 10px;display:inline-block">
              {theme_name}
            </span>
          </div>
          {entries_html}
        </div>"""

    today = date.today()
    kw = today.isocalendar()[1]
    year = today.year
    week_start = (today - timedelta(days=today.weekday() + 7)).strftime("%d.%m.")
    week_end = (today - timedelta(days=today.weekday() + 1)).strftime("%d.%m.%Y")

    no_entries_msg = ""
    if total == 0:
        no_entries_msg = """
        <div style="padding:20px;text-align:center;color:#888;font-size:14px">
          In der vergangenen Woche wurden keine neuen Einträge mit den gesuchten Kriterien hochgeladen.
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,Helvetica,sans-serif;font-size:15px">
<div style="max-width:700px;margin:20px auto">
  <div style="background:#004B87;padding:16px 28px">
    <div style="color:#fff;font-size:17px;font-weight:700;margin-bottom:2px">
      Lobbyregister-Monitor · KW {kw}/{year}
    </div>
    <div style="color:#a8c8e8;font-size:12px">
      Neue Stellungnahmen &amp; Gutachten · {week_start}–{week_end} · BMWE / Bundestag
    </div>
  </div>
  <div style="background:#f0f4f8;padding:10px 28px;border-bottom:1px solid #d0d8e4;
              display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">
    <div style="font-size:13px;color:#555">{total} neue Eintrag{"" if total == 1 else "e"} diese Woche</div>
    <div><a href="{SITE_URL}" style="font-size:13px;color:#004B87;text-decoration:none;font-weight:600">
      → Vollständige Übersicht öffnen
    </a></div>
  </div>
  <div style="padding:20px 28px;background:#fff">
    <p style="font-size:14px;color:#444;margin-bottom:18px;line-height:1.6;
               padding-bottom:14px;border-bottom:1px solid #e8edf3">
      Neue Stellungnahmen und Gutachten im Lobbyregister mit Adressat BMWE oder Bundestag
      aus den beobachteten Themenfeldern (Energie &amp; Wasserstoff, Klimaschutz, EU-Binnenmarkt,
      EU-Gesetzgebung, Bundestag, Wettbewerbsrecht, Politisches Leben/Parteien, Sonstige).
    </p>
    {no_entries_msg}
    {theme_blocks}
    <hr style="border:none;border-top:1px solid #e0e8f0;margin:16px 0">
    <p style="font-size:13px;color:#555;text-align:center">
      <a href="{SITE_URL}" style="color:#004B87;text-decoration:none;font-weight:600">
        → Alle Einträge und Filteroptionen auf der Übersichtsseite
      </a>
    </p>
  </div>
  <div style="padding:14px 28px;background:#f0f4f8;border-top:1px solid #d0d8e4;
              font-size:12px;color:#777;line-height:1.6">
    Diese Mail wird automatisch jeden Montag generiert und verschickt.
    Alle Daten stammen direkt aus dem
    <a href="https://www.lobbyregister.bundestag.de" style="color:#004B87;text-decoration:none">
      Lobbyregister des Deutschen Bundestages</a>.<br>
    Weiterleitungen und Abonnement-Änderungen: bitte an
    <a href="mailto:martin.jahn@bmwe.bund.de" style="color:#004B87;text-decoration:none">
      Martin Jahn, IIIA4</a> wenden.<br>
    Bei technischen Problemen:
    <a href="{SITE_URL}/wartung.html" style="color:#004B87;text-decoration:none">
      Wartungsdokumentation</a>
    <div style="font-size:10px;color:#999;margin-top:6px">
      Bundesministerium für Wirtschaft und Energie (BMWE) · www.bundeswirtschaftsministerium.de
    </div>
  </div>
</div>
</body></html>"""
    return html, total


def send_email(html_body, total):
    today = date.today()
    kw = today.isocalendar()[1]
    year = today.year

    subject = f"Lobbyregister-Monitor KW {kw}/{year}: {total} neue Eintrag{'e' if total != 1 else ''}"
    if total == 0:
        subject = f"Lobbyregister-Monitor KW {kw}/{year}: Keine neuen Einträge"

    payload = {
        "from": "onboarding@resend.dev",
        "to": [EMAIL_RECIPIENT],
        "subject": subject,
        "html": html_body,
    }

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    print(f"Mail erfolgreich versendet an {EMAIL_RECIPIENT} (ID: {resp.json().get('id')})")


def main():
    print("=== Lobbyregister Monitor – E-Mail-Versand ===")
    data = load_data()
    statements = data.get("statements", [])
    generated_at = data.get("generated_at", datetime.now().isoformat())

    html_body, total = build_email_html(statements, generated_at)
    send_email(html_body, total)
    print(f"=== Fertig ({total} Einträge in Mail) ===")


if __name__ == "__main__":
    main()

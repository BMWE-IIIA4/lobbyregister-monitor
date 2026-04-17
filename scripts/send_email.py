"""
send_email.py
=============
Liest die gespeicherten Daten und versendet die wöchentliche
Zusammenfassungs-Mail über Resend.

Zeigt bei Gemini-Ausfall einen Warnhinweis in der Mail an.
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

# Themenfelder sortiert nach Priorität für die Mail-Gliederung
THEME_ORDER = [
    ("Energie & Wasserstoff", ["FOI_ENERGY", "FOI_ENERGY_RENEWABLE", "FOI_ENERGY_ELECTRICITY",
                               "FOI_ENERGY_GAS", "FOI_ENERGY_HYDROGEN"]),
    ("Klimaschutz",           ["FOI_ENVIRONMENT_CLIMATE", "FOI_ENVIRONMENT|FOI_ENVIRONMENT_CLIMATE"]),
    ("EU-Binnenmarkt & EU-Gesetzgebung", ["FOI_EU_DOMESTIC_MARKET", "FOI_EU_LAWS",
                                          "FOI_EUROPEAN_UNION|FOI_EU_DOMESTIC_MARKET",
                                          "FOI_EUROPEAN_UNION|FOI_EU_LAWS"]),
    ("Bundestag",             ["FOI_BUNDESTAG"]),
    ("Wettbewerbsrecht",      ["FOI_ECONOMY_COMPETITION_LAW", "FOI_ECONOMY|FOI_ECONOMY_COMPETITION_LAW"]),
    ("Politisches Leben, Parteien", ["FOI_POLITICAL_PARTIES"]),
    ("Sonstige",              ["FOI_OTHER"]),
]


def load_data():
    """Lädt die gespeicherten Daten."""
    path = Path("docs/data.json")
    if not path.exists():
        raise FileNotFoundError("docs/data.json nicht gefunden – Skript fetch_and_build.py zuerst ausführen")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_week_statements(statements):
    """Gibt nur Einträge der letzten 7 Tage zurück."""
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    return [
        s for s in statements
        if (s.get("upload_date") or s.get("sending_date") or "0000-00-00") >= cutoff
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
    """Ordnet einen Eintrag dem ersten passenden Thema zu."""
    field_codes = {f["code"] for f in stmt.get("fields", [])}
    for theme_name, codes in THEME_ORDER:
        if field_codes & set(codes):
            return theme_name
    return "Sonstige"


def render_entry_html(stmt):
    """Rendert einen Eintrag als HTML für die Mail."""
    title = stmt["regulatory_project_title"]
    org = stmt["org_name"]
    sending = format_date_de(stmt.get("sending_date"))
    upload = format_date_de(stmt.get("upload_date"))
    summary = stmt.get("summary") or ""
    if len(summary) > 300:
        summary = summary[:300] + "..."
    recipients = stmt.get("recipients", [])
    pdf_url = stmt.get("pdf_url", "")
    pdf_pages = stmt.get("pdf_pages", 0)

    sn = stmt.get("statement_number", "")
    rn = stmt.get("register_number", "")
    base = "https://www.lobbyregister.bundestag.de/inhalte-der-interessenvertretung/stellungnahmengutachtensuche"
    stmt_url = f"{base}/{sn}/{rn}" if sn and rn else base

    badges = "".join(
        f'<span style="display:inline-block;font-size:9px;padding:1px 5px;margin:1px 2px 1px 0;'
        f'background:#dbeafe;color:#1e3a8a;border:1px solid #bfdbfe;font-weight:600">{r}</span>'
        for r in recipients
    )

    pdf_link = (
        f'<a href="{pdf_url}" style="color:#004B87;text-decoration:none">'
        f'↗ PDF ({pdf_pages} S.)</a>'
        if pdf_url else "Kein PDF"
    )

    return f"""
    <div style="border:1px solid #d0d8e4;margin-bottom:8px;overflow:hidden">
      <div style="background:#eef3f9;padding:8px 12px;font-size:13px;font-weight:700;color:#003366">{title}</div>
      <div style="padding:6px 12px;font-size:11px;color:#555;border-top:1px solid #e0e8f0;display:flex;flex-wrap:wrap;gap:0">
        <div style="padding:3px 14px 3px 0">
          <span style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#888;display:block;margin-bottom:1px">Von</span>
          {org}
        </div>
        <div style="padding:3px 14px 3px 0">
          <span style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#888;display:block;margin-bottom:1px">Stellungnahme</span>
          {sending}
        </div>
        <div style="padding:3px 14px 3px 0">
          <span style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#888;display:block;margin-bottom:1px">Hochgeladen</span>
          {upload}
        </div>
        <div style="padding:3px 0">
          <span style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#888;display:block;margin-bottom:1px">Adressaten</span>
          {badges}
        </div>
      </div>
      {'<div style="padding:7px 12px;font-size:12px;color:#333;border-top:1px solid #e0e8f0;line-height:1.55">' + summary + '</div>' if summary else ''}
      <div style="padding:5px 12px;font-size:11px;border-top:1px solid #e0e8f0;background:#f9fbfd">
        <a href="{stmt_url}" style="color:#004B87;text-decoration:none;margin-right:16px">↗ Registereintrag ({sn})</a>
        {pdf_link}
      </div>
    </div>"""


def build_email_html(statements, generated_at, gemini_stats=None):
    """Baut den vollständigen HTML-Mail-Body."""
    week_stmts = get_week_statements(statements)
    total = len(week_stmts)

    # Gemini-Warnhinweis prüfen
    gemini_warning = ""
    if gemini_stats:
        if gemini_stats.get("skipped") or gemini_stats.get("gemini_failed"):
            gemini_warning = """
    <div style="background:#fff8e1;border:1px solid #ffe082;border-left:3px solid #f9a825;
                padding:10px 16px;margin-bottom:16px;font-size:12px;color:#5d4037;line-height:1.5">
      <strong>Hinweis:</strong> Die KI-gestützte Relevanzfilterung und Zusammenfassung
      konnte bei dieser Aktualisierung nicht durchgeführt werden.
      Alle Einträge werden ungefiltert mit Originaltexten angezeigt.
    </div>"""

    # Nach Thema gruppieren
    by_theme = defaultdict(list)
    for stmt in week_stmts:
        theme = assign_theme(stmt)
        by_theme[theme].append(stmt)

    # Themenblöcke rendern (nur Themen mit Einträgen, in definierter Reihenfolge)
    theme_blocks = ""
    for theme_name, _ in THEME_ORDER:
        stmts = by_theme.get(theme_name, [])
        if not stmts:
            continue
        entries_html = "".join(render_entry_html(s) for s in stmts)
        theme_blocks += f"""
        <div style="margin-bottom:20px">
          <div style="margin-bottom:10px">
            <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
                         color:#fff;background:#004B87;padding:4px 10px;display:inline-block">
              {theme_name}
            </span>
          </div>
          {entries_html}
        </div>"""

    # KW berechnen
    today = date.today()
    kw = today.isocalendar()[1]
    year = today.year
    week_start = (today - timedelta(days=today.weekday() + 7)).strftime("%d.%m.")
    week_end = (today - timedelta(days=today.weekday() + 1)).strftime("%d.%m.%Y")

    no_entries_msg = ""
    if total == 0:
        no_entries_msg = """
        <div style="padding:20px;text-align:center;color:#888;font-size:13px">
          In der vergangenen Woche wurden keine neuen Einträge mit den gesuchten Kriterien hochgeladen.
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,Helvetica,sans-serif">
<div style="max-width:700px;margin:20px auto">
  <!-- Header -->
  <div style="background:#004B87;padding:16px 28px;display:flex;align-items:center;gap:16px">
    <img src="https://bmwe-iiia4.github.io/lobbyregister-monitor/Logo_BMWE.png"
         alt="BMWE" style="height:40px;width:auto;filter:brightness(0) invert(1)">
    <div>
      <div style="color:#fff;font-size:15px;font-weight:700;margin-bottom:2px">
        Lobbyregister-Monitor · KW {kw}/{year}
      </div>
      <div style="color:#a8c8e8;font-size:11px">
        Neue Stellungnahmen &amp; Gutachten · {week_start}–{week_end} · BMWE / Bundestag
      </div>
    </div>
  </div>
  <!-- Meta -->
  <div style="background:#f0f4f8;padding:10px 28px;border-bottom:1px solid #d0d8e4;
              display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">
    <div style="font-size:12px;color:#555">{total} neue Eintrag{"" if total == 1 else "e"} diese Woche</div>
    <div><a href="{SITE_URL}" style="font-size:12px;color:#004B87;text-decoration:none;font-weight:600">
      → Vollständige Übersicht öffnen
    </a></div>
  </div>
  <!-- Body -->
  <div style="padding:20px 28px;background:#fff">
    {gemini_warning}
    <p style="font-size:13px;color:#444;margin-bottom:18px;line-height:1.6;
               padding-bottom:14px;border-bottom:1px solid #e8edf3">
      Neue Stellungnahmen und Gutachten im Lobbyregister mit Adressat BMWE oder Bundestag
      aus den beobachteten Themenfeldern (Energie &amp; Wasserstoff, Klimaschutz, EU-Binnenmarkt,
      EU-Gesetzgebung, Bundestag, Wettbewerbsrecht, Politisches Leben/Parteien, Sonstige).
    </p>
    {no_entries_msg}
    {theme_blocks}
    <hr style="border:none;border-top:1px solid #e0e8f0;margin:16px 0">
    <p style="font-size:12px;color:#555;text-align:center">
      <a href="{SITE_URL}" style="color:#004B87;text-decoration:none;font-weight:600">
        → Alle Einträge und Filteroptionen auf der Übersichtsseite
      </a>
    </p>
  </div>
  <!-- Footer -->
  <div style="padding:14px 28px;background:#f0f4f8;border-top:1px solid #d0d8e4;
              font-size:11px;color:#777;line-height:1.6">
    Diese Mail wird automatisch jeden Montag generiert und verschickt.
    Alle Daten stammen direkt aus dem
    <a href="https://www.lobbyregister.bundestag.de" style="color:#004B87;text-decoration:none">
      Lobbyregister des Deutschen Bundestages</a>.<br>
    Weiterleitungen und Abonnement-Änderungen: bitte an
    <a href="mailto:martin.jahn@bmwe.bund.de" style="color:#004B87;text-decoration:none">
      Martin Jahn, IIIA4</a> wenden.<br>
    Bei technischen Problemen:
    <a href="{SITE_URL}/wartung" style="color:#004B87;text-decoration:none">
      Wartungsdokumentation</a>
    <div style="font-size:10px;color:#999;margin-top:6px">
      Bundesministerium für Wirtschaft und Energie (BMWE) · www.bundeswirtschaftsministerium.de
    </div>
  </div>
</div>
</body></html>"""
    return html, total


def send_email(html_body, total):
    """Versendet die Mail über Resend."""
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
    gemini_stats = data.get("gemini_stats")

    html_body, total = build_email_html(statements, generated_at, gemini_stats)
    send_email(html_body, total)
    print(f"=== Fertig ({total} Einträge in Mail) ===")


if __name__ == "__main__":
    main()

"""
send_email.py
=============
Versendet die wöchentliche Übersichts-Mail mit neuen Stellungnahmen.
Layout: tabellenbasiert (robust für Outlook/Exchange-Mailfilter).

Zusammengehoerige Stellungnahmen (gleiches Regelungsvorhaben, gleiche
Institution, gleiche Themenfelder, gleiches Hochladedatum) werden zu einem
gemeinsamen Block zusammengefasst. Sortierung innerhalb eines Tages erfolgt
alphabetisch nach Regelungsvorhaben.
"""

import json
import os
import re
import requests
from datetime import timedelta, date
from collections import defaultdict

# ── Konfiguration ──────────────────────────────────────────────────────────────

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "")
SITE_URL = os.environ.get("SITE_URL", "https://lobbyregister-bot.de")

# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def format_date_de(iso_date):
    if not iso_date:
        return "–"
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return iso_date


def upload_delay_style(sending_raw, upload_raw):
    """Gibt (css_farbe, tage) zurück: grün ≤7 Tage, gelb/orange ≤30, rot >30.
    Der Verzug bezieht sich immer auf das Hochladedatum (Abstand zum
    Einreichungsdatum beim Empfaenger)."""
    if not sending_raw or not upload_raw:
        return None, 0
    try:
        d1 = date.fromisoformat(sending_raw)
        d2 = date.fromisoformat(upload_raw)
        diff = (d2 - d1).days
        if diff <= 0:
            return None, diff
        elif diff <= 7:
            return "#16a34a", diff
        elif diff <= 30:
            return "#d97706", diff
        else:
            return "#dc2626", diff
    except Exception:
        return None, 0


# ── Gruppierung ────────────────────────────────────────────────────────────────

def group_statements(statements):
    """Gruppiert Stellungnahmen mit identischem Regelungsvorhaben, identischer
    Institution, identischen Themenfeldern und identischem Hochladedatum zu
    einem gemeinsamen Block."""
    groups = {}
    order = []
    for stmt in statements:
        field_key = tuple(sorted(f["code"] for f in stmt.get("fields", [])))
        key = (
            stmt.get("regulatory_project_title", ""),
            stmt.get("org_name", ""),
            field_key,
            stmt.get("upload_date", ""),
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(stmt)

    result = []
    for key in order:
        members = groups[key]
        if len(members) == 1:
            result.append(members[0])
        else:
            members_sorted = sorted(members, key=lambda m: m.get("sending_date") or "")
            result.append({"_group": members_sorted})
    return result


def group_sort_key(item):
    if isinstance(item, dict) and "_group" in item:
        return item["_group"][0].get("regulatory_project_title", "").lower()
    return item.get("regulatory_project_title", "").lower()


# ── Karten-Rendering ───────────────────────────────────────────────────────────

# Wiederverwendete Style-Strings.
# Spaltenbreite: width="1" + white-space:nowrap ist der klassische E-Mail-Trick
# fuer "so schmal wie inhaltlich noetig" (Outlook/Exchange-kompatibel), OHNE
# eine harte Minimalbreite zu erzwingen – Innenabstand (padding) bleibt wie
# bisher erhalten, nur die Spaltenbreite wird dynamisch statt fix.
LABEL_GRAY = ('font-size:13px;color:#94a3b8;font-weight:bold;text-transform:uppercase;'
              'white-space:nowrap;vertical-align:top;padding:2px 10px;background:#fafafa;line-height:1')
VALUE_GRAY = 'font-size:14px;padding:2px 10px;background:#fafafa;vertical-align:top;line-height:1'
LABEL_WHITE = ('font-size:13px;color:#94a3b8;font-weight:bold;text-transform:uppercase;'
               'white-space:nowrap;vertical-align:top;padding:2px 10px;background:#fff;'
               'border-top:1px solid #f1f5f9;line-height:1')
VALUE_WHITE = ('font-size:14px;padding:2px 10px;background:#fff;vertical-align:top;'
               'border-top:1px solid #f1f5f9;line-height:1')


def render_entry_card(stmt):
    """Rendert eine einzelne Stellungnahme als HTML-Tabelle (email-robust)."""
    title = stmt["regulatory_project_title"]
    org = stmt["org_name"]
    org_url = stmt.get("org_url", "")
    sending = format_date_de(stmt.get("sending_date"))
    upload_raw = stmt.get("upload_date")
    sending_raw = stmt.get("sending_date")

    color, diff = upload_delay_style(sending_raw, upload_raw)
    delay_str = f" (+{diff} Tage)" if diff > 0 else ""
    if color:
        upload_display = f'<span style="color:{color}">{format_date_de(upload_raw)}{delay_str}</span>'
    else:
        upload_display = f'{format_date_de(upload_raw)}{delay_str}'

    summary = stmt.get("summary", "") or "Keine Beschreibung verfügbar."
    summary = summary.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    summary = summary.replace('&lt;b&gt;', '<b style="background:#fef9c3;padding:1px 2px">')
    summary = summary.replace('&lt;/b&gt;', '</b>')

    has_ai_summary = bool(stmt.get("summary") and len(stmt.get("summary", "")) > 50)
    ai_warning = "" if has_ai_summary else (
        '<tr><td colspan="2" style="padding:5px 12px;font-size:11px;'
        'background:#fff3cd;color:#856404;border-bottom:1px solid #f1f5f9">'
        '⚠️ KI-Prüfung steht noch aus</td></tr>'
    )

    org_html = f'<a href="{org_url}" style="color:#004B87;text-decoration:none">{org}</a>' if org_url else org

    recip_badges = " ".join(
        f'<span style="background:#eff6ff;color:#1e40af;font-size:13px;font-weight:bold;'
        f'padding:1px 7px;border-radius:10px;border:1px solid #bfdbfe">{r}</span>'
        for r in stmt.get("recipients", [])
    )

    field_tags = " ".join(
        f'<span style="background:#f1f5f9;color:#475569;font-size:13px;'
        f'padding:1px 7px;border-radius:10px;border:1px solid #e2e8f0">{f["label"]}</span>'
        for f in stmt.get("fields", [])
    )

    sg_label = f' ({stmt.get("sg_number", "")})' if stmt.get("sg_number") else ""
    pdf_pages = stmt.get("pdf_pages", 0)
    stmt_link = (
        f'<a href="{stmt.get("statement_url","")}" style="color:#004B87;text-decoration:none;margin-right:16px">'
        f'↗ Stellungnahme im Lobbyregister{sg_label}</a>'
    ) if stmt.get("statement_url") else ''
    pdf_link = (
        f'<a href="{stmt.get("pdf_url","")}" style="color:#004B87;text-decoration:none">'
        f'↗ PDF ({pdf_pages} Seiten)</a>'
    ) if stmt.get("pdf_url") else ''

    return f'''
<table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;margin-bottom:10px;border-collapse:collapse">
  <tr>
    <td colspan="2" style="padding:6px 12px;font-size:15px;font-weight:bold;color:#0f172a;border-left:4px solid #004B87;border-bottom:1px solid #f1f5f9">{title}</td>
  </tr>
  {ai_warning}
  <tr>
    <td width="1" style="{LABEL_GRAY};padding-top:3px">Bereitgestellt von</td>
    <td style="{VALUE_GRAY};padding-top:3px">{org_html}</td>
  </tr>
  <tr>
    <td style="{LABEL_GRAY}">Datum Stellungnahme</td>
    <td style="{VALUE_GRAY}">{sending}</td>
  </tr>
  <tr>
    <td style="{LABEL_GRAY};padding-bottom:3px">Hochgeladen am</td>
    <td style="{VALUE_GRAY};padding-bottom:3px">{upload_display}</td>
  </tr>
  <tr>
    <td style="{LABEL_WHITE};padding-top:3px">Adressaten</td>
    <td style="{VALUE_WHITE};padding-top:3px">{recip_badges}</td>
  </tr>
  <tr>
    <td style="{LABEL_WHITE};padding-bottom:3px">Themenfelder</td>
    <td style="{VALUE_WHITE};padding-bottom:3px">{field_tags}</td>
  </tr>
  <tr>
    <td colspan="2" style="padding:6px 12px;font-size:14px;color:#334155;line-height:1.4;border-top:1px solid #f1f5f9">
      <span style="font-size:11px;font-weight:bold;color:#94a3b8;text-transform:uppercase;display:block;margin-bottom:2px;line-height:1">Inhalt</span>
      {summary}
    </td>
  </tr>
  <tr>
    <td colspan="2" style="padding:5px 12px;font-size:13px;background:#f8fafc;border-top:1px solid #f1f5f9">
      {stmt_link}{pdf_link}
    </td>
  </tr>
</table>'''


def render_group_card(members):
    """Rendert mehrere zusammengehoerige Stellungnahmen als einen gemeinsamen
    Block (Tabelle). Identische Felder einzeilig, variierende Felder
    nummeriert (1, 2, 3, ...).

    Der Verzug (+X Tage) haengt an "Hochgeladen am": das Datum ist innerhalb
    einer Gruppe identisch, die Verzugsanzeige zum jeweiligen sending_date
    kann aber variieren und wird deshalb dort nummeriert, nicht bei
    "Datum Stellungnahme".
    """
    first = members[0]
    title = first["regulatory_project_title"]
    org = first["org_name"]
    org_url = first.get("org_url", "")
    org_html = f'<a href="{org_url}" style="color:#004B87;text-decoration:none">{org}</a>' if org_url else org
    upload_raw = first.get("upload_date")

    sending_vals = [format_date_de(m.get("sending_date")) for m in members]
    sending_display = sending_vals[0] if len(set(sending_vals)) == 1 else "<br>".join(
        f"{i+1}) {v}" for i, v in enumerate(sending_vals)
    )

    upload_vals = []
    for m in members:
        color, diff = upload_delay_style(m.get("sending_date"), upload_raw)
        delay_str = f" (+{diff} Tage)" if diff > 0 else ""
        v = (f'<span style="color:{color}">{format_date_de(upload_raw)}{delay_str}</span>'
             if color else f'{format_date_de(upload_raw)}{delay_str}')
        upload_vals.append(v)
    upload_display = upload_vals[0] if len(set(upload_vals)) == 1 else "<br>".join(
        f"{i+1}) {v}" for i, v in enumerate(upload_vals)
    )

    recip_lists = [tuple(m.get("recipients", [])) for m in members]
    if len(set(recip_lists)) == 1:
        recip_badges = " ".join(
            f'<span style="background:#eff6ff;color:#1e40af;font-size:13px;font-weight:bold;'
            f'padding:1px 7px;border-radius:10px;border:1px solid #bfdbfe">{r}</span>'
            for r in members[0].get("recipients", [])
        )
    else:
        rows = []
        for i, m in enumerate(members):
            badges = " ".join(
                f'<span style="background:#eff6ff;color:#1e40af;font-size:13px;font-weight:bold;'
                f'padding:1px 7px;border-radius:10px;border:1px solid #bfdbfe">{r}</span>'
                for r in m.get("recipients", [])
            )
            rows.append(f'<div style="margin-bottom:2px">{i+1}) {badges}</div>')
        recip_badges = "".join(rows)

    field_tags = " ".join(
        f'<span style="background:#f1f5f9;color:#475569;font-size:13px;'
        f'padding:1px 7px;border-radius:10px;border:1px solid #e2e8f0">{f["label"]}</span>'
        for f in first.get("fields", [])
    )

    content_rows = []
    link_rows = []
    any_pending = False
    for i, m in enumerate(members):
        summary = m.get("summary", "") or "Keine Beschreibung verfügbar."
        summary = summary.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        summary = summary.replace('&lt;b&gt;', '<b style="background:#fef9c3;padding:1px 2px">')
        summary = summary.replace('&lt;/b&gt;', '</b>')
        if m.get("gemini_status") == "pending":
            any_pending = True
        content_rows.append(
            f'<div style="margin-bottom:6px"><strong>{i+1})</strong> {summary}</div>'
        )
        sg_label = f' ({m.get("sg_number", "")})' if m.get("sg_number") else ""
        pdf_pages = m.get("pdf_pages", 0)
        stmt_link = (
            f'<a href="{m.get("statement_url","")}" style="color:#004B87;text-decoration:none;margin-right:16px">'
            f'↗ Stellungnahme im Lobbyregister{sg_label}</a>'
        ) if m.get("statement_url") else ''
        pdf_link = (
            f'<a href="{m.get("pdf_url","")}" style="color:#004B87;text-decoration:none">'
            f'↗ PDF ({pdf_pages} Seiten)</a>'
        ) if m.get("pdf_url") else ''
        link_rows.append(f'<div style="margin-bottom:2px">{i+1}) {stmt_link}{pdf_link}</div>')

    content_html = "".join(content_rows)
    links_html = "".join(link_rows)

    ai_warning = ""
    if any_pending:
        ai_warning = (
            '<tr><td colspan="2" style="padding:5px 12px;font-size:11px;'
            'background:#fff3cd;color:#856404;border-bottom:1px solid #f1f5f9">'
            '⚠️ Für mindestens eine Stellungnahme in diesem Block steht die KI-Prüfung noch aus</td></tr>'
        )

    return f'''
<table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;margin-bottom:10px;border-collapse:collapse">
  <tr>
    <td colspan="2" style="padding:6px 12px;font-size:15px;font-weight:bold;color:#0f172a;border-left:4px solid #004B87;border-bottom:1px solid #f1f5f9">
      {title} <span style="font-size:12px;font-weight:normal;color:#64748b">({len(members)} zusammengehörige Stellungnahmen)</span>
    </td>
  </tr>
  {ai_warning}
  <tr>
    <td width="1" style="{LABEL_GRAY};padding-top:3px">Bereitgestellt von</td>
    <td style="{VALUE_GRAY};padding-top:3px">{org_html}</td>
  </tr>
  <tr>
    <td style="{LABEL_GRAY}">Datum Stellungnahme</td>
    <td style="{VALUE_GRAY}">{sending_display}</td>
  </tr>
  <tr>
    <td style="{LABEL_GRAY};padding-bottom:3px">Hochgeladen am</td>
    <td style="{VALUE_GRAY};padding-bottom:3px">{upload_display}</td>
  </tr>
  <tr>
    <td style="{LABEL_WHITE};padding-top:3px">Adressaten</td>
    <td style="{VALUE_WHITE};padding-top:3px">{recip_badges}</td>
  </tr>
  <tr>
    <td style="{LABEL_WHITE};padding-bottom:3px">Themenfelder</td>
    <td style="{VALUE_WHITE};padding-bottom:3px">{field_tags}</td>
  </tr>
  <tr>
    <td colspan="2" style="padding:6px 12px;font-size:14px;color:#334155;line-height:1.4;border-top:1px solid #f1f5f9">
      <span style="font-size:11px;font-weight:bold;color:#94a3b8;text-transform:uppercase;display:block;margin-bottom:2px;line-height:1">Inhalt</span>
      {content_html}
    </td>
  </tr>
  <tr>
    <td colspan="2" style="padding:5px 12px;font-size:13px;background:#f8fafc;border-top:1px solid #f1f5f9">
      {links_html}
    </td>
  </tr>
</table>'''


def render_card_or_group(item):
    if isinstance(item, dict) and "_group" in item:
        return render_group_card(item["_group"])
    return render_entry_card(item)


# ── E-Mail-Aufbau ──────────────────────────────────────────────────────────────

def build_email_html(statements, week_start, week_end):
    """Baut die komplette E-Mail als HTML."""

    # Nach upload_date gruppieren
    by_date = defaultdict(list)
    for stmt in statements:
        key = stmt.get("upload_date") or "unbekannt"
        by_date[key].append(stmt)

    day_sections = ""
    for iso_date in sorted(by_date.keys(), reverse=True):
        grouped = group_statements(by_date[iso_date])
        grouped_sorted = sorted(grouped, key=group_sort_key)

        try:
            d = date.fromisoformat(iso_date)
            days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
            months = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
                      "Juli", "August", "September", "Oktober", "November", "Dezember"]
            day_label = f"{days[d.weekday()]}, {d.day}. {months[d.month]} {d.year}"
        except Exception:
            day_label = iso_date

        cards = "".join(render_card_or_group(item) for item in grouped_sorted)

        day_sections += f'''
<div style="margin-bottom:16px">
  <div style="font-size:11px;font-weight:bold;color:#64748b;text-transform:uppercase;
              letter-spacing:0.05em;padding:8px 0 6px;border-bottom:2px solid #e2e8f0;
              margin-bottom:10px">{day_label}</div>
  {cards}
</div>'''

    week_label = f"{week_start.strftime('%d.%m.')} – {week_end.strftime('%d.%m.%Y')}"
    kw = week_start.isocalendar()[1]

    return f'''<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="font-family:Arial,sans-serif;font-size:13px;color:#222;margin:0;padding:20px;background:#f0f0f0">
<div style="max-width:700px;margin:0 auto;background:#fff">

  <div style="background:#004B87;padding:14px 20px">
    <div style="color:#fff;font-size:15px;font-weight:bold">Lobbyregister-Monitor – Wochenübersicht der KW {kw}</div>
    <div style="color:#a8c8e8;font-size:11px;margin-top:3px">Neue Stellungnahmen der letzten 7 Tage ({week_label})</div>
  </div>

  <div style="background:#e3f2fd;border-left:3px solid #1976d2;padding:8px 16px;font-size:11px;color:#0d47a1">
    <strong>Hinweis:</strong> Automatisch generierte Übersicht. Für alle Details:
    <a href="{SITE_URL}" style="color:#004B87;font-weight:bold">vollständige Webseite</a>.
  </div>

  <div style="padding:16px 20px">
    {day_sections}
  </div>

  <div style="background:#f8fafc;padding:12px 20px;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b">
    <a href="{SITE_URL}" style="color:#004B87;text-decoration:none">Zur Webseite</a> ·
    <a href="{SITE_URL}/hilfe.html" style="color:#004B87;text-decoration:none">Nutzungsanleitung</a> ·
    <a href="{SITE_URL}/impressum.html" style="color:#004B87;text-decoration:none">Impressum</a><br>
    <span style="color:#94a3b8">Daten: <a href="https://www.lobbyregister.bundestag.de" style="color:#94a3b8">Lobbyregister des Deutschen Bundestages</a></span>
  </div>

</div>
</body>
</html>'''


# ── Hauptprogramm ──────────────────────────────────────────────────────────────

def main():
    print("=== Lobbyregister Monitor – E-Mail-Versand ===")

    try:
        with open("docs/data.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("FEHLER: docs/data.json nicht gefunden")
        return

    statements = data.get("statements", [])

    today = date.today()
    week_start = today - timedelta(days=7)
    week_end = today

    recent = []
    for stmt in statements:
        # KI-aussortierte Statements werden in data.json behalten (zur Vermeidung
        # des Cache-Kreislaufs), aber NICHT in der Wochenmail dargestellt.
        if stmt.get("gemini_status") == "filtered":
            continue
        upload_str = stmt.get("upload_date")
        if not upload_str:
            continue
        try:
            if date.fromisoformat(upload_str) >= week_start:
                recent.append(stmt)
        except ValueError:
            continue

    if not recent:
        print("Keine neuen Einträge in den letzten 7 Tagen – keine Mail versendet.")
        return

    print(f"{len(recent)} neue Einträge ({week_start.strftime('%d.%m.')} – {week_end.strftime('%d.%m.%Y')})")

    html = build_email_html(recent, week_start, week_end)

    if not RESEND_API_KEY or not EMAIL_RECIPIENT:
        print("WARNUNG: RESEND_API_KEY oder EMAIL_RECIPIENT fehlt")
        with open("/tmp/email_preview.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Vorschau gespeichert: /tmp/email_preview.html")
        return

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": "Lobbyregister-Monitor <update@lobbyregister-bot.de>",
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

"""
send_email.py
=============
Versendet die wöchentliche Übersichts-Mail mit neuen Stellungnahmen.
Layout: tabellenbasiert (robust für Outlook/Exchange-Mailfilter). Am Anfang
eine Übersichtstabelle (Institution, Regelungsvorhaben) mit Sprungmarken zu
den Karten; ab 20 Zeilen nach Tagen gegliedert. Karten ohne Label-Spalte:
Metadaten stehen als Zeilen unter dem Titel.

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


# ── Kurznamen der Institutionen (Übersichtstabelle) ────────────────────────────

# Feste Kürzel für häufige Einreicher, deren Name kein Kürzel enthält.
ORG_SHORT_OVERRIDES = {
    "Bundesverband Neue Energiewirtschaft": "bne",
    "Bundesverband WindEnergie": "BWE",
    "EnBW Energie Baden-Württemberg": "EnBW",
    "50Hertz Transmission": "50Hertz",
    "Bayerische Motoren Werke": "BMW",
    "Verband der Automobilindustrie": "VDA",
    "Verband kommunaler Unternehmen": "VKU",
    "Bundesverband der Deutschen Industrie": "BDI",
    "Deutscher Industrie- und Handelskammertag": "DIHK",
}
LEGAL_FORMS = re.compile(
    r"\s*(,|\b)(GmbH\s*&\s*Co\.?\s*KG(aA)?|GmbH|gGmbH|AG|SE|KGaA|KG|mbH|"
    r"Aktiengesellschaft|e\.\s?V\.?|eG|Holding)\s*$", re.IGNORECASE)


def short_org_name(name):
    """Kurzname für die Übersicht: führendes Kürzel ("BDEW …"), Kürzel in
    Klammern ("… (BEE)"), feste Zuordnung oder Name ohne Rechtsform."""
    name = (name or "").strip()
    words = name.split()
    if len(words) > 1 and re.fullmatch(r"[A-ZÄÖÜ0-9][A-ZÄÖÜ0-9&\-\.]{1,11}", words[0]) \
            and sum(c.isupper() for c in words[0]) >= 2:
        return words[0]
    m = re.search(r"\(([A-Za-zÄÖÜäöü0-9][^()]{1,14})\)", name)
    if m and (any(c.isupper() for c in m.group(1)) or (" " not in m.group(1) and len(m.group(1)) <= 8)):
        return re.sub(r"\s*e\.\s?V\.?$", "", m.group(1)).strip()
    m = re.search(r"\s-\s*([A-Za-z0-9]{2,10})\s*-\s*$", name)          # "… - en2x -"
    if m:
        return m.group(1)
    base = name.split(" - ")[0] if " - " in name else name               # "Name - Untertitel"
    for _ in range(3):
        base = LEGAL_FORMS.sub("", base).strip(" ,-")
    for full, short in ORG_SHORT_OVERRIDES.items():
        if base.startswith(full):
            return short
    return base if len(base) <= 30 else base[:28].rstrip() + "…"


# ── Karten-Rendering (Layout: Metadaten als Zeilen, ohne Label-Spalte) ─────────

BADGE_RECIP = ('display:inline-block;background:#eff6ff;color:#1e40af;font-size:12px;font-weight:bold;'
               'padding:1px 7px;border-radius:10px;border:1px solid #bfdbfe;margin:1px 2px 1px 0')
BADGE_FIELD = ('display:inline-block;background:#fff;color:#475569;font-size:12px;'
               'padding:1px 7px;border-radius:10px;border:1px solid #e2e8f0;margin:1px 2px 1px 0')
LINK = 'color:#004B87;text-decoration:none'
TOP_LINK = ('<a href="#uebersicht" style="color:#94a3b8;text-decoration:none;font-size:11px">'
            '↑ Übersicht</a>')

QUELLE_HINWEIS = {
    "pdf_scan": "Grundlage: PDF-Scan (von der KI gelesen)",
    "register": "Grundlage: nur Registerangabe zum Regelungsvorhaben – Dokument nicht auswertbar",
}


def esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def anchor_id(stmt):
    key = stmt.get("sg_number") or f'{stmt.get("register_number", "")}-{stmt.get("sending_date", "")}'
    return "sg-" + re.sub(r"[^A-Za-z0-9\-]", "", key)


def summary_html(stmt):
    summary = esc(stmt.get("summary", "") or "Keine Beschreibung verfügbar.")
    summary = summary.replace('&lt;b&gt;', '<b style="background:#fef9c3;padding:1px 2px">')
    summary = summary.replace('&lt;/b&gt;', '</b>')
    note = QUELLE_HINWEIS.get(stmt.get("summary_quelle", ""))
    if note:
        summary += f'<br><span style="font-size:11px;color:#94a3b8">{note}</span>'
    return summary


def abweichung_row(stmt, prefix=""):
    if not (stmt.get("abweichung") and stmt.get("abweichung_hinweis")):
        return ""
    return ('<tr><td style="padding:7px 12px;font-size:13px;background:#fff7ed;'
            f'border-top:1px solid #fed7aa;color:#9a3412">{prefix}<b>⚠ Dokument weicht vom angegebenen '
            f'Regelungsvorhaben ab:</b> {esc(stmt["abweichung_hinweis"])}</td></tr>')


def delay_html(sending_raw, upload_raw):
    color, diff = upload_delay_style(sending_raw, upload_raw)
    txt = format_date_de(upload_raw) + (f' (+{diff} Tage)' if diff > 0 else '')
    return f'<span style="color:{color}">{txt}</span>' if color else txt


def org_link(stmt):
    org = esc(stmt["org_name"])
    url = stmt.get("org_url", "")
    return f'<a href="{url}" style="{LINK};font-weight:bold">{org}</a>' if url else f'<b>{org}</b>'


def badges(items, style):
    return " ".join(f'<span style="{style}">{esc(i)}</span>' for i in items)


def links_html(stmt):
    sg_label = f' ({stmt.get("sg_number", "")})' if stmt.get("sg_number") else ""
    parts = []
    if stmt.get("statement_url"):
        parts.append(f'<a href="{stmt["statement_url"]}" style="{LINK}">↗ Stellungnahme im Lobbyregister{sg_label}</a>')
    if stmt.get("pdf_url"):
        parts.append(f'<a href="{stmt["pdf_url"]}" style="{LINK}">↗ PDF ({stmt.get("pdf_pages", 0)} Seiten)</a>')
    return " &nbsp; ".join(parts)


def card_frame(anchor, title_html, rows_html):
    return (
        f'\n<a name="{anchor}" id="{anchor}"></a>\n'
        '<table width="100%" cellpadding="0" cellspacing="0" '
        'style="border:1px solid #e2e8f0;margin-bottom:10px;border-collapse:collapse">\n'
        '  <tr><td style="padding:6px 12px;font-size:15px;font-weight:bold;color:#0f172a;'
        f'border-left:4px solid #004B87;border-bottom:1px solid #f1f5f9">{title_html}</td></tr>\n'
        f'  {rows_html}\n</table>'
    )


def meta_rows(org_html, dates_html, recips_html, fields):
    return (
        f'<tr><td style="padding:6px 12px 2px;font-size:14px;background:#fafafa">{org_html} '
        f'<span style="color:#64748b;font-size:13px">{dates_html}</span></td></tr>'
        '<tr><td style="padding:2px 12px 6px;font-size:12px;color:#64748b;background:#fafafa">'
        f'An: {recips_html} &nbsp; Themen: {badges([f["label"] for f in fields], BADGE_FIELD)}</td></tr>'
    )


def content_row(html):
    return ('<tr><td style="padding:6px 12px;font-size:14px;color:#334155;line-height:1.4;'
            f'border-top:1px solid #f1f5f9">{html}</td></tr>')


def link_row(html):
    return ('<tr><td style="padding:5px 12px;font-size:13px;background:#f8fafc;'
            f'border-top:1px solid #f1f5f9">{html}</td></tr>')


def pending_row(text):
    return ('<tr><td style="padding:5px 12px;font-size:11px;background:#fff3cd;color:#856404">'
            f'{text}</td></tr>')


def render_entry_card(stmt):
    """Rendert eine einzelne Stellungnahme als HTML-Tabelle (email-robust)."""
    pending = pending_row("⚠️ KI-Prüfung steht noch aus") if stmt.get("gemini_status") == "pending" else ""
    dates = (f'· Stellungnahme vom {format_date_de(stmt.get("sending_date"))} '
             f'· hochgeladen {delay_html(stmt.get("sending_date"), stmt.get("upload_date"))}')
    rows = (pending
            + meta_rows(org_link(stmt), dates, badges(stmt.get("recipients", []), BADGE_RECIP), stmt.get("fields", []))
            + abweichung_row(stmt)
            + content_row(summary_html(stmt))
            + link_row(f'{links_html(stmt)} &nbsp; {TOP_LINK}'))
    return card_frame(anchor_id(stmt), esc(stmt["regulatory_project_title"]), rows)


def render_group_card(members):
    """Rendert mehrere zusammengehoerige Stellungnahmen als einen gemeinsamen
    Block. Identische Angaben einmal, abweichende nummeriert (1, 2, 3, ...)."""
    first = members[0]
    upload_raw = first.get("upload_date")
    sending_vals = [format_date_de(m.get("sending_date")) for m in members]
    sending = sending_vals[0] if len(set(sending_vals)) == 1 else " / ".join(
        f"{i+1}) {v}" for i, v in enumerate(sending_vals))
    delays = [delay_html(m.get("sending_date"), upload_raw) for m in members]
    upload = delays[0] if len(set(delays)) == 1 else " / ".join(f"{i+1}) {v}" for i, v in enumerate(delays))

    recip_lists = [tuple(m.get("recipients", [])) for m in members]
    if len(set(recip_lists)) == 1:
        recips = badges(first.get("recipients", []), BADGE_RECIP)
    else:
        recips = " ".join(f'{i+1}) {badges(m.get("recipients", []), BADGE_RECIP)}' for i, m in enumerate(members))

    pending = (pending_row("⚠️ Für mindestens eine Stellungnahme in diesem Block steht die KI-Prüfung noch aus")
               if any(m.get("gemini_status") == "pending" for m in members) else "")
    warns = "".join(abweichung_row(m, prefix=f"{i+1}) ") for i, m in enumerate(members))
    content = "".join(f'<div style="margin-bottom:6px"><strong>{i+1})</strong> {summary_html(m)}</div>'
                      for i, m in enumerate(members))
    links = "".join(f'<div style="margin-bottom:2px">{i+1}) {links_html(m)}</div>' for i, m in enumerate(members))
    rows = (pending
            + meta_rows(org_link(first), f'· Stellungnahmen vom {sending} · hochgeladen {upload}',
                        recips, first.get("fields", []))
            + warns + content_row(content) + link_row(links + TOP_LINK))
    title = (f'{esc(first["regulatory_project_title"])} <span style="font-size:12px;font-weight:normal;'
             f'color:#64748b">({len(members)} zusammengehörige Stellungnahmen)</span>')
    return card_frame(anchor_id(first), title, rows)


def render_card_or_group(item):
    if isinstance(item, dict) and "_group" in item:
        return render_group_card(item["_group"])
    return render_entry_card(item)


# ── Übersichtstabelle ──────────────────────────────────────────────────────────

TOC_SPLIT_THRESHOLD = 20   # ab so vielen Zeilen wird die Übersicht nach Tagen gegliedert
TD = 'padding:4px 8px;border-top:1px solid #f1f5f9;vertical-align:top;font-size:13px'
TH = 'padding:4px 8px;font-size:11px;color:#64748b;font-weight:bold;background:#f8fafc;text-align:left'


def toc_row(item, with_date):
    members = item["_group"] if isinstance(item, dict) and "_group" in item else [item]
    first = members[0]
    count = f' <span style="color:#94a3b8">({len(members)})</span>' if len(members) > 1 else ""
    flag = (' <span style="font-size:11px;color:#b45309;font-weight:bold">⚠ Inhalt weicht ab</span>'
            if any(m.get("abweichung") and m.get("abweichung_hinweis") for m in members) else "")
    date_cell = (f'<td width="52" style="{TD};width:52px;color:#475569;white-space:nowrap">'
                 f'{format_date_de(first.get("upload_date"))[:6]}</td>') if with_date else ""
    return (f'<tr>{date_cell}<td width="120" style="{TD};width:120px">{esc(short_org_name(first["org_name"]))}</td>'
            f'<td style="{TD}"><a href="#{anchor_id(first)}" style="{LINK}">'
            f'{esc(first["regulatory_project_title"])}</a>{count}{flag}</td></tr>')


def build_toc(day_items, total_statements):
    """day_items: Liste von (Tagesbezeichnung, [Karten]) in Mail-Reihenfolge."""
    n_rows = sum(len(items) for _, items in day_items)
    if n_rows >= TOC_SPLIT_THRESHOLD:
        head = f'<tr><td style="{TH}">Institution</td><td style="{TH}">Regelungsvorhaben</td></tr>'
        body = ""
        for label, items in day_items:
            body += ('<tr><td colspan="2" style="padding:8px 8px 3px;font-size:11px;font-weight:bold;'
                     f'color:#64748b;text-transform:uppercase;letter-spacing:0.05em">{label}</td></tr>')
            body += "".join(toc_row(it, with_date=False) for it in items)
    else:
        head = (f'<tr><td style="{TH}">Datum</td><td style="{TH}">Institution</td>'
                f'<td style="{TH}">Regelungsvorhaben</td></tr>')
        body = "".join(toc_row(it, with_date=True) for _, items in day_items for it in items)
    return (
        '\n<a name="uebersicht" id="uebersicht"></a>\n'
        '<div style="font-size:11px;font-weight:bold;color:#64748b;text-transform:uppercase;'
        'letter-spacing:0.05em;padding:0 0 6px;border-bottom:2px solid #e2e8f0">'
        f'Übersicht ({total_statements} Stellungnahmen)</div>\n'
        '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-bottom:18px">\n'
        f'  {head}{body}\n</table>'
    )


# ── E-Mail-Aufbau ──────────────────────────────────────────────────────────────

DAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS_DE = ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
             "Juli", "August", "September", "Oktober", "November", "Dezember"]


def day_label_de(iso_date):
    try:
        d = date.fromisoformat(iso_date)
        return f"{DAYS_DE[d.weekday()]}, {d.day}. {MONTHS_DE[d.month]} {d.year}"
    except Exception:
        return iso_date


def build_email_html(statements, week_start, week_end):
    """Baut die komplette E-Mail als HTML."""
    by_date = defaultdict(list)
    for stmt in statements:
        by_date[stmt.get("upload_date") or "unbekannt"].append(stmt)

    day_items = []
    for iso_date in sorted(by_date.keys(), reverse=True):
        grouped = sorted(group_statements(by_date[iso_date]), key=group_sort_key)
        day_items.append((day_label_de(iso_date), grouped))

    toc = build_toc(day_items, len(statements))

    day_sections = ""
    for label, items in day_items:
        cards = "".join(render_card_or_group(item) for item in items)
        day_sections += (
            '\n<div style="margin-bottom:16px">\n'
            '  <div style="font-size:11px;font-weight:bold;color:#64748b;text-transform:uppercase;'
            'letter-spacing:0.05em;padding:8px 0 6px;border-bottom:2px solid #e2e8f0;'
            f'margin-bottom:10px">{label}</div>\n  {cards}\n</div>'
        )

    week_label = f"{week_start.strftime('%d.%m.')} – {week_end.strftime('%d.%m.%Y')}"
    kw = week_start.isocalendar()[1]

    return EMAIL_TEMPLATE.format(kw=kw, week_label=week_label, site=SITE_URL,
                                 toc=toc, day_sections=day_sections)


EMAIL_TEMPLATE = """<!DOCTYPE html>
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
    <a href="{site}" style="color:#004B87;font-weight:bold">vollständige Webseite</a>.
  </div>

  <div style="padding:16px 20px">
    {toc}
    {day_sections}
  </div>

  <div style="background:#f8fafc;padding:12px 20px;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b">
    <a href="{site}" style="color:#004B87;text-decoration:none">Zur Webseite</a> ·
    <a href="{site}/hilfe.html" style="color:#004B87;text-decoration:none">Nutzungsanleitung</a> ·
    <a href="{site}/impressum.html" style="color:#004B87;text-decoration:none">Impressum</a><br>
    <span style="color:#94a3b8">Daten: <a href="https://www.lobbyregister.bundestag.de" style="color:#94a3b8">Lobbyregister des Deutschen Bundestages</a></span>
  </div>

</div>
</body>
</html>"""


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

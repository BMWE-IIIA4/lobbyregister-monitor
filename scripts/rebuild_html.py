"""
rebuild_html.py
===============
Generiert die HTML-Seite aus docs/data.json neu.
Fügt bei Gemini-Ausfall einen Warnhinweis ein.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from fetch_and_build import generate_html


GEMINI_WARNING_HTML = """
<div style="background:#fff8e1;border:1px solid #ffe082;border-left:3px solid #f9a825;
            padding:10px 16px;margin:0;font-size:12px;color:#5d4037;line-height:1.5">
  <strong>Hinweis:</strong> Die KI-gestützte Relevanzfilterung und Zusammenfassung
  konnte bei dieser Aktualisierung nicht durchgeführt werden.
  Alle Einträge werden ungefiltert mit Originaltexten angezeigt.
</div>"""


def main():
    data_path = Path("docs/data.json")
    if not data_path.exists():
        print("FEHLER: docs/data.json nicht gefunden")
        sys.exit(1)

    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    statements = data.get("statements", [])
    generated_at = data.get("generated_at", datetime.now().isoformat())

    stats = data.get("gemini_stats")
    gemini_ok = True

    if stats:
        if stats.get("skipped"):
            print(f"Gemini übersprungen: {stats.get('reason', 'unbekannt')}")
            gemini_ok = False
        elif stats.get("gemini_failed"):
            print(f"Gemini fehlgeschlagen: {stats.get('api_errors', 0)} Fehler")
            gemini_ok = False
        else:
            print(f"Gemini OK: {stats.get('total_input', '?')} → {stats.get('total_output', '?')} Einträge, "
                  f"{stats.get('filtered_out', 0)} aussortiert, {stats.get('summaries_generated', 0)} Zusammenfassungen")

    html = generate_html(statements, generated_at)

    if not gemini_ok:
        marker = '<div class="page-desc">'
        if marker in html:
            html = html.replace(marker, GEMINI_WARNING_HTML + "\n" + marker)
            print("Gemini-Warnhinweis in HTML eingefügt.")

    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML generiert: docs/index.html ({len(statements)} Einträge)")

if __name__ == "__main__":
    main()

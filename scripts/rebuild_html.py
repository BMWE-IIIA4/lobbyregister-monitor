"""
rebuild_html.py
===============
Generiert die HTML-Seite aus docs/data.json neu.

Wird nach gemini_enrich.py aufgerufen, damit die angereicherten
Zusammenfassungen und die gefilterten Einträge korrekt dargestellt werden.

Fügt bei Gemini-Ausfall einen Warnhinweis in die Seite ein.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

# fetch_and_build.py importieren für die Render-Funktionen
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

    # Gemini-Stats anzeigen wenn vorhanden
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
            print(
                f"Gemini-Anreicherung: {stats.get('total_input', '?')} → "
                f"{stats.get('total_output', '?')} Einträge"
            )
            print(f"  Aussortiert: {stats.get('filtered_out', 0)}")
            print(f"  Zusammenfassungen: {stats.get('summaries_generated', 0)}")

    html = generate_html(statements, generated_at)

    # Bei Gemini-Ausfall: Warnhinweis nach der Meta-Bar einfügen
    if not gemini_ok:
        # Hinweis nach </div> der meta-bar und vor der page-desc einfügen
        marker = '<div class="page-desc">'
        if marker in html:
            html = html.replace(marker, GEMINI_WARNING_HTML + "\n" + marker)
            print("Gemini-Warnhinweis in HTML eingefügt.")

    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML neu generiert: docs/index.html ({len(statements)} Einträge)")


if __name__ == "__main__":
    main()

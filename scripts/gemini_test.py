"""
gemini_test.py
==============
Testet die KI-Pruefung fuer einzelne Stellungnahmen, OHNE Daten zu veraendern.

Aufruf: python scripts/gemini_test.py SG2609210037 [SG... ...]
Erwartet docs/data.json (wird im Test-Workflow aus dem data-store-Branch
geladen). Gibt pro Stellungnahme Quelle (pdf_text / pdf_scan / register) und
das vollstaendige Gemini-Ergebnis aus. Schreibt weder Cache noch data.json.
Verbraucht 1–2 Gemini-Calls pro Stellungnahme.
"""

import json
import sys

import gemini_enrich as g


def main():
    sg_numbers = [a.strip() for arg in sys.argv[1:] for a in arg.replace(",", " ").split() if a.strip()]
    if not sg_numbers:
        print("Bitte SG-Nummer(n) angeben, z.B.: python scripts/gemini_test.py SG2609210037")
        sys.exit(1)
    if not g.GEMINI_API_KEY:
        print("FEHLER: GEMINI_API_KEY fehlt.")
        sys.exit(1)

    with open(g.DATA_PATH, encoding="utf-8") as f:
        statements = json.load(f).get("statements", [])
    by_sg = {s.get("sg_number"): s for s in statements}

    failed = False
    for sg in sg_numbers:
        print(f"\n=== {sg} ===")
        stmt = by_sg.get(sg)
        if not stmt:
            print("  nicht in data.json gefunden")
            failed = True
            continue
        print(f"  Organisation: {stmt.get('org_name')}")
        print(f"  Regelungsvorhaben: {stmt.get('regulatory_project_title')}")
        print(f"  Registerbeschreibung vorhanden: {'ja' if stmt.get('register_description') else 'nein'}")
        result, quelle, n_calls = g.analyze_statement(stmt)
        print(f"  Quelle: {quelle} | API-Calls: {n_calls}")
        if isinstance(result, dict):
            print(f"  Hauptgegenstand laut KI: {result.get('hauptgegenstand', '–')} | Bezug: {result.get('bezug', '–')} | Gesetze: {result.get('gesetze', '–')}")
            print(json.dumps(g.cache_entry_from_result(result, quelle, stmt), ensure_ascii=False, indent=2))
        else:
            print(f"  Kein Ergebnis: {result}")
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

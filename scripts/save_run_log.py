"""
save_run_log.py
===============
Speichert Workflow-Run-Informationen mit erweiterten Metriken.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BERLIN_TZ = ZoneInfo("Europe/Berlin")


def main():
    log_file = Path("docs/run_history.json")

    # Vorherige Logs laden oder neu initialisieren
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, dict):
                history = {"runs": []}
            if "runs" not in history or not isinstance(history["runs"], list):
                history["runs"] = []
        except (json.JSONDecodeError, ValueError):
            history = {"runs": []}
    else:
        history = {"runs": []}

    # Aktuelle Daten laden um Metriken zu berechnen
    data_file = Path("docs/data.json")
    total_entries = 0
    new_entries = 0
    pending_entries = 0
    filtered_entries = 0

    if data_file.exists():
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            statements = data.get("statements", [])
            total_entries = len(statements)

            # NEU = heute tatsaechlich neu in die DB aufgenommen (von
            # fetch_and_build.py als 'newly_added' in data.json hinterlegt),
            # NICHT "in den letzten 24h veroeffentlicht".
            new_entries = data.get("newly_added", 0)

            for stmt in statements:
                if not stmt.get("summary") or len(stmt.get("summary", "")) < 50:
                    pending_entries += 1

            # gemini_filtered_out wird von gemini_enrich.py als Zahl in data.json gespeichert
            filtered_entries = data.get("gemini_filtered_out", 0)

        except Exception as e:
            print(f"Warnung: Daten nicht lesbar: {e}")

    # GitHub Actions Umgebungsvariablen
    run_number = os.environ.get("GITHUB_RUN_NUMBER", "?")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "unknown")
    force_email = os.environ.get("INPUT_FORCE_EMAIL", "false").lower() == "true"

    # Trigger-Typ
    if event_name == "schedule":
        trigger = "Automatisch (täglich)"
    elif event_name == "workflow_dispatch":
        trigger = "Manuell"
    else:
        trigger = event_name

    # E-Mail-Status: wurde sie heute versendet?
    # Mail geht raus wenn: (automatischer Run UND Montag) ODER (manuell UND force_email)
    now_berlin = datetime.now(BERLIN_TZ)
    is_monday = now_berlin.weekday() == 0  # 0 = Montag
    email_sent = (event_name == "schedule" and is_monday) or \
                 (event_name == "workflow_dispatch" and force_email)

    # Run-URL
    repo = os.environ.get("GITHUB_REPOSITORY", "BMWE-IIIA4/lobbyregister-monitor")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if run_id else ""

    # Neuer Log-Eintrag
    new_run = {
        "timestamp": now_berlin.isoformat(),
        "run_number": run_number,
        "run_url": run_url,
        "trigger": trigger,
        "email_sent": email_sent,
        "metrics": {
            "total_entries": total_entries,
            "new_entries": new_entries,
            "pending_ai": pending_entries,
            "filtered_out": filtered_entries
        }
    }

    history["runs"].insert(0, new_run)
    history["runs"] = history["runs"][:50]

    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"✓ Run-Log gespeichert: #{run_number} ({trigger})")
    print(f"  Einträge: {total_entries} gesamt, {new_entries} neu, "
          f"{pending_entries} ausstehend, {filtered_entries} aussortiert")
    print(f"  E-Mail versendet: {'Ja' if email_sent else 'Nein'} "
          f"(Montag: {is_monday}, Trigger: {event_name})")


if __name__ == "__main__":
    main()

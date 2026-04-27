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
                # Sicherstellen dass "runs" existiert und eine Liste ist
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
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            statements = data.get("statements", [])
            total_entries = len(statements)
            
            # Neue Einträge = die in den letzten 24h hochgeladen wurden
            from datetime import date, timedelta
            today = date.today()
            yesterday = today - timedelta(days=1)
            
            for stmt in statements:
                upload_str = stmt.get("upload_date")
                if upload_str:
                    try:
                        upload_date = date.fromisoformat(upload_str)
                        if upload_date >= yesterday:
                            new_entries += 1
                    except:
                        pass
                
                # Ausstehend = keine KI-Zusammenfassung
                if not stmt.get("summary") or len(stmt.get("summary", "")) < 50:
                    pending_entries += 1
    
    # Gemini-Cache für aussortierte Einträge
    gemini_cache_file = Path("docs/gemini_cache.json")
    if gemini_cache_file.exists():
        with open(gemini_cache_file, "r", encoding="utf-8") as f:
            cache = json.load(f)
            filtered_entries = cache.get("filtered_count", 0)
    
    # E-Mail-Status prüfen
    email_sent = os.environ.get("EMAIL_SENT", "false") == "true"
    
    # GitHub Actions Umgebungsvariablen
    run_number = os.environ.get("GITHUB_RUN_NUMBER", "?")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    workflow = os.environ.get("GITHUB_WORKFLOW", "Lobbyregister Monitor")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "unknown")
    
    # Trigger-Typ bestimmen
    if event_name == "schedule":
        trigger = "Automatisch (täglich)"
    elif event_name == "workflow_dispatch":
        trigger = "Manuell"
    else:
        trigger = event_name
    
    # Run-URL
    repo = os.environ.get("GITHUB_REPOSITORY", "BMWE-IIIA4/lobbyregister-monitor")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if run_id else ""
    
    # Neuer Log-Eintrag mit erweiterten Metriken
    now = datetime.now(BERLIN_TZ)
    new_run = {
        "timestamp": now.isoformat(),
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
    
    # Hinzufügen (neueste zuerst)
    history["runs"].insert(0, new_run)
    
    # Nur die letzten 50 Runs behalten
    history["runs"] = history["runs"][:50]
    
    # Speichern
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    
    print(f"✓ Run-Log gespeichert: #{run_number} ({trigger})")
    print(f"  Einträge: {total_entries} gesamt, {new_entries} neu, {pending_entries} ausstehend, {filtered_entries} aussortiert")
    print(f"  E-Mail versendet: {'Ja' if email_sent else 'Nein'}")


if __name__ == "__main__":
    main()

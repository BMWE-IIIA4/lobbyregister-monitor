#!/usr/bin/env python3
"""
inject_admin_hash.py
====================
Ersetzt den Platzhalter {{ADMIN_PASSWORD_HASH}} in admin.html
mit dem tatsächlichen Hash aus dem GitHub Secret.
"""

import os
import sys

def main():
    admin_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")
    
    if not admin_hash:
        print("FEHLER: ADMIN_PASSWORD_HASH Secret nicht gesetzt!")
        sys.exit(1)
    
    # admin.html einlesen
    with open("docs/admin.html", "r", encoding="utf-8") as f:
        content = f.read()
    
    # Platzhalter ersetzen
    content = content.replace("{{ADMIN_PASSWORD_HASH}}", admin_hash)
    
    # Zurückschreiben
    with open("docs/admin.html", "w", encoding="utf-8") as f:
        f.write(content)
    
    print(f"✓ Admin-Hash erfolgreich eingefügt")

if __name__ == "__main__":
    main()

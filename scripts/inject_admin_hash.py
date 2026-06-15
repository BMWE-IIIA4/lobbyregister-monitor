#!/usr/bin/env python3
"""
inject_admin_hash.py
====================
Ersetzt den Platzhalter {{ADMIN_PASSWORD_HASH}} im Admin-Panel mit dem
tatsaechlichen Hash aus dem GitHub Secret.

Hinweis zum Dateipfad: Das Admin-Panel liegt unter einem nicht erratbaren Pfad
(Security through Obscurity). Es ist kein Geheimnis im engeren Sinne, da die
verlinkten GitHub-Aktionen ohnehin GitHub-Authentifizierung erfordern – der
Pfad-Schutz verhindert nur unbeabsichtigtes Aufrufen.
"""

import os
import sys

ADMIN_PAGE_PATH = "docs/mgmt-7f3b2a-bmwe.html"


def main():
    admin_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")

    if not admin_hash:
        print("FEHLER: ADMIN_PASSWORD_HASH Secret nicht gesetzt!")
        sys.exit(1)

    if not os.path.exists(ADMIN_PAGE_PATH):
        print(f"FEHLER: {ADMIN_PAGE_PATH} nicht gefunden")
        sys.exit(1)

    with open(ADMIN_PAGE_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    if "{{ADMIN_PASSWORD_HASH}}" not in content:
        print(f"WARNUNG: Platzhalter nicht in {ADMIN_PAGE_PATH} gefunden – bereits ersetzt?")

    content = content.replace("{{ADMIN_PASSWORD_HASH}}", admin_hash)

    with open(ADMIN_PAGE_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"✓ Admin-Hash in {ADMIN_PAGE_PATH} eingefuegt")


if __name__ == "__main__":
    main()

"""
config.py
=========
Gemeinsame Konfigurationswerte fuer alle Skripte des Lobbyregister-Monitors.

Einzige Quelle (Single Source of Truth) fuer Werte, die in mehreren Skripten
gebraucht werden – so muss bei einer Aenderung nur eine Stelle angepasst werden.
"""

# Gemini-Modell – wird von gemini_enrich.py (Anreicherung) und
# health_check.py (Selbsttest) gemeinsam genutzt.
#
# Hinweis (Juli 2026): gemini-2.5-pro ist im Free Tier dieses Projekts auf
# limit: 0 gesetzt (per Test in der Google-AI-Studio-Ratenlimit-Uebersicht
# verifiziert) und daher nicht nutzbar. gemini-3.1-flash-lite hat mit Abstand
# das hoechste Kontingent (10-15 RPM, 500 Requests/Tag) und wird deshalb
# bewusst beibehalten, obwohl staerkere Modelle (3.x/2.5 Flash) qualitativ
# etwas besser waeren – deren Kontingent liegt bei nur 20 Requests/Tag.
GEMINI_MODEL = "gemini-3.1-flash-lite"

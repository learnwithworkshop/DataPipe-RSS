# 🏗️ DataPipe-RSS: System Architecture & Component Documentation

Yeh document is project ke directory structure, har folder ke maqsad, aur unke modules ke aapsi connection ko biyan karta hai. Is architecture ko SOLID principles aur high scalability ko dhyan me rakh kar design kiya gaya hai.

---

## 🗺️ High-Level Component Flow



---

## 📁 Folder Structure Overview

Neeche har folder aur file ka detail map diya gaya hai taaki future me naye features (AI, Telegram, Notion) bina kisi dikkat ke add kiye ja sakein:

```text
DataPipe-RSS/
├── config/                # System Configuration & Environment Settings
│   ├── settings.py        # Global constants aur features toggles
│   └── feeds.json         # Targets RSS URLs ki dynamic list
├── core/                  # Business Logic (The Core Engine)
│   ├── collector.py       # RSS parser aur data extraction
│   ├── database.py        # SQLite implementation (Duplicate Preventer)
│   └── processor.py       # (Future-proof) Text cleaning aur AI processing
├── connectors/            # Output Channels (Extensible Plugins)
│   ├── google_sheets.py   # Google Apps Script Webhook Integration
│   └── excel_online.py    # MS Graph / Power Automate Webhook
├── logs/                  # System Monitoring
│   └── project_audit.md   # Automatic generate hone wala code modification log
├── utils/                 # Shared Utilities (Cross-Cutting Concerns)
│   ├── __init__.py        # Python package initializer
│   ├── logger.py          # System level error aur info logging
│   ├── security.py        # API keys aur Encryption handlers
│   └── tracker.py         # Advanced File System Monitor & Git Diff Generator
├── .env                   # Sensitive Credentials (Local Only - Strictly Ignored)
├── .gitignore             # Git exclusion rules
├── requirements.txt       # Project dependencies
└── main.py                # Main Application Entry Point

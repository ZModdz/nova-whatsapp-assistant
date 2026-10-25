# NOVA — Natural-language Organizer & Virtual Assistant

A complete, privacy-first, zero-recurring-cost (**RM0/month**) personal WhatsApp AI assistant built with **Python, FastAPI, Local Ollama (`qwen2.5:3b`), SQLite (WAL mode), APScheduler, Google Calendar OAuth 2.0, and the Official Meta WhatsApp Cloud API**.

---

## Project Directory Structure

```text
jarvis/
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI server & Meta WhatsApp Cloud API webhooks
│   ├── core/
│   │   ├── config.py               # Pydantic Settings (.env loader, Asia/Kuala_Lumpur TZ)
│   │   ├── logging_config.py       # Rotating file & console structured logger
│   │   └── security.py             # HMAC-SHA256 verification, phone whitelist, rate limiter
│   ├── db/
│   │   └── database.py             # SQLite WAL database, deduplication, confirmation state
│   └── services/
│       ├── assistant_router.py     # Shortcuts, confirmation state machine & LLM tool router
│       ├── briefing_service.py     # On-demand & scheduled morning briefings
│       ├── calendar_service.py     # Google Calendar OAuth 2.0, conflict detection & CRUD
│       ├── memory_service.py       # Controlled long-term memory (view, save, forget, toggle)
│       ├── ollama_service.py       # Local Ollama LLM client with structured JSON tool calls
│       ├── reminder_service.py     # Persistent reminders, startup recovery & quiet hours
│       ├── scheduler_service.py    # APScheduler background worker & 24h window keepalive
│       ├── task_service.py         # Personal task manager (priorities, deadlines, recurrence)
│       └── whatsapp_service.py     # WhatsApp Cloud API client + Windows Toast & Email fallbacks
├── credentials/                    # Git-ignored folder for Google OAuth client_secret.json & token.json
├── data/                           # Git-ignored folder for SQLite database (jarvis.db)
├── docs/
│   ├── SETUP_WHATSAPP_AND_TUNNEL.md
│   ├── SETUP_GOOGLE_OAUTH.md
│   └── OPERATIONS_AND_SECURITY.md
├── logs/                           # Git-ignored rotating application logs (jarvis.log)
├── scripts/
│   ├── check_environment.ps1       # Verify Python venv, .env, Ollama, and Tailscale
│   ├── start_jarvis.ps1            # Launch Ollama & FastAPI server on 127.0.0.1:8000
│   ├── register_startup_task.ps1   # Auto-start JARVIS on Windows logon (even on battery)
│   ├── backup_db.ps1               # Safe online SQLite backup & restore utility
│   ├── setup_google_oauth.py       # Interactive one-time Google Calendar OAuth login
│   └── chat_cli.py                 # Local terminal WhatsApp chat simulator for instant testing
├── tests/
│   └── test_jarvis.py              # Automated pytest unit & integration suite
├── .env.example                    # Safe configuration template with placeholders
├── .gitignore                      # Protects secrets, tokens, databases, and logs
└── requirements.txt                # Python dependencies
```

---

## Quick Start Commands (Windows PowerShell)

### 1. Run Automated Tests
```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### 2. Test JARVIS Immediately in Your Terminal (No External Keys Needed)
```powershell
.\.venv\Scripts\python.exe scripts\chat_cli.py
```

### 3. Start the Live JARVIS Server
```powershell
.\scripts\start_jarvis.ps1
```

### 4. Register Auto-Start on Windows Login
```powershell
.\scripts\register_startup_task.ps1
```

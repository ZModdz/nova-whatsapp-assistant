# Operations, Security, Troubleshooting & Zero-Cost Maintenance Guide

This document covers deliverables #10, #11, #12, #13, and #14:
- Troubleshooting Guide
- Security Checklist
- Zero-Cost (RM0/month) Maintenance Guide
- Known Limitations & Future Improvements
- Manual Configuration vs. Automated Features

---

## 1. Security Checklist

- [x] **Single-Owner Phone Whitelist:** Every incoming WhatsApp message is checked against `ALLOWED_WHATSAPP_NUMBER` using constant-time `hmac.compare_digest` in [`app/core/security.py`](../app/core/security.py). Messages from any other phone number are silently dropped before reaching the database or AI model.
- [x] **Webhook Signature Verification:** Every `POST /webhook` request validates Meta's `X-Hub-Signature-256` HMAC-SHA256 signature using your `WHATSAPP_APP_SECRET`.
- [x] **Idempotent Webhook Deduplication:** Incoming `message_id`s are atomically stored in the `processed_messages` SQLite table so Meta webhook retries never execute a command or create a reminder twice.
- [x] **Confirmation Gate for Consequential Actions:** Creating, updating, or deleting Google Calendar events (or clearing all memories) enters a `pending_confirmations` state and requires you to explicitly reply `YES` before calling the external API.
- [x] **No Arbitrary Code Execution:** The local Ollama LLM only outputs structured JSON matching predefined tool schemas in [`app/services/assistant_router.py`](../app/services/assistant_router.py). It has zero shell or raw SQL access.
- [x] **Localhost Isolation:** FastAPI binds strictly to `127.0.0.1:8000` and Ollama binds to `127.0.0.1:11434`. Neither SQLite nor Ollama is ever exposed to the internet; Tailscale Funnel only forwards HTTPS traffic to port `8000`.
- [x] **Git Secret Protection:** [`.gitignore`](../.gitignore) excludes `.env`, `credentials/`, `data/*.db`, `backups/`, and `logs/`.

---

## 2. Maintaining NOVA at RM0 / Month Forever

1. **Local AI (RM0):** Always run `qwen2.5:3b` or `llama3.2:3b` via local Ollama. Never add paid OpenAI/Anthropic API keys unless you intentionally choose to later.
2. **Meta WhatsApp Sandbox & 24-Hour Service Window (RM0):**
   - Use Meta's free Developer Sandbox number (`+1 555...`) with a **System User Permanent Token** (see [`docs/SETUP_WHATSAPP_AND_TUNNEL.md`](SETUP_WHATSAPP_AND_TUNNEL.md)).
   - Because Meta allows unlimited free-form replies inside an open **24-hour customer service window** (which resets every time you text NOVA), NOVA sends you a polite keepalive prompt at hour 23 whenever you have upcoming reminders. Replying `ok` resets your free 24-hour window for another full day at RM0 cost.
3. **Google Calendar (RM0):**
   - In Google Cloud Console -> OAuth Consent Screen, click **Publish App** (without submitting for commercial verification) so your personal OAuth refresh token in `credentials/token.json` never expires after 7 days.
4. **Tailscale Funnel (RM0):**
   - Tailscale's Personal plan is free for up to 3 users and 100 devices and provides a permanent `https://<hostname>.<tailnet>.ts.net` URL without buying a domain name.

---

## 3. Backup, Restore & Update Instructions

### Safe Online Database Backup
While JARVIS is running (or stopped), run:
```powershell
.\scripts\backup_db.ps1 -Mode backup
```
This uses SQLite's native online backup API to create a timestamped snapshot in `backups/jarvis_backup_YYYYMMDD_HHMMSS.db`.

### Database Restore
```powershell
.\scripts\backup_db.ps1 -Mode restore -RestoreFile ".\backups\jarvis_backup_20261021_140000.db"
```

---

## 4. Troubleshooting Guide

| Symptom | Cause | Solution |
| :--- | :--- | :--- |
| **Meta Webhook Verify fails (403)** | `WHATSAPP_VERIFY_TOKEN` in `.env` does not match what you typed in Meta Dashboard. | Ensure both strings match character-for-character and restart JARVIS. |
| **Meta Webhook POST returns 401** | `WHATSAPP_APP_SECRET` in `.env` is missing or incorrect. | Copy the **App Secret** from Meta Dashboard -> *App Settings -> Basic* into `.env`. |
| **JARVIS ignores my WhatsApp text** | `ALLOWED_WHATSAPP_NUMBER` in `.env` doesn't match your sender phone format. | Check `logs/jarvis.log` for `Blocked unauthorized WhatsApp message from sender: 601...` and set that exact number in `.env`. |
| **JARVIS replies "My local AI brain (Ollama) is currently unreachable"** | Ollama is not running or `qwen2.5:3b` hasn't finished downloading. | Run `ollama serve` and `ollama pull qwen2.5:3b`. Shortcut commands (`HELP`, `TASKS`, `TODAY`, `BRIEFING`) still work even while Ollama is offline! |
| **Reminder did not arrive on WhatsApp while laptop was off** | Laptop was powered off at the due time, or the 24h WhatsApp window was closed. | 1. When you turn your laptop back on, JARVIS's startup recovery immediately delivers overdue reminders.<br>2. For Calendar events, JARVIS also attaches native Google Calendar popup alerts so your phone alerts you even when your laptop is off! |

---

## 5. Known Limitations & Future Improvements

1. **Laptop Powered Off / Sleep State:** Because JARVIS is self-hosted on your on-the-go laptop at RM0 cost, the local Python process and Ollama cannot process incoming WhatsApp chats while the laptop is completely powered off.
   - *How we mitigated this:*
     - Meta queues webhook retries for several hours if your laptop is briefly asleep/restarting.
     - On Windows login, `scripts/register_startup_task.ps1` automatically restarts JARVIS and runs an **Overdue Reminder Recovery Sweep**.
     - Calendar events automatically include a **30-minute native Google Calendar mobile notification**, which Google pushes to your phone even when your laptop is turned off.
2. **Meta 24-Hour Window Rule:** Free-form outbound WhatsApp messages require you to have messaged JARVIS within the last 24 hours.
   - *How we mitigated this:* Automatic 23-hour keepalive prompt + Windows Desktop Toast fallback + optional free Gmail SMTP fallback + Missed Alert queue shown on your next WhatsApp message.
3. **Future Plug-in Extensions:** The modular tool router in [`app/services/assistant_router.py`](../app/services/assistant_router.py) is ready for voice-note transcription (`faster-whisper`), local PDF study note summarization, and expense tracking.

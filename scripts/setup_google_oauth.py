"""Interactive one-time Google Calendar OAuth 2.0 setup script."""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.calendar_service import CalendarService


def main() -> None:
    print("==========================================================")
    print("  JARVIS — Google Calendar OAuth 2.0 Authorization Setup  ")
    print("==========================================================")
    cal = CalendarService()
    if not cal.client_secret_path.exists():
        print(f"\n[!] Missing file: {cal.client_secret_path}")
        print("Please follow docs/SETUP_GOOGLE_OAUTH.md to download client_secret.json")
        print(f"and place it at: {cal.client_secret_path.resolve()}\n")
        sys.exit(1)

    print("\nOpening browser for Google Calendar authorization...")
    creds = cal.get_credentials(allow_interactive=True)
    if creds and creds.valid:
        print(f"\n[OK] Success! OAuth token saved to: {cal.token_path.resolve()}")
        ok, events, err = cal.list_events(
            start_iso="2026-01-01T00:00:00+08:00",
            end_iso="2026-12-31T23:59:59+08:00",
            max_results=5,
        )
        if ok:
            print(f"[OK] Verified Calendar API connection! Found {len(events)} sample event(s).")
        else:
            print(f"[!] Token saved, but test query returned: {err}")
    else:
        print("\n[!] Authorization did not complete.")


if __name__ == "__main__":
    main()

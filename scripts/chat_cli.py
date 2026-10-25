"""Interactive local WhatsApp simulator CLI to test JARVIS directly in the terminal."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import create_app


async def run_cli() -> None:
    app = create_app()
    router = app.state.router
    print("=================================================================")
    print("  JARVIS Local WhatsApp Chat Simulator (Asia/Kuala_Lumpur)       ")
    print("  Type any message or command (HELP, TASKS, BRIEFING, etc.)      ")
    print("  Type 'exit' or 'quit' to close.                                ")
    print("=================================================================\n")

    while True:
        try:
            user_input = input("You (WhatsApp): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if user_input.lower() in ("exit", "quit"):
            print("Goodbye!")
            break
        if not user_input:
            continue

        reply = await router.handle_message(user_input)
        print(f"\nJARVIS:\n{reply}\n" + ("-" * 65))


if __name__ == "__main__":
    asyncio.run(run_cli())

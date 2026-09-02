"""The morning brief: what is due today, and what is coming.

Task Scheduler runs this daily at 06:00 as "Saga Morning Brief". A run
missed while the machine was off fires when it comes back, so the window
appears once a day whether or not the machine was on at six. It prints the
same views `today` and `upcoming` give; exports/ refreshes itself on the
way through, because cli.main brings a stale brief forward before it
dispatches.

Every run is logged. A scheduled task that has quietly stopped running looks
exactly like a quiet day with nothing due, and the log is what tells them
apart.
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

from saga import cli, export

LOG_PATH = Path(__file__).resolve().parent / "morning.log"


def log(message):
    """Append a timestamped line to the log."""
    line = f"{datetime.now():%Y-%m-%d %H:%M}  {message}"
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def brief():
    """Print both views. Returns the first non-zero exit code, or 0."""
    print(f"\n  SAGA  {datetime.now():%A, %Y-%m-%d}\n")
    status = cli.main(["today", "--days", str(export.SOON_DAYS)])
    if status:
        return status
    print()
    return cli.main(["upcoming", "--days", str(export.DEADLINE_DAYS)])


if __name__ == "__main__":
    try:
        status = brief()
        log("ok" if status == 0 else f"saga exited {status}")
    except Exception:
        detail = traceback.format_exc()
        print(detail, file=sys.stderr)
        log(f"ERROR:\n{detail}")
        status = 1

    if sys.stdin.isatty():
        try:
            input("\nPress Enter to close. ")
        except (EOFError, KeyboardInterrupt):
            print()
    sys.exit(status)
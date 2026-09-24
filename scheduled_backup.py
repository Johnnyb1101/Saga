"""Noninteractive entry point for Windows Task Scheduler."""

from saga.scheduled_backup import main

if __name__ == "__main__":
    raise SystemExit(main())

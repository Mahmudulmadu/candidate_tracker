"""Start the tracker.

    python run.py

Reads HOST and PORT from .env. Pass --reload for development.
"""

from __future__ import annotations

import sys

import uvicorn

from app.config import settings

if __name__ == "__main__":
    reload = "--reload" in sys.argv
    # ASCII only: the Windows console defaults to a code page that turns
    # arrows and box-drawing into mojibake.
    print()
    print("  Interview Tracker")
    print(f"  ->  http://localhost:{settings.port}")
    if settings.host == "0.0.0.0":
        print(f"  ->  on the office network: http://<this-machine-ip>:{settings.port}")
    print(f"  Database: {settings.db_target}")
    if not settings.db_configured:
        print("  !  PostgreSQL connection details are not set - see .env.example")
    print()

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=reload,
        log_level="info",
    )

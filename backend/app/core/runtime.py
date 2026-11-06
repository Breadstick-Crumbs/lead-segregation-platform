"""Process-level runtime setup shared by entrypoints that start their own event loop."""

import asyncio
import sys


def use_selector_event_loop_on_windows() -> None:
    """psycopg's async mode can't run on Windows' default ProactorEventLoop.

    Call this before `asyncio.run()` in scripts, Alembic and tests. Uvicorn already picks the
    selector loop when reloading (`fastapi dev`); production runs on Linux.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

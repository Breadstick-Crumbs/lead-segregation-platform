"""The Procrastinate app. Jobs live in Postgres; the worker process runs them:

    uv run procrastinate --app=app.workers.app.app worker

The API never opens this app's connection pool: it enqueues on the request's own database
connection (see `app.workers.queue`), so a job commits or rolls back with the data it refers to.
"""

from procrastinate import App, PsycopgConnector
from sqlalchemy.engine import make_url

from app.core.config import get_settings


def _conninfo() -> str:
    # The SQLAlchemy URL names the driver ("postgresql+psycopg://"); libpq wants plain postgresql.
    url = make_url(str(get_settings().database_url)).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


app = App(connector=PsycopgConnector(conninfo=_conninfo()), import_paths=["app.workers.tasks"])

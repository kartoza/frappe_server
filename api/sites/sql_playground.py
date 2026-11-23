"""Ad-hoc SQL access to site databases on the shared MariaDB server.

Connects as root (the same credential the platform already uses for
`bench new-site`/backup/restore — see bench_exec.py) rather than each
site's own scoped db user, since there's no per-site db password stored
anywhere in the platform's own database to use instead. This is a
deliberate full-access admin tool, consistent with this platform's
existing trust boundary (docker.sock is already mounted into api/worker,
granting host-level power) — gated to admin users only at the view layer.
"""
import pymysql
from django.conf import settings

# MariaDB's own catalog/internal schemas — never legitimate "sites" to
# browse or write to from here; excluded even though root could reach
# them regardless, since there's no playground use case for them and
# hiding them avoids an admin accidentally wrecking the server itself.
SYSTEM_DATABASES = {'information_schema', 'mysql', 'performance_schema', 'sys'}

# Upper bound on rows handed back to the client per statement — the
# query still runs to completion server-side either way; this just
# protects the response (and the browser rendering it) from a pathological
# `SELECT *` on a huge table.
MAX_ROWS_RETURNED = 500

# MariaDB-specific per-statement cap, in seconds (MariaDB's own unit for
# this variable — easy to mistake for milliseconds) so one runaway query
# can't hang an API request indefinitely.
MAX_STATEMENT_TIME_SECONDS = 30


def _connect(database=None):
    return pymysql.connect(
        host=settings.MARIADB_HOST,
        user='root',
        password=settings.MARIADB_ROOT_PASSWORD,
        database=database,
        client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS,
        connect_timeout=10,
        cursorclass=pymysql.cursors.Cursor,
    )


def list_databases():
    """Non-system database names on the shared MariaDB server (i.e. sites)."""
    conn = _connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SHOW DATABASES')
            names = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()
    return sorted(name for name in names if name not in SYSTEM_DATABASES)


def run_sql(database, sql):
    """Execute one or more ';'-separated statements against ``database``.

    Returns a list of per-statement results, each either
    ``{"columns": [...], "rows": [[...]], "truncated": bool}`` for a
    statement that produced a result set, or ``{"affected_rows": N}``
    for one that didn't (INSERT/UPDATE/DELETE/DDL). Raises
    ``pymysql.Error`` on failure — the caller is responsible for turning
    that into a clean 400, not a 500; this module doesn't swallow errors
    because partial execution of a multi-statement batch matters to the
    caller (which statement failed, and did earlier ones in the same
    batch already commit).
    """
    if database not in list_databases():
        raise ValueError(f'"{database}" is not a known database.')

    conn = _connect(database=database)
    try:
        with conn.cursor() as cursor:
            cursor.execute(f'SET SESSION MAX_STATEMENT_TIME={MAX_STATEMENT_TIME_SECONDS}')
            cursor.execute(sql)
            results = [_read_resultset(cursor)]
            while cursor.nextset():
                results.append(_read_resultset(cursor))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return results


def _read_resultset(cursor):
    if cursor.description is None:
        return {'affected_rows': cursor.rowcount}

    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchmany(MAX_ROWS_RETURNED)
    truncated = cursor.fetchone() is not None
    return {
        'columns': columns,
        'rows': [list(row) for row in rows],
        'truncated': truncated,
    }

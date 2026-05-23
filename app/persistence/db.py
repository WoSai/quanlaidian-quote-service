import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quote (
    id               TEXT PRIMARY KEY,
    org              TEXT NOT NULL,
    form_hash        TEXT NOT NULL,
    idempotency_key  TEXT,
    form_json        TEXT NOT NULL,
    config_json      TEXT NOT NULL,
    factor           REAL NOT NULL,
    total_list       INTEGER NOT NULL,
    total_final      INTEGER NOT NULL,
    pricing_version  TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_quote_org_form_hash
    ON quote(org, form_hash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_quote_org_idempotency_key
    ON quote(org, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_quote_created_at
    ON quote(created_at);

CREATE TABLE IF NOT EXISTS quote_render (
    id          TEXT PRIMARY KEY,
    quote_id    TEXT NOT NULL REFERENCES quote(id) ON DELETE CASCADE,
    format      TEXT NOT NULL CHECK (format IN ('pdf', 'xlsx', 'json')),
    file_token  TEXT NOT NULL,
    filename    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    short_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_render_quote_format
    ON quote_render(quote_id, format);

CREATE TABLE IF NOT EXISTS approval (
    id              TEXT PRIMARY KEY,
    quote_id        TEXT UNIQUE NOT NULL REFERENCES quote(id) ON DELETE CASCADE,
    required        INTEGER NOT NULL,
    reasons_json    TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('not_required', 'pending', 'approved', 'rejected')),
    requested_by    TEXT,
    requested_at    TEXT NOT NULL,
    decided_by      TEXT,
    decision_reason TEXT,
    decided_at      TEXT
);

CREATE TABLE IF NOT EXISTS api_token (
    token_id     TEXT PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,
    org          TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT,
    revoked_at   TEXT,
    last_used_on TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_token_hash
    ON api_token(token_hash);
CREATE INDEX IF NOT EXISTS idx_api_token_org
    ON api_token(org);
"""


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migrate_short_id(conn: sqlite3.Connection) -> None:
    """Add quote_render.short_id to pre-existing databases, then index it.

    The index is created here (not in _SCHEMA) so the column is guaranteed to
    exist on legacy DBs whose quote_render table predates short_id.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(quote_render)")}
    if "short_id" not in cols:
        conn.execute("ALTER TABLE quote_render ADD COLUMN short_id TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_render_short_id "
        "ON quote_render(short_id) WHERE short_id IS NOT NULL"
    )


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(path)
    try:
        conn.executescript(_SCHEMA)
        _migrate_short_id(conn)
    finally:
        conn.close()


@contextmanager
def get_conn(path: Path) -> Iterator[sqlite3.Connection]:
    conn = _connect(path)
    try:
        yield conn
    finally:
        conn.close()

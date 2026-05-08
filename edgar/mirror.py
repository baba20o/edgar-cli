"""Local SQLite mirror of SEC EDGAR data for one or more filers.

Schema:
- `filers(cik, ticker, name, sic, sic_description, fiscal_year_end,
          submissions_etag, submissions_last_modified, last_synced_at)`
- `filings(cik, accession, form, filed, primary_document, items, filing_url,
           primary_doc_url, period_of_report, INTEGER PRIMARY KEY rowid)`
- `facts(cik, taxonomy, tag, label, unit, val, start_date, end_date,
         filed, accession, frame, period_type, period_length_days)`
- `documents(cik, accession, sequence, doc_type, document, description,
             url, INTEGER PRIMARY KEY rowid)`
- `filings_fts` virtual table (FTS5) over filing description + items.

Refresh is incremental: the submissions ETag is sent on subsequent fetches,
and only new accessions are inserted. Facts are reconciled by
`(cik, taxonomy, tag, start_date, end_date, accession)` — restatements show
up as duplicate `(cik, tag, start, end)` rows with different `accession`/`val`.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 1


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS filers (
    cik TEXT PRIMARY KEY,
    ticker TEXT,
    name TEXT,
    sic TEXT,
    sic_description TEXT,
    fiscal_year_end TEXT,
    entity_type TEXT,
    submissions_etag TEXT,
    submissions_last_modified TEXT,
    facts_etag TEXT,
    facts_last_modified TEXT,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS filings (
    cik TEXT NOT NULL,
    accession TEXT NOT NULL,
    form TEXT,
    filed TEXT,
    period_of_report TEXT,
    primary_document TEXT,
    primary_doc_description TEXT,
    items TEXT,
    filing_url TEXT,
    primary_doc_url TEXT,
    PRIMARY KEY (cik, accession)
);
CREATE INDEX IF NOT EXISTS idx_filings_cik_filed ON filings(cik, filed DESC);
CREATE INDEX IF NOT EXISTS idx_filings_cik_form_filed ON filings(cik, form, filed DESC);

CREATE TABLE IF NOT EXISTS facts (
    cik TEXT NOT NULL,
    taxonomy TEXT NOT NULL,
    tag TEXT NOT NULL,
    label TEXT,
    description TEXT,
    unit TEXT NOT NULL,
    val REAL,
    start_date TEXT,
    end_date TEXT,
    filed TEXT,
    accession TEXT,
    fy INTEGER,
    fp TEXT,
    form TEXT,
    frame TEXT,
    period_length_days INTEGER,
    PRIMARY KEY (cik, taxonomy, tag, unit, start_date, end_date, accession)
);
CREATE INDEX IF NOT EXISTS idx_facts_cik_tag ON facts(cik, tag);
CREATE INDEX IF NOT EXISTS idx_facts_period ON facts(cik, tag, start_date, end_date);

CREATE TABLE IF NOT EXISTS documents (
    cik TEXT NOT NULL,
    accession TEXT NOT NULL,
    sequence TEXT,
    doc_type TEXT,
    document TEXT,
    description TEXT,
    url TEXT,
    PRIMARY KEY (cik, accession, document)
);

CREATE VIRTUAL TABLE IF NOT EXISTS filings_fts USING fts5(
    cik UNINDEXED,
    accession UNINDEXED,
    form,
    filed UNINDEXED,
    description,
    items,
    primary_document
);

CREATE TABLE IF NOT EXISTS filing_bodies (
    cik TEXT NOT NULL,
    accession TEXT NOT NULL,
    form TEXT,
    filed TEXT,
    primary_document TEXT,
    body_length INTEGER,
    fetched_at TEXT,
    truncated INTEGER DEFAULT 0,
    PRIMARY KEY (cik, accession)
);
CREATE INDEX IF NOT EXISTS idx_bodies_cik_form ON filing_bodies(cik, form, filed DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS filing_bodies_fts USING fts5(
    cik UNINDEXED,
    accession UNINDEXED,
    form UNINDEXED,
    filed UNINDEXED,
    body
);
"""

# Hard cap on per-document body size to keep the database bounded. Content
# beyond this is dropped with `truncated=1` recorded so callers can detect it.
DEFAULT_BODY_MAX_BYTES = 4 * 1024 * 1024  # 4 MB of plain text per filing


@contextmanager
def open_db(path: str | Path):
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
        yield conn
    finally:
        conn.close()


def _normalize_recent(recent: dict, cik: str) -> list[tuple]:
    accessions = recent.get("accessionNumber", [])
    rows = []
    for i, accn in enumerate(accessions):
        if not accn:
            continue
        rows.append((
            cik, accn,
            (recent.get("form", []) + [None] * (i + 1))[i],
            (recent.get("filingDate", []) + [None] * (i + 1))[i],
            (recent.get("reportDate", []) + [None] * (i + 1))[i],
            (recent.get("primaryDocument", []) + [None] * (i + 1))[i],
            (recent.get("primaryDocDescription", []) + [None] * (i + 1))[i],
            (recent.get("items", []) + [None] * (i + 1))[i],
        ))
    return rows


def ingest_submissions(conn: sqlite3.Connection, cik: str, submissions: dict,
                       client=None) -> dict:
    """Insert/update one filer's submissions JSON. Returns counts."""
    from edgar.api import filing_urls, normalize_cik

    cik_n = normalize_cik(cik)
    conn.execute("""
        INSERT INTO filers(cik, ticker, name, sic, sic_description,
                            fiscal_year_end, entity_type, last_synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cik) DO UPDATE SET
            ticker=excluded.ticker, name=excluded.name, sic=excluded.sic,
            sic_description=excluded.sic_description,
            fiscal_year_end=excluded.fiscal_year_end,
            entity_type=excluded.entity_type,
            last_synced_at=excluded.last_synced_at
    """, (
        cik_n,
        ", ".join(submissions.get("tickers", [])) or None,
        submissions.get("name") or None,
        submissions.get("sic") or None,
        submissions.get("sicDescription") or None,
        submissions.get("fiscalYearEnd") or None,
        submissions.get("entityType") or None,
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    ))

    recent = submissions.get("filings", {}).get("recent", {})
    rows = _normalize_recent(recent, cik_n)
    inserted = 0
    fts_rows = []
    for row in rows:
        urls = filing_urls(cik_n, row[1], row[5] or "")
        try:
            conn.execute("""
                INSERT INTO filings(cik, accession, form, filed, period_of_report,
                                    primary_document, primary_doc_description, items,
                                    filing_url, primary_doc_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (*row, urls["filing_url"], urls["primary_doc_url"]))
            inserted += 1
            fts_rows.append((row[0], row[1], row[2], row[3], row[6] or "",
                             row[7] or "", row[5] or ""))
        except sqlite3.IntegrityError:
            pass  # already mirrored

    if fts_rows:
        conn.executemany("""
            INSERT INTO filings_fts(cik, accession, form, filed, description,
                                     items, primary_document)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, fts_rows)

    conn.commit()
    return {"filings_inserted": inserted, "filings_seen": len(rows)}


def ingest_companyfacts(conn: sqlite3.Connection, cik: str, facts_doc: dict) -> dict:
    """Insert/update facts from a companyfacts JSON. Returns counts."""
    from edgar.api import normalize_cik, period_type_from_days
    from datetime import datetime

    cik_n = normalize_cik(cik)
    inserted = 0
    seen = 0
    facts = facts_doc.get("facts", {}) or {}
    for taxonomy, tags in facts.items():
        for tag, concept in tags.items():
            label = concept.get("label", "")
            description = concept.get("description", "")
            for unit, rows in (concept.get("units", {}) or {}).items():
                for row in rows:
                    seen += 1
                    start = row.get("start", "")
                    end = row.get("end", "")
                    period_days = 0
                    try:
                        if start and end:
                            d_s = datetime.strptime(start, "%Y-%m-%d").date()
                            d_e = datetime.strptime(end, "%Y-%m-%d").date()
                            period_days = (d_e - d_s).days
                    except (ValueError, TypeError):
                        pass
                    try:
                        conn.execute("""
                            INSERT INTO facts(cik, taxonomy, tag, label, description,
                                              unit, val, start_date, end_date, filed,
                                              accession, fy, fp, form, frame,
                                              period_length_days)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            cik_n, taxonomy, tag, label, description, unit,
                            row.get("val"), start, end, row.get("filed", ""),
                            row.get("accn", ""), row.get("fy"), row.get("fp", ""),
                            row.get("form", ""), row.get("frame", ""), period_days,
                        ))
                        inserted += 1
                    except sqlite3.IntegrityError:
                        pass
    conn.commit()
    return {"facts_inserted": inserted, "facts_seen": seen}


def get_filer_metadata(conn: sqlite3.Connection, cik: str) -> Optional[dict]:
    cur = conn.execute("SELECT * FROM filers WHERE cik = ?", (cik,))
    row = cur.fetchone()
    return dict(row) if row else None


def update_filer_etag(conn: sqlite3.Connection, cik: str, source: str,
                      etag: Optional[str], last_modified: Optional[str]) -> None:
    if source == "submissions":
        conn.execute("""
            UPDATE filers SET submissions_etag = ?, submissions_last_modified = ?
            WHERE cik = ?
        """, (etag, last_modified, cik))
    elif source == "facts":
        conn.execute("""
            UPDATE filers SET facts_etag = ?, facts_last_modified = ?
            WHERE cik = ?
        """, (etag, last_modified, cik))
    conn.commit()


def _escape_fts_query(query: str) -> str:
    """Wrap a user query so FTS5 treats it as a phrase rather than parsing
    operators. Hyphens in particular (e.g. `10-K`) are otherwise interpreted
    as NOT operators."""
    if any(ch in query for ch in '"()*'):
        return query  # caller is using FTS syntax explicitly
    cleaned = query.replace('"', '""')
    return f'"{cleaned}"'


def ingest_filing_body(conn: sqlite3.Connection, cik: str, accession: str,
                       form: str, filed: str, primary_document: str,
                       body_text: str,
                       max_bytes: int = DEFAULT_BODY_MAX_BYTES) -> dict:
    """Insert a filing's plain-text body into both `filing_bodies` and the
    `filing_bodies_fts` virtual table. Idempotent: existing rows are skipped."""
    body_text = body_text or ""
    truncated = 0
    if len(body_text.encode("utf-8")) > max_bytes:
        # Truncate at character boundary that fits within byte budget.
        body_text = body_text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        truncated = 1
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        conn.execute("""
            INSERT INTO filing_bodies(cik, accession, form, filed,
                                       primary_document, body_length,
                                       fetched_at, truncated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (cik, accession, form, filed, primary_document,
              len(body_text), fetched_at, truncated))
        conn.execute("""
            INSERT INTO filing_bodies_fts(cik, accession, form, filed, body)
            VALUES (?, ?, ?, ?, ?)
        """, (cik, accession, form, filed, body_text))
        conn.commit()
        return {"inserted": True, "truncated": bool(truncated),
                "body_length": len(body_text)}
    except sqlite3.IntegrityError:
        return {"inserted": False, "already_present": True}


def search_bodies(conn: sqlite3.Connection, query: str,
                  form: Optional[str] = None, since: Optional[str] = None,
                  ciks: Optional[list[str]] = None,
                  limit: int = 25,
                  snippet_chars: int = 240) -> list[dict]:
    """Full-text search filing bodies. Returns rows with snippets."""
    sql = (
        "SELECT b.cik, b.accession, b.form, b.filed, "
        "bb.primary_document, fi.name, "
        "snippet(filing_bodies_fts, 4, '«', '»', ' … ', 16) AS snippet "
        "FROM filing_bodies_fts b "
        "LEFT JOIN filing_bodies bb ON b.cik = bb.cik AND b.accession = bb.accession "
        "LEFT JOIN filers fi ON b.cik = fi.cik "
        "WHERE filing_bodies_fts MATCH ?"
    )
    params: list[Any] = [_escape_fts_query(query)]
    if form:
        sql += " AND b.form = ?"
        params.append(form.upper())
    if since:
        sql += " AND b.filed >= ?"
        params.append(since)
    if ciks:
        placeholders = ",".join("?" * len(ciks))
        sql += f" AND b.cik IN ({placeholders})"
        params.extend(ciks)
    sql += " ORDER BY b.filed DESC LIMIT ?"
    params.append(limit)
    return [dict(row) for row in conn.execute(sql, params)]


def filings_needing_bodies(conn: sqlite3.Connection, cik: str,
                           form: Optional[str] = None,
                           limit: int = 50) -> list[dict]:
    """Return filings (newest first) that don't yet have a body row."""
    sql = ("SELECT f.cik, f.accession, f.form, f.filed, f.primary_doc_url "
           "FROM filings f LEFT JOIN filing_bodies b "
           "ON f.cik = b.cik AND f.accession = b.accession "
           "WHERE f.cik = ? AND b.accession IS NULL "
           "AND f.primary_doc_url != ''")
    params: list[Any] = [cik]
    if form:
        sql += " AND f.form = ?"
        params.append(form.upper())
    sql += " ORDER BY f.filed DESC LIMIT ?"
    params.append(limit)
    return [dict(row) for row in conn.execute(sql, params)]


def search_filings(conn: sqlite3.Connection, query: str,
                   form: Optional[str] = None, since: Optional[str] = None,
                   ciks: Optional[list[str]] = None,
                   limit: int = 50) -> list[dict]:
    """Full-text search the mirrored filing metadata via FTS5."""
    sql = ("SELECT f.cik, f.accession, f.form, f.filed, f.description, "
           "f.items, f.primary_document, fi.name "
           "FROM filings_fts f LEFT JOIN filers fi ON f.cik = fi.cik "
           "WHERE filings_fts MATCH ?")
    params: list[Any] = [_escape_fts_query(query)]
    if form:
        sql += " AND f.form = ?"
        params.append(form.upper())
    if since:
        sql += " AND f.filed >= ?"
        params.append(since)
    if ciks:
        placeholders = ",".join("?" * len(ciks))
        sql += f" AND f.cik IN ({placeholders})"
        params.extend(ciks)
    sql += " ORDER BY f.filed DESC LIMIT ?"
    params.append(limit)
    return [dict(row) for row in conn.execute(sql, params)]


def stats(conn: sqlite3.Connection) -> dict:
    out = {}
    out["filers"] = conn.execute("SELECT COUNT(*) FROM filers").fetchone()[0]
    out["filings"] = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
    out["facts"] = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    out["documents"] = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    out["fts_rows"] = conn.execute("SELECT COUNT(*) FROM filings_fts").fetchone()[0]
    out["last_synced"] = conn.execute(
        "SELECT MAX(last_synced_at) FROM filers"
    ).fetchone()[0]
    return out

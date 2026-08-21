import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_PATH, DEFAULTS

_lock = threading.RLock()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with _lock, connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            language TEXT NOT NULL,
            video_id TEXT NOT NULL,
            title TEXT NOT NULL,
            normalised_title TEXT,
            description TEXT,
            channel_id TEXT,
            channel_title TEXT,
            published_at TEXT,
            default_audio_language TEXT,
            default_language TEXT,
            duration_seconds INTEGER NOT NULL DEFAULT 0,
            thumbnail_url TEXT,
            youtube_url TEXT NOT NULL,
            embeddable INTEGER NOT NULL DEFAULT 0,
            licence TEXT,
            status TEXT NOT NULL DEFAULT 'DISCOVERED',
            rejection_reason TEXT,
            download_url TEXT,
            download_status TEXT NOT NULL DEFAULT 'NOT_READY',
            file_path TEXT,
            bytes_downloaded INTEGER NOT NULL DEFAULT 0,
            total_bytes INTEGER,
            download_speed_bps REAL,
            download_eta_seconds REAL,
            download_error TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            source_query TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(language, video_id)
        );

        CREATE INDEX IF NOT EXISTS idx_movies_language_status
        ON movies(language, status);

        CREATE INDEX IF NOT EXISTS idx_movies_download_status
        ON movies(download_status);

        CREATE TABLE IF NOT EXISTS search_state (
            language TEXT NOT NULL,
            search_query TEXT NOT NULL,
            next_page_token TEXT,
            exhausted INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(language, search_query)
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            language TEXT NOT NULL,
            target INTEGER,
            status TEXT NOT NULL,
            stats_json TEXT,
            message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS provider_scan_cache (
            provider TEXT NOT NULL,
            language TEXT NOT NULL,
            identifier TEXT NOT NULL,
            status TEXT NOT NULL,
            result_code TEXT,
            reason TEXT,
            title TEXT,
            duration_seconds INTEGER,
            licence TEXT,
            rights_clear INTEGER,
            download_url TEXT,
            metadata_json TEXT,
            checked_at TEXT NOT NULL,
            PRIMARY KEY(provider, language, identifier)
        );
        """)
        movie_columns = {row["name"] for row in conn.execute("PRAGMA table_info(movies)")}
        for column in ("default_audio_language", "default_language"):
            if column not in movie_columns:
                conn.execute(f"ALTER TABLE movies ADD COLUMN {column} TEXT")
        if "source_status" not in movie_columns:
            conn.execute("ALTER TABLE movies ADD COLUMN source_status TEXT NOT NULL DEFAULT 'SOURCE_PENDING'")
        for column in ("source_provider", "source_error", "source_checked_at"):
            if column not in movie_columns:
                conn.execute(f"ALTER TABLE movies ADD COLUMN {column} TEXT")
        if "provider" not in movie_columns:
            conn.execute("ALTER TABLE movies ADD COLUMN provider TEXT NOT NULL DEFAULT 'youtube'")
        if "provider_id" not in movie_columns:
            conn.execute("ALTER TABLE movies ADD COLUMN provider_id TEXT")
            conn.execute("UPDATE movies SET provider_id=video_id WHERE provider='youtube'")
        if "source_type" not in movie_columns:
            conn.execute("ALTER TABLE movies ADD COLUMN source_type TEXT")
        if "download_backend" not in movie_columns:
            conn.execute("ALTER TABLE movies ADD COLUMN download_backend TEXT")
        for key, value in DEFAULTS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (key, value))


def get_setting(key, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with _lock, connect() as conn:
        conn.execute("""
        INSERT INTO settings(key,value) VALUES (?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, str(value)))


def all_settings():
    with connect() as conn:
        return {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM settings")}


def get_search_state(language, query):
    with connect() as conn:
        row = conn.execute("""
        SELECT next_page_token, exhausted FROM search_state
        WHERE language=? AND search_query=?
        """, (language, query)).fetchone()
        if not row:
            return None, False
        return row["next_page_token"], bool(row["exhausted"])


def save_search_state(language, query, token, exhausted):
    with _lock, connect() as conn:
        conn.execute("""
        INSERT INTO search_state(language,search_query,next_page_token,exhausted,updated_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(language,search_query) DO UPDATE SET
            next_page_token=excluded.next_page_token,
            exhausted=excluded.exhausted,
            updated_at=excluded.updated_at
        """, (language, query, token, int(exhausted), now_iso()))


def reset_search_state(language):
    with _lock, connect() as conn:
        conn.execute("DELETE FROM search_state WHERE language=?", (language,))


def delete_movie_from_library(movie_id):
    """Permanently forget one catalogue record; never touches its file path."""
    with _lock, connect() as conn:
        row = conn.execute("SELECT language FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM movies WHERE id=?", (movie_id,))
        return dict(row)


def clear_movie_library():
    """Remove catalogue rows only, preserving settings and provider memory."""
    with _lock, connect() as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            deleted = conn.execute("DELETE FROM movies").rowcount
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise


def start_fresh():
    """Forget discovery/download state while deliberately preserving settings/files."""
    with _lock, connect() as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            deleted = conn.execute("DELETE FROM movies").rowcount
            conn.execute("DELETE FROM search_state")
            conn.execute("DELETE FROM provider_scan_cache")
            conn.execute("DELETE FROM jobs")
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise


def upsert_movie(movie):
    now = now_iso()
    with _lock, connect() as conn:
        conn.execute("""
        INSERT INTO movies(
            language,video_id,provider,title,normalised_title,description,
            channel_id,channel_title,published_at,default_audio_language,default_language,duration_seconds,
            thumbnail_url,youtube_url,embeddable,licence,status,
            rejection_reason,download_url,download_status,source_query,
            created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(language,video_id) DO UPDATE SET
            title=excluded.title,
            normalised_title=excluded.normalised_title,
            description=excluded.description,
            channel_id=excluded.channel_id,
            channel_title=excluded.channel_title,
            published_at=excluded.published_at,
            default_audio_language=excluded.default_audio_language,
            default_language=excluded.default_language,
            duration_seconds=excluded.duration_seconds,
            thumbnail_url=excluded.thumbnail_url,
            embeddable=excluded.embeddable,
            licence=excluded.licence,
            source_query=excluded.source_query,
            updated_at=excluded.updated_at
        """, (
            movie["language"], movie["video_id"], movie.get("provider", "youtube"),
            movie["title"], movie.get("normalised_title"),
            movie.get("description"), movie.get("channel_id"), movie.get("channel_title"),
            movie.get("published_at"), movie.get("default_audio_language"), movie.get("default_language"),
            int(movie.get("duration_seconds") or 0),
            movie.get("thumbnail_url"), movie["youtube_url"], int(bool(movie.get("embeddable"))),
            movie.get("licence"), movie.get("status", "DISCOVERED"), movie.get("rejection_reason"),
            movie.get("download_url"), movie.get("download_status", "NOT_READY"),
            movie.get("source_query"), now, now
        ))


def upsert_movie_with_target_guard(movie, accepted_statuses, target):
    """Upsert a movie, atomically re-checking the accepted count if it is
    about to become ACCEPTED.

    The count-check and the write happen inside one SQLite write
    transaction (BEGIN IMMEDIATE), so the accepted count can never be
    pushed past target even if another writer -- another thread in this
    process, or a separate process such as the CLI -- is racing to accept
    a movie for the same language at the same moment. If the target is
    already satisfied at write time, the candidate is stored as
    DISCOVERED (not accepted, not rejected as bad content) instead.

    Pass target=None to skip the ceiling check entirely (still an atomic
    write) -- used when discovery is counting toward a downloadable-only
    target instead of the raw accepted count.

    Returns the status actually stored.
    """
    now = now_iso()
    requested_status = movie.get("status", "DISCOVERED")
    final_status = requested_status
    language = movie["language"]
    with _lock, connect() as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            if requested_status == "ACCEPTED" and target is not None:
                placeholders = ",".join("?" for _ in accepted_statuses)
                current = conn.execute(
                    f"SELECT COUNT(*) FROM movies WHERE language=? AND status IN ({placeholders})",
                    [language, *accepted_statuses]
                ).fetchone()[0]
                if current >= target:
                    final_status = "DISCOVERED"

            conn.execute("""
            INSERT INTO movies(
                language,video_id,provider,title,normalised_title,description,
                channel_id,channel_title,published_at,default_audio_language,default_language,duration_seconds,
                thumbnail_url,youtube_url,embeddable,licence,status,
                rejection_reason,download_url,download_status,source_query,
                created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(language,video_id) DO UPDATE SET
                title=excluded.title,
                normalised_title=excluded.normalised_title,
                description=excluded.description,
                channel_id=excluded.channel_id,
                channel_title=excluded.channel_title,
                published_at=excluded.published_at,
                default_audio_language=excluded.default_audio_language,
                default_language=excluded.default_language,
                duration_seconds=excluded.duration_seconds,
                thumbnail_url=excluded.thumbnail_url,
                embeddable=excluded.embeddable,
                licence=excluded.licence,
                source_query=excluded.source_query,
                updated_at=excluded.updated_at
            """, (
                movie["language"], movie["video_id"], movie.get("provider", "youtube"),
                movie["title"], movie.get("normalised_title"),
                movie.get("description"), movie.get("channel_id"), movie.get("channel_title"),
                movie.get("published_at"), movie.get("default_audio_language"), movie.get("default_language"),
                int(movie.get("duration_seconds") or 0),
                movie.get("thumbnail_url"), movie["youtube_url"], int(bool(movie.get("embeddable"))),
                movie.get("licence"), final_status, movie.get("rejection_reason"),
                movie.get("download_url"), movie.get("download_status", "NOT_READY"),
                movie.get("source_query"), now, now
            ))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return final_status


def get_movie_id(language, video_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM movies WHERE language=? AND video_id=?", (language, video_id)
        ).fetchone()
        return row["id"] if row else None


def all_video_ids():
    with connect() as conn:
        return {r["video_id"] for r in conn.execute("SELECT DISTINCT video_id FROM movies")}


def count_downloadable(language, accepted_statuses):
    """Count movies whose source is verified ready or already downloaded."""
    placeholders = ",".join("?" for _ in accepted_statuses)
    with connect() as conn:
        return conn.execute(
            f"""SELECT COUNT(*) FROM movies WHERE language=? AND status IN ({placeholders})
                AND (source_status='SOURCE_READY' OR download_status='DOWNLOADED')""",
            [language, *accepted_statuses]
        ).fetchone()[0]


def apply_source_resolution(movie_id, language, resolution, accepted_statuses):
    """Persist a resolved (or missing/invalid) download source for a movie.

    The write and the resulting downloadable-count computation happen in one
    SQLite write transaction, so callers get a consistent count reflecting
    at least this write -- avoiding a separate, racy follow-up count query.

    Returns the downloadable count for the language after this write.
    """
    now = now_iso()
    ready = resolution.get("source_status") == "SOURCE_READY"
    with _lock, connect() as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("""
            UPDATE movies SET
                source_status=?,
                source_provider=?,
                source_error=?,
                source_checked_at=?,
                download_url=CASE WHEN ?=1 THEN ? ELSE download_url END,
                download_status=CASE WHEN ?=1 AND download_status<>'DOWNLOADED' THEN 'READY' ELSE download_status END,
                status=CASE WHEN status='DISCOVERED' AND ?=1 THEN 'ACCEPTED' ELSE status END,
                updated_at=?
            WHERE id=?
            """, (
                resolution.get("source_status", "SOURCE_MISSING"),
                resolution.get("provider"), resolution.get("error"), now,
                int(ready), resolution.get("download_url"),
                int(ready), int(ready), now, movie_id
            ))
            placeholders = ",".join("?" for _ in accepted_statuses)
            downloadable = conn.execute(
                f"""SELECT COUNT(*) FROM movies WHERE language=? AND status IN ({placeholders})
                    AND (source_status='SOURCE_READY' OR download_status='DOWNLOADED')""",
                [language, *accepted_statuses]
            ).fetchone()[0]
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return downloadable


def mark_youtube_download_ready(movie_id):
    """Make a stable YouTube identity eligible for yt-dlp.

    The watch URL is deliberately the only persisted URL: expiring CDN stream
    URLs are resolved by yt-dlp immediately before each transfer.
    """
    with _lock, connect() as conn:
        conn.execute("""UPDATE movies SET provider='youtube', provider_id=video_id,
            source_type='YOUTUBE', download_backend='YTDLP',
            source_status='SOURCE_READY', source_provider='youtube', source_error=NULL,
            source_checked_at=?, download_status=CASE WHEN download_status='DOWNLOADED'
                THEN download_status ELSE 'READY' END, updated_at=? WHERE id=?""",
            (now_iso(), now_iso(), movie_id))


def queue_movies_for_download(language, ids=None):
    """Queue selected accepted YouTube catalogue rows, reporting useful totals."""
    with _lock, connect() as conn:
        where = ["language=?", "status IN ('ACCEPTED','QUEUED')"]
        params = [language]
        if ids:
            marks = ",".join("?" for _ in ids)
            where.append(f"id IN ({marks})")
            params.extend(ids)
        rows = conn.execute(f"SELECT id,download_status FROM movies WHERE {' AND '.join(where)}", params).fetchall()
        queued = already = 0
        for row in rows:
            if row['download_status'] == 'DOWNLOADED':
                already += 1
                continue
            conn.execute("""UPDATE movies SET provider='youtube', provider_id=video_id,
                source_type='YOUTUBE', download_backend='YTDLP', source_status='SOURCE_READY',
                source_provider='youtube', source_error=NULL, download_status='READY', updated_at=? WHERE id=?""",
                (now_iso(), row['id']))
            queued += 1
        return {"requested": len(ids) if ids is not None else len(rows), "queued": queued,
                "already_downloaded": already, "could_not_queue": max(0, (len(ids) if ids is not None else len(rows))-len(rows))}


def list_movies_for_source_resolution(language, ids=None, accepted_statuses=None):
    accepted_statuses = accepted_statuses or ["ACCEPTED", "QUEUED", "DOWNLOADING", "DOWNLOADED"]
    params = [language]
    placeholders = ",".join("?" for _ in accepted_statuses)
    where = ["language=?", f"status IN ({placeholders})"]
    params.extend(accepted_statuses)
    if ids:
        id_placeholders = ",".join("?" for _ in ids)
        where.append(f"id IN ({id_placeholders})")
        params.extend(ids)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM movies WHERE {' AND '.join(where)}", params
        ).fetchall()
        return [dict(r) for r in rows]


def movie_exists(language, video_id):
    with connect() as conn:
        return conn.execute(
            "SELECT 1 FROM movies WHERE language=? AND video_id=?",
            (language, video_id)
        ).fetchone() is not None


def find_probable_title_duplicate(language, normalised_title, duration_seconds, exclude_video_id=None):
    if not normalised_title or len(normalised_title) < 4:
        return None
    params = [language, normalised_title, max(0, duration_seconds - 300), duration_seconds + 300]
    sql = """
        SELECT video_id,title,duration_seconds FROM movies
        WHERE language=? AND normalised_title=? AND duration_seconds BETWEEN ? AND ?
        AND status IN ('ACCEPTED','QUEUED','DOWNLOADING','DOWNLOADED')
    """
    if exclude_video_id:
        sql += " AND video_id<>?"
        params.append(exclude_video_id)
    sql += " LIMIT 1"
    with connect() as conn:
        return conn.execute(sql, params).fetchone()


def set_download_source(movie_id, download_url):
    with _lock, connect() as conn:
        conn.execute("""
        UPDATE movies SET download_url=?, download_status='READY',
        status=CASE WHEN status='DISCOVERED' THEN 'ACCEPTED' ELSE status END,
        updated_at=? WHERE id=?
        """, (download_url, now_iso(), movie_id))


def update_download(movie_id, **fields):
    allowed = {
        "download_status","file_path","bytes_downloaded","total_bytes",
        "download_speed_bps","download_eta_seconds","download_error",
        "retry_count","status"
    }
    updates = []
    values = []
    for key, value in fields.items():
        if key in allowed:
            updates.append(f"{key}=?")
            values.append(value)
    if not updates:
        return
    updates.append("updated_at=?")
    values.append(now_iso())
    values.append(movie_id)
    with _lock, connect() as conn:
        conn.execute(f"UPDATE movies SET {', '.join(updates)} WHERE id=?", values)


def get_movie(movie_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
        return dict(row) if row else None


def list_movies(language, status=None, limit=100, offset=0, search=""):
    params = [language]
    where = ["language=?"]
    if status == "ACCEPTED":
        where.append("status IN ('ACCEPTED','QUEUED','DOWNLOADING')")
    elif status == "DOWNLOADED":
        where.append("download_status='DOWNLOADED'")
    elif status == "REJECTED":
        where.append("status=?")
        params.append("REJECTED")
    if search:
        where.append("(title LIKE ? OR channel_title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    params.extend([int(limit), int(offset)])
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT * FROM movies WHERE {' AND '.join(where)}
                ORDER BY id DESC LIMIT ? OFFSET ?""",
            params
        ).fetchall()
        return [dict(r) for r in rows]


def list_movie_ids(language, status=None, search="", limit=20000):
    params = [language]
    where = ["language=?"]
    if status == "ACCEPTED":
        where.append("status IN ('ACCEPTED','QUEUED','DOWNLOADING')")
    elif status == "DOWNLOADED":
        where.append("download_status='DOWNLOADED'")
    elif status == "REJECTED":
        where.append("status=?")
        params.append("REJECTED")
    if search:
        where.append("(title LIKE ? OR channel_title LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    params.append(int(limit))
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT id FROM movies WHERE {' AND '.join(where)}
                ORDER BY id DESC LIMIT ?""",
            params
        ).fetchall()
        return [r["id"] for r in rows]


def get_provider_cache_entry(provider, language, identifier):
    """Look up a previously-scanned provider candidate.

    Status is one of ACCEPTED_READY, ACCEPTED_AMBIGUOUS, REJECTED,
    ERROR_TEMPORARY, ERROR_PERMANENT. Every status except ERROR_TEMPORARY is
    a final verdict a caller can reuse without re-fetching metadata;
    ERROR_TEMPORARY means the previous attempt failed on a transient network
    issue and should be retried on a later run.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM provider_scan_cache WHERE provider=? AND language=? AND identifier=?",
            (provider, language, identifier)
        ).fetchone()
        return dict(row) if row else None


def set_provider_cache_entry(
    provider, language, identifier, status, result_code=None, reason=None,
    title=None, duration_seconds=None, licence=None, rights_clear=None,
    download_url=None, metadata_json=None,
):
    with _lock, connect() as conn:
        conn.execute("""
        INSERT INTO provider_scan_cache(
            provider,language,identifier,status,result_code,reason,title,
            duration_seconds,licence,rights_clear,download_url,metadata_json,checked_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(provider,language,identifier) DO UPDATE SET
            status=excluded.status,
            result_code=excluded.result_code,
            reason=excluded.reason,
            title=excluded.title,
            duration_seconds=excluded.duration_seconds,
            licence=excluded.licence,
            rights_clear=excluded.rights_clear,
            download_url=excluded.download_url,
            metadata_json=excluded.metadata_json,
            checked_at=excluded.checked_at
        """, (
            provider, language, identifier, status, result_code, reason, title,
            duration_seconds, licence, (int(rights_clear) if rights_clear is not None else None),
            download_url, metadata_json, now_iso()
        ))


def count_movies(language, statuses=None):
    params = [language]
    sql = "SELECT COUNT(*) FROM movies WHERE language=?"
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        sql += f" AND status IN ({placeholders})"
        params.extend(statuses)
    with connect() as conn:
        return conn.execute(sql, params).fetchone()[0]


def counts(language):
    with connect() as conn:
        rows = conn.execute("""
        SELECT status,COUNT(*) n FROM movies WHERE language=? GROUP BY status
        """, (language,)).fetchall()
        status_counts = {r["status"]: r["n"] for r in rows}
        drows = conn.execute("""
        SELECT download_status,COUNT(*) n FROM movies
        WHERE language=? GROUP BY download_status
        """, (language,)).fetchall()
        download_counts = {r["download_status"]: r["n"] for r in drows}
        return status_counts, download_counts


def source_status_counts(language, accepted_statuses):
    placeholders = ",".join("?" for _ in accepted_statuses)
    with connect() as conn:
        rows = conn.execute(f"""
        SELECT source_status, COUNT(*) n FROM movies
        WHERE language=? AND status IN ({placeholders})
        GROUP BY source_status
        """, [language, *accepted_statuses]).fetchall()
        return {r["source_status"]: r["n"] for r in rows}


def next_download_ready(language=None):
    """Atomically selects and claims the next ready download job.

    The select and the claiming write (flipping download_status to
    DOWNLOADING, which is not one of the selectable states) happen inside a
    single BEGIN IMMEDIATE transaction, so concurrent worker threads racing
    to call this at the same moment can never claim the same movie twice.
    Returns the claimed movie dict, or None if nothing is ready.
    """
    params = []
    sql = """
    SELECT * FROM movies
    WHERE download_status IN ('READY','QUEUED','WAITING_NETWORK')
      AND ((download_backend='YTDLP' AND provider='youtube') OR (download_url IS NOT NULL AND download_url<>''))
      AND status<>'REJECTED'
    """
    if language:
        sql += " AND language=?"
        params.append(language)
    sql += " ORDER BY CASE download_status WHEN 'QUEUED' THEN 0 WHEN 'READY' THEN 1 ELSE 2 END, id ASC LIMIT 1"
    with _lock, connect() as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(sql, params).fetchone()
            if not row:
                conn.commit()
                return None
            movie = dict(row)
            conn.execute(
                "UPDATE movies SET download_status='DOWNLOADING', status='DOWNLOADING', updated_at=? WHERE id=?",
                (now_iso(), movie["id"])
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    movie["download_status"] = "DOWNLOADING"
    movie["status"] = "DOWNLOADING"
    return movie


def reset_interrupted_downloads():
    """Normalises movies left mid-transfer by an unclean shutdown.

    A movie whose download_status is still DOWNLOADING at startup was never
    actually finished -- the process that was writing its .part file is
    gone. Reset it to READY (not FAILED) so a worker resumes it from the
    existing .part file instead of it being silently stuck, dropped, or
    mistaken for complete.
    """
    with _lock, connect() as conn:
        conn.execute("""
        UPDATE movies SET download_status='READY',
        status=CASE WHEN status='DOWNLOADING' THEN 'ACCEPTED' ELSE status END,
        updated_at=?
        WHERE download_status='DOWNLOADING'
        """, (now_iso(),))


def create_job(kind, language, target=None, status="RUNNING", stats=None, message=None):
    now = now_iso()
    with _lock, connect() as conn:
        cur = conn.execute("""
        INSERT INTO jobs(kind,language,target,status,stats_json,message,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        """, (kind, language, target, status, json.dumps(stats or {}), message, now, now))
        return cur.lastrowid


def update_job(job_id, status=None, stats=None, message=None):
    fields, values = [], []
    if status is not None:
        fields.append("status=?"); values.append(status)
    if stats is not None:
        fields.append("stats_json=?"); values.append(json.dumps(stats))
    if message is not None:
        fields.append("message=?"); values.append(message)
    fields.append("updated_at=?"); values.append(now_iso())
    values.append(job_id)
    with _lock, connect() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=?", values)


def get_latest_job(kind, language, require_activity=False):
    """Return the most recent job, optionally skipping no-work job records."""
    with connect() as conn:
        rows = conn.execute("""
        SELECT * FROM jobs WHERE kind=? AND language=? ORDER BY id DESC
        """, (kind, language)).fetchall()

    for row in rows:
        job = dict(row)
        try:
            job["stats"] = json.loads(job.get("stats_json") or "{}")
        except json.JSONDecodeError:
            job["stats"] = {}
        if not require_activity or any((
            job["stats"].get("candidates_scanned", 0),
            job["stats"].get("api_requests", 0),
            job["stats"].get("network_retries", 0),
        )):
            return job
    return None


def get_latest_source_query(language):
    with connect() as conn:
        row = conn.execute("""
        SELECT source_query FROM movies
        WHERE language=? AND source_query IS NOT NULL AND source_query<>''
        ORDER BY updated_at DESC, id DESC LIMIT 1
        """, (language,)).fetchone()
        return row["source_query"] if row else ""


def reject_movie(movie_id, reason="USER_REJECTED"):
    with _lock, connect() as conn:
        row = conn.execute("SELECT language,video_id FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not row:
            return None
        conn.execute("""
        UPDATE movies SET status='REJECTED', rejection_reason=?,
        download_status=CASE WHEN download_status='DOWNLOADED' THEN download_status ELSE 'NOT_READY' END,
        updated_at=? WHERE id=?
        """, (reason, now_iso(), movie_id))
        return dict(row)


def restore_movie(movie_id):
    with _lock, connect() as conn:
        conn.execute("""
        UPDATE movies SET status='ACCEPTED', rejection_reason=NULL, updated_at=?
        WHERE id=?
        """, (now_iso(), movie_id))


def _normalise_bulk_ids(movie_ids):
    ids = []
    seen = set()
    for value in movie_ids or []:
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def bulk_reject_movies(language, movie_ids, reason="USER_REJECTED"):
    """Reject many movies for one language in a single transaction.

    IDs that don't exist, or belong to a different language, count as
    not_found; IDs already REJECTED are skipped rather than re-updated.
    """
    ids = _normalise_bulk_ids(movie_ids)
    result = {"requested": len(ids), "updated": 0, "skipped": 0, "not_found": 0}
    if not ids:
        return result
    with _lock, connect() as conn:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id,status FROM movies WHERE id IN ({placeholders}) AND language=?",
            [*ids, language]
        ).fetchall()
        result["not_found"] = len(ids) - len(rows)
        updatable_ids = [r["id"] for r in rows if r["status"] != "REJECTED"]
        result["skipped"] = len(rows) - len(updatable_ids)
        if updatable_ids:
            up_placeholders = ",".join("?" for _ in updatable_ids)
            cur = conn.execute(
                f"""UPDATE movies SET status='REJECTED', rejection_reason=?,
                download_status=CASE WHEN download_status='DOWNLOADED' THEN download_status ELSE 'NOT_READY' END,
                updated_at=? WHERE id IN ({up_placeholders})""",
                [reason, now_iso(), *updatable_ids]
            )
            result["updated"] = cur.rowcount
    return result


def bulk_restore_movies(language, movie_ids):
    """Restore many REJECTED movies for one language in a single transaction.

    Only updates existing rows, so restoring never creates duplicate rows.
    """
    ids = _normalise_bulk_ids(movie_ids)
    result = {"requested": len(ids), "updated": 0, "skipped": 0, "not_found": 0}
    if not ids:
        return result
    with _lock, connect() as conn:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id,status FROM movies WHERE id IN ({placeholders}) AND language=?",
            [*ids, language]
        ).fetchall()
        result["not_found"] = len(ids) - len(rows)
        updatable_ids = [r["id"] for r in rows if r["status"] == "REJECTED"]
        result["skipped"] = len(rows) - len(updatable_ids)
        if updatable_ids:
            up_placeholders = ",".join("?" for _ in updatable_ids)
            cur = conn.execute(
                f"""UPDATE movies SET status='ACCEPTED', rejection_reason=NULL, updated_at=?
                WHERE id IN ({up_placeholders})""",
                [now_iso(), *updatable_ids]
            )
            result["updated"] = cur.rowcount
    return result


def retry_download(movie_id):
    """Requeues a movie for download. Guarded to only affect a movie that
    isn't already downloading/queued/downloaded, so a stray retry call can
    never pull an in-flight or finished job back into the READY queue and
    cause a duplicate/duplicate-in-progress download.

    Accepts either a direct-HTTP movie (has a download_url) or a
    YouTube/YTDLP movie (no permanent download_url -- YtDlpDownloadBackend
    re-resolves streams from the stable video_id/youtube_url at download
    time, so no URL needs to be stored)."""
    with _lock, connect() as conn:
        conn.execute("""
        UPDATE movies SET download_status='READY', download_error=NULL, retry_count=0,
        status=CASE WHEN status='REJECTED' THEN status ELSE 'ACCEPTED' END, updated_at=?
        WHERE id=?
          AND ((download_backend='YTDLP' AND provider='youtube') OR (download_url IS NOT NULL AND download_url<>''))
          AND download_status NOT IN ('DOWNLOADING','QUEUED','DOWNLOADED')
        """, (now_iso(), movie_id))

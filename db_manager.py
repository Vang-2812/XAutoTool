import sqlite3
from datetime import datetime

DB_FILE = "history.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Main interaction log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            post_url TEXT,
            comment_content TEXT,
            status TEXT,
            views INTEGER,
            account_id TEXT DEFAULT 'default'
        )
    """)

    # Stores the 5 generated reply variants per post.
    # variant_index: 0 = first posted, 1-4 = queued for future use.
    # posted_at: NULL until actually posted.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS post_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_url TEXT NOT NULL,
            variant_index INTEGER NOT NULL,
            reply_text TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            posted_at DATETIME,
            post_views INTEGER,
            account_id TEXT DEFAULT 'default'
        )
    """)
    # Commit table creation FIRST so PRAGMA table_info can see the tables
    conn.commit()

    # --- Migration: add account_id column if missing (existing DBs) ---
    # IMPORTANT: Run migrations BEFORE creating indexes on account_id
    _migrate_add_account_id(cursor, "interactions")
    _migrate_add_account_id(cursor, "post_variants")

    # Index for fast lookups by post URL and account
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_variants_url
        ON post_variants (post_url)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_variants_account
        ON post_variants (account_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_interactions_account
        ON interactions (account_id)
    """)

    conn.commit()
    conn.close()


def _migrate_add_account_id(cursor, table_name):
    """Add account_id column to an existing table if it doesn't exist yet."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [col[1] for col in cursor.fetchall()]
    if "account_id" not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN account_id TEXT DEFAULT 'default'")


# ---------------------------------------------------------------------------
# Interaction log helpers
# ---------------------------------------------------------------------------

def log_interaction(post_url, comment_content, status, views, account_id="default"):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO interactions (timestamp, post_url, comment_content, status, views, account_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (datetime.now(), post_url, comment_content, status, views, account_id))
    conn.commit()
    conn.close()

def get_history(account_id=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if account_id:
        cursor.execute(
            "SELECT id, timestamp, post_url, comment_content, status, views, account_id "
            "FROM interactions WHERE account_id = ? ORDER BY timestamp DESC",
            (account_id,)
        )
    else:
        cursor.execute(
            "SELECT id, timestamp, post_url, comment_content, status, views, account_id "
            "FROM interactions ORDER BY timestamp DESC"
        )
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_posted_urls(account_id="default"):
    """Return a set of post URLs that have already been successfully replied to."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT post_url FROM interactions WHERE status = 'Success' AND account_id = ?",
        (account_id,)
    )
    urls = {row[0] for row in cursor.fetchall()}
    conn.close()
    return urls

# ---------------------------------------------------------------------------
# Reply-variant helpers
# ---------------------------------------------------------------------------

def save_reply_variants(post_url: str, variants: list[str], account_id: str = "default") -> None:
    """
    Persist all generated reply variants (up to 5) for a post.
    Existing variants for the same URL and account are replaced.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Clear any previous variants for this post + account (re-generation scenario)
    cursor.execute(
        "DELETE FROM post_variants WHERE post_url = ? AND account_id = ?",
        (post_url, account_id)
    )
    now = datetime.now()
    for idx, text in enumerate(variants[:5]):
        cursor.execute("""
            INSERT INTO post_variants (post_url, variant_index, reply_text, created_at, account_id)
            VALUES (?, ?, ?, ?, ?)
        """, (post_url, idx, text, now, account_id))
    conn.commit()
    conn.close()

def get_next_variant(post_url: str, account_id: str = "default") -> dict | None:
    """
    Return the lowest-index variant that has not yet been posted,
    or None if all variants have been posted / none exist.
    Returns a dict: {id, variant_index, reply_text}
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, variant_index, reply_text
        FROM post_variants
        WHERE post_url = ? AND posted_at IS NULL AND account_id = ?
        ORDER BY variant_index ASC
        LIMIT 1
    """, (post_url, account_id))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "variant_index": row[1], "reply_text": row[2]}
    return None

def mark_variant_posted(variant_id: int, post_views: int) -> None:
    """Record when a variant was posted and how many views the post had at that time."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE post_variants
        SET posted_at = ?, post_views = ?
        WHERE id = ?
    """, (datetime.now(), post_views, variant_id))
    conn.commit()
    conn.close()

def get_variants_for_url(post_url: str, account_id: str = None) -> list[dict]:
    """Return all stored variants for a post URL, ordered by variant_index."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if account_id:
        cursor.execute("""
            SELECT id, variant_index, reply_text, created_at, posted_at, post_views
            FROM post_variants
            WHERE post_url = ? AND account_id = ?
            ORDER BY variant_index ASC
        """, (post_url, account_id))
    else:
        cursor.execute("""
            SELECT id, variant_index, reply_text, created_at, posted_at, post_views
            FROM post_variants
            WHERE post_url = ?
            ORDER BY variant_index ASC
        """, (post_url,))
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": r[0], "variant_index": r[1], "reply_text": r[2],
            "created_at": r[3], "posted_at": r[4], "post_views": r[5]
        }
        for r in rows
    ]

def get_posts_with_pending_variants(account_id: str = "default") -> list[str]:
    """
    Return a list of distinct post URLs that have at least one un-posted variant
    (posted_at IS NULL), ordered by the variant's creation time (oldest first).
    These are the posts eligible for the 'Re-Reply to Post' strategy.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT post_url
        FROM post_variants
        WHERE posted_at IS NULL AND account_id = ?
        ORDER BY created_at ASC
    """, (account_id,))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_post_url_by_variant_text(reply_text: str, account_id: str = "default") -> str | None:
    """
    Search for a reply text in post_variants to find its parent post_url.
    This is used as a fallback when the UI doesn't provide a direct link.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Use LIKE for minor whitespace differences, or exact match
    cursor.execute("""
        SELECT post_url FROM post_variants 
        WHERE (reply_text = ? OR reply_text LIKE ?) AND account_id = ?
        LIMIT 1
    """, (reply_text, f"%{reply_text}%", account_id))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

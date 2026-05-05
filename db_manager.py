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
            views INTEGER
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
            post_views INTEGER
        )
    """)
    # Index for fast lookups by post URL
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_variants_url
        ON post_variants (post_url)
    """)

    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Interaction log helpers
# ---------------------------------------------------------------------------

def log_interaction(post_url, comment_content, status, views):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO interactions (timestamp, post_url, comment_content, status, views)
        VALUES (?, ?, ?, ?, ?)
    """, (datetime.now(), post_url, comment_content, status, views))
    conn.commit()
    conn.close()

def get_history():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM interactions ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_posted_urls():
    """Return a set of post URLs that have already been successfully replied to."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT post_url FROM interactions WHERE status = 'Success'")
    urls = {row[0] for row in cursor.fetchall()}
    conn.close()
    return urls

# ---------------------------------------------------------------------------
# Reply-variant helpers
# ---------------------------------------------------------------------------

def save_reply_variants(post_url: str, variants: list[str]) -> None:
    """
    Persist all generated reply variants (up to 5) for a post.
    Existing variants for the same URL are replaced.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Clear any previous variants for this post (re-generation scenario)
    cursor.execute("DELETE FROM post_variants WHERE post_url = ?", (post_url,))
    now = datetime.now()
    for idx, text in enumerate(variants[:5]):
        cursor.execute("""
            INSERT INTO post_variants (post_url, variant_index, reply_text, created_at)
            VALUES (?, ?, ?, ?)
        """, (post_url, idx, text, now))
    conn.commit()
    conn.close()

def get_next_variant(post_url: str) -> dict | None:
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
        WHERE post_url = ? AND posted_at IS NULL
        ORDER BY variant_index ASC
        LIMIT 1
    """, (post_url,))
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

def get_variants_for_url(post_url: str) -> list[dict]:
    """Return all stored variants for a post URL, ordered by variant_index."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
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

def get_posts_with_pending_variants() -> list[str]:
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
        WHERE posted_at IS NULL
        ORDER BY created_at ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]


from urllib.parse import urlparse
from sqlalchemy import text


def migrate_db(engine) -> None:
    """Add any new columns to existing SQLite tables (safe, idempotent) and backfill job title/icon."""
    migrations = [
        # (table_name, column_name, column_definition)
        ("scrapejob", "title",                     "TEXT"),
        ("scrapejob", "icon_url",                  "TEXT"),
        ("scrapejob", "scrape_mode",               "TEXT NOT NULL DEFAULT 'single'"),
        ("scrapejob", "total_pages",               "INTEGER"),
        ("scrapejob", "scraped_pages",             "INTEGER NOT NULL DEFAULT 0"),
        ("scrapejob", "log_path",                  "TEXT"),
        ("page",      "icon_url",                  "TEXT"),
        ("page",      "url_path",                  "TEXT"),
        ("page",      "url_depth",                 "INTEGER NOT NULL DEFAULT 0"),
        ("page",      "status",                    "TEXT NOT NULL DEFAULT 'completed'"),
        ("page",      "hreflang",                  "TEXT"),
        ("page",      "lastmod",                   "TEXT"),
        ("page",      "content_type",              "TEXT"),
        ("page",      "clean_url",                 "TEXT"),
        ("page",      "description",              "TEXT"),
        ("page",      "favicon_path",              "TEXT"),
        ("page",      "category_id",               "INTEGER REFERENCES category(id)"),
        ("page",      "types_id",                  "INTEGER REFERENCES types(id)"),
        ("page",      "genre_id",                  "INTEGER REFERENCES genre(id)"),
        ("page",      "categorization_source",     "TEXT NOT NULL DEFAULT 'dual_hybrid'"),
        ("page",      "categorization_confidence", "REAL NOT NULL DEFAULT 0.0"),
        ("page",      "notion_page_id",            "TEXT"),
        ("page",      "sync_status",               "TEXT NOT NULL DEFAULT 'unmerged'"),
        ("page",      "last_synced_at",            "TIMESTAMP"),
        ("category",  "section",                   "TEXT"),
        ("scrapejob", "category_id",               "INTEGER REFERENCES category(id)"),
        ("scrapejob", "types_id",                  "INTEGER REFERENCES types(id)"),
    ]
    with engine.connect() as conn:
        for table, column, definition in migrations:
            result = conn.execute(text(f"PRAGMA table_info({table})"))
            existing = [row[1] for row in result.fetchall()]
            if not existing:
                continue
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
                conn.commit()

        # Backfill scrapejob.title and scrapejob.icon_url from completed pages where missing
        jobs = conn.execute(
            text("SELECT id, target_url, title, icon_url FROM scrapejob WHERE title IS NULL OR icon_url IS NULL")
        ).fetchall()
        for job_row in jobs:
            j_id, j_target, j_title, j_icon = job_row
            norm_target = (j_target or "").rstrip("/")
            pages = conn.execute(
                text(
                    "SELECT url, title, icon_url, status FROM page "
                    "WHERE job_id = :jid ORDER BY CASE WHEN status = 'completed' THEN 0 ELSE 1 END, id ASC"
                ),
                {"jid": j_id},
            ).fetchall()

            best_title = j_title
            best_icon = j_icon

            for p_url, p_title, p_icon, _ in pages:
                is_real_title = (
                    p_title
                    and p_title.strip()
                    and p_title.strip() != "Untitled"
                    and not p_title.strip().startswith("/")
                    and not p_title.strip().startswith("http")
                )
                is_root_match = (p_url or "").rstrip("/") == norm_target

                if is_real_title and (not best_title or is_root_match):
                    best_title = p_title.strip()
                if p_icon and (not best_icon or is_root_match):
                    best_icon = p_icon
                if best_title and best_icon and is_root_match:
                    break

            if not best_icon and j_target:
                parsed = urlparse(j_target)
                domain = parsed.hostname or parsed.netloc
                if domain:
                    best_icon = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"

            if best_title != j_title or best_icon != j_icon:
                conn.execute(
                    text("UPDATE scrapejob SET title = :title, icon_url = :icon WHERE id = :jid"),
                    {"title": best_title, "icon": best_icon, "jid": j_id},
                )
        conn.commit()

        # Backfill scrapejob.category_id and scrapejob.types_id from pages where missing
        null_tax_jobs = conn.execute(
            text("SELECT id FROM scrapejob WHERE category_id IS NULL")
        ).fetchall()
        for (j_id,) in null_tax_jobs:
            page_tax = conn.execute(
                text(
                    "SELECT category_id, types_id FROM page "
                    "WHERE job_id = :jid AND category_id IS NOT NULL "
                    "ORDER BY id ASC LIMIT 1"
                ),
                {"jid": j_id},
            ).fetchone()
            if page_tax and page_tax[0]:
                conn.execute(
                    text("UPDATE scrapejob SET category_id = :cid, types_id = :tid WHERE id = :jid"),
                    {"cid": page_tax[0], "tid": page_tax[1], "jid": j_id},
                )
        conn.commit()


# Backward-compatible alias
_migrate_db = migrate_db

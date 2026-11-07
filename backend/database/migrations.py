from datetime import datetime, timezone
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
        ("category",  "workspace_id",              "INTEGER REFERENCES workspace(id)"),
        ("scrapejob", "category_id",               "INTEGER REFERENCES category(id)"),
        ("scrapejob", "types_id",                  "INTEGER REFERENCES types(id)"),
        ("scrapejob", "workspace_id",              "INTEGER REFERENCES workspace(id)"),
        ("page",      "workspace_id",              "INTEGER REFERENCES workspace(id)"),
        ("tag",       "workspace_id",              "INTEGER REFERENCES workspace(id)"),
        ("tag",       "section",                   "TEXT"),
    ]
    with engine.connect() as conn:
        # Ensure workspace, tag, and pagetaglink tables exist
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS workspace (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                icon TEXT DEFAULT '📁',
                color TEXT DEFAULT 'mauve',
                description TEXT,
                notion_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tag (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER REFERENCES workspace(id),
                name TEXT NOT NULL UNIQUE,
                slug TEXT NOT NULL UNIQUE,
                section TEXT,
                notion_id TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pagetaglink (
                page_id INTEGER REFERENCES page(id),
                tag_id INTEGER REFERENCES tag(id),
                PRIMARY KEY (page_id, tag_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS element (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER REFERENCES workspace(id),
                name TEXT NOT NULL UNIQUE,
                slug TEXT NOT NULL UNIQUE,
                icon TEXT DEFAULT 'clothes-button',
                section TEXT DEFAULT 'goods',
                notion_id TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pageelementlink (
                page_id INTEGER REFERENCES page(id),
                element_id INTEGER REFERENCES element(id),
                PRIMARY KEY (page_id, element_id)
            )
        """))
        conn.commit()

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

        # Seed canonical workspaces: fp, qfwfq, and full-site
        canonical_workspaces = [
            ("FP", "fp", "⚡", "mauve", "Services, creative direction, portfolio & tasks"),
            ("Qfwfq", "qfwfq", "🪐", "teal", "Goods, shops, items & commerce"),
            ("Full-Site", "full-site", "🌐", "sapphire", "Internal workspace for full-site scraping and content analysis (not linked to Notion)"),
        ]
        for w_name, w_slug, w_icon, w_color, w_desc in canonical_workspaces:
            existing_w = conn.execute(
                text("SELECT id FROM workspace WHERE slug = :slug"), {"slug": w_slug}
            ).fetchone()
            if not existing_w:
                conn.execute(
                    text(
                        "INSERT INTO workspace (name, slug, icon, color, description, created_at) "
                        "VALUES (:name, :slug, :icon, :color, :desc, :created_at)"
                    ),
                    {
                        "name": w_name,
                        "slug": w_slug,
                        "icon": w_icon,
                        "color": w_color,
                        "desc": w_desc,
                        "created_at": datetime.now(timezone.utc),
                    },
                )
        conn.commit()

        # Get workspace IDs
        fp_row = conn.execute(text("SELECT id FROM workspace WHERE slug = 'fp'")).fetchone()
        qfwfq_row = conn.execute(text("SELECT id FROM workspace WHERE slug = 'qfwfq'")).fetchone()
        fullsite_row = conn.execute(text("SELECT id FROM workspace WHERE slug = 'full-site'")).fetchone()
        fp_id = fp_row[0] if fp_row else None
        qfwfq_id = qfwfq_row[0] if qfwfq_row else None
        fullsite_id = fullsite_row[0] if fullsite_row else None

        # Check if category table exists before backfilling category-dependent relations
        cat_table_exists = (
            conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='category'")
            ).fetchone()
            is not None
        )

        # Backfill category workspace_id from section
        if cat_table_exists and qfwfq_id:
            conn.execute(
                text("UPDATE category SET workspace_id = :wid WHERE section = 'goods' AND workspace_id IS NULL"),
                {"wid": qfwfq_id},
            )
        if cat_table_exists and fp_id:
            conn.execute(
                text("UPDATE category SET workspace_id = :wid WHERE section = 'services' AND workspace_id IS NULL"),
                {"wid": fp_id},
            )
        conn.commit()

        # Backfill scrapejob.workspace_id and page.workspace_id from category.workspace_id
        if cat_table_exists:
            conn.execute(text("""
                UPDATE scrapejob
                SET workspace_id = (SELECT workspace_id FROM category WHERE category.id = scrapejob.category_id)
                WHERE category_id IS NOT NULL AND workspace_id IS NULL
            """))
            conn.execute(text("""
                UPDATE page
                SET workspace_id = (SELECT workspace_id FROM category WHERE category.id = page.category_id)
                WHERE category_id IS NOT NULL AND workspace_id IS NULL
            """))
            conn.commit()

        # Backfill page.workspace_id from scrapejob.workspace_id
        conn.execute(text("""
            UPDATE page
            SET workspace_id = (SELECT workspace_id FROM scrapejob WHERE scrapejob.id = page.job_id)
            WHERE workspace_id IS NULL AND job_id IN (SELECT id FROM scrapejob WHERE workspace_id IS NOT NULL)
        """))
        conn.commit()

        # Ensure existing full-site/sitemap scrapes route to Full-Site workspace
        if fullsite_id:
            conn.execute(
                text("UPDATE scrapejob SET workspace_id = :wid WHERE scrape_mode = 'sitemap'"),
                {"wid": fullsite_id},
            )
            conn.execute(
                text("""
                    UPDATE page SET workspace_id = :wid 
                    WHERE job_id IN (SELECT id FROM scrapejob WHERE scrape_mode = 'sitemap')
                """),
                {"wid": fullsite_id},
            )
            conn.commit()

        # Codify workspace Notion database associations (Full-Site is strictly unlinked)
        GOODS_NOTION_ID = "d46f6dbf-310c-4c4d-800e-813eeba04ab6"
        SERVICES_NOTION_ID = "d74d19f8-6945-4eb0-8d40-e879f3b1d571"
        if qfwfq_id:
            conn.execute(
                text("UPDATE workspace SET notion_id = :nid WHERE id = :wid AND (notion_id IS NULL OR notion_id = '')"),
                {"nid": GOODS_NOTION_ID, "wid": qfwfq_id},
            )
        if fp_id:
            conn.execute(
                text("UPDATE workspace SET notion_id = :nid WHERE id = :wid AND (notion_id IS NULL OR notion_id = '')"),
                {"nid": SERVICES_NOTION_ID, "wid": fp_id},
            )
        if fullsite_id:
            conn.execute(
                text("UPDATE workspace SET notion_id = NULL WHERE id = :wid"),
                {"wid": fullsite_id},
            )
        conn.commit()


# Backward-compatible alias
_migrate_db = migrate_db

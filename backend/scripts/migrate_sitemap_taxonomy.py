#!/usr/bin/env python3
"""
migrate_sitemap_taxonomy.py

Retroactively reclassifies and relocates folders that were mistakenly assigned
to a 'sitemap.xml' taxonomy category directory, moves orphaned jobs from root
into _downloads/, and updates SQLite database records.
"""

import os
import sys
import shutil
import sqlite3
import argparse
import csv
from urllib.parse import urlparse

# Ensure backend directory is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)

from modules.scrapers.sitemap_fetcher import determine_content_type
from modules.config import DOWNLOADS_DIR

DB_PATH = os.path.join(PROJECT_ROOT, "soupre.db")


def extract_url_from_metadata(page_dir: str) -> str:
    """Read URL from metadata.csv if present."""
    meta_path = os.path.join(page_dir, "metadata.csv")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if row and row[0].strip().lower() == "url" and len(row) > 1:
                        return row[1].strip()
        except Exception as e:
            print(f"Warning reading {meta_path}: {e}")
    return ""


def migrate_root_jobs(dry_run: bool = False):
    """Move any job_* directories located in the repository root into _downloads/."""
    for item in os.listdir(PROJECT_ROOT):
        src_path = os.path.join(PROJECT_ROOT, item)
        if os.path.isdir(src_path) and item.startswith("job_"):
            dest_path = os.path.join(DOWNLOADS_DIR, item)
            print(f"[Root Migration] Found orphaned job directory: {src_path} -> {dest_path}")
            if not dry_run:
                if os.path.exists(dest_path):
                    print(f"  Target {dest_path} already exists! Merging contents...")
                    for sub in os.listdir(src_path):
                        s_sub = os.path.join(src_path, sub)
                        d_sub = os.path.join(dest_path, sub)
                        if not os.path.exists(d_sub):
                            shutil.move(s_sub, d_sub)
                    shutil.rmtree(src_path)
                else:
                    shutil.move(src_path, dest_path)
                print(f"  Successfully moved to {dest_path}")


def migrate_disk_folders(dry_run: bool = False) -> int:
    """Find all {lang}/sitemap.xml/ directories and reclassify each child page folder."""
    moved_count = 0
    if not os.path.exists(DOWNLOADS_DIR):
        print(f"Downloads dir {DOWNLOADS_DIR} not found.")
        return 0

    for job_name in sorted(os.listdir(DOWNLOADS_DIR)):
        job_dir = os.path.join(DOWNLOADS_DIR, job_name)
        if not os.path.isdir(job_dir) or not job_name.startswith("job_"):
            continue

        for lang in os.listdir(job_dir):
            lang_dir = os.path.join(job_dir, lang)
            if not os.path.isdir(lang_dir) or lang in ("_images", "images"):
                continue

            sitemap_xml_dir = os.path.join(lang_dir, "sitemap.xml")
            if not os.path.isdir(sitemap_xml_dir):
                continue

            print(f"\n[Disk Migration] Processing {sitemap_xml_dir}...")
            page_folders = [f for f in os.listdir(sitemap_xml_dir) if os.path.isdir(os.path.join(sitemap_xml_dir, f))]
            for page_folder in page_folders:
                page_path = os.path.join(sitemap_xml_dir, page_folder)
                url = extract_url_from_metadata(page_path)
                new_type = determine_content_type(url) if url else "pages"
                # Safety guard
                if "sitemap" in new_type.lower() or new_type.lower().endswith(".xml"):
                    new_type = "pages"

                target_type_dir = os.path.join(lang_dir, new_type)
                target_page_dir = os.path.join(target_type_dir, page_folder)

                print(f"  Move: {page_folder} ({url or 'no URL'}) -> {new_type}/")
                if not dry_run:
                    os.makedirs(target_type_dir, exist_ok=True)
                    if os.path.exists(target_page_dir):
                        print(f"    Target directory {target_page_dir} already exists, merging...")
                        for file_name in os.listdir(page_path):
                            s_file = os.path.join(page_path, file_name)
                            d_file = os.path.join(target_page_dir, file_name)
                            if not os.path.exists(d_file):
                                shutil.move(s_file, d_file)
                        shutil.rmtree(page_path)
                    else:
                        shutil.move(page_path, target_page_dir)
                moved_count += 1

            if not dry_run:
                # Remove empty sitemap.xml folder if empty
                remaining = os.listdir(sitemap_xml_dir)
                if not remaining:
                    os.rmdir(sitemap_xml_dir)
                    print(f"  Pruned empty directory: {sitemap_xml_dir}")
                else:
                    print(f"  Warning: sitemap.xml dir still has files: {remaining}")

    return moved_count


def migrate_database_records(dry_run: bool = False) -> int:
    """Update SQLite page table records having content_type = 'sitemap.xml'."""
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found.")
        return 0

    if not dry_run:
        backup_path = f"{DB_PATH}.bak_taxonomy_migration"
        shutil.copyfile(DB_PATH, backup_path)
        print(f"[DB Migration] Created database backup at {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT id, url, markdown_path, screenshot_path FROM page WHERE content_type = 'sitemap.xml'")
    rows = cursor.fetchall()
    print(f"\n[DB Migration] Found {len(rows)} records with content_type = 'sitemap.xml'")

    updated_count = 0
    for page_id, url, md_path, shot_path in rows:
        new_type = determine_content_type(url)
        if "sitemap" in new_type.lower() or new_type.lower().endswith(".xml"):
            new_type = "pages"

        new_md_path = md_path.replace("/sitemap.xml/", f"/{new_type}/") if md_path else md_path
        new_shot_path = shot_path.replace("/sitemap.xml/", f"/{new_type}/") if shot_path else shot_path

        if not dry_run:
            cursor.execute(
                "UPDATE page SET content_type = ?, markdown_path = ?, screenshot_path = ? WHERE id = ?",
                (new_type, new_md_path, new_shot_path, page_id)
            )
        updated_count += 1

    if not dry_run:
        conn.commit()
    conn.close()
    return updated_count


def main():
    parser = argparse.ArgumentParser(description="Migrate corrupted sitemap.xml taxonomy directories and database records.")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without modifying files or database.")
    args = parser.parse_args()

    mode_str = "[DRY RUN] " if args.dry_run else ""
    print(f"=== {mode_str}Starting Sitemap Taxonomy Migration ===")

    migrate_root_jobs(dry_run=args.dry_run)
    moved_dirs = migrate_disk_folders(dry_run=args.dry_run)
    updated_records = migrate_database_records(dry_run=args.dry_run)

    print("\n=== Migration Summary ===")
    print(f"Page directories moved: {moved_dirs}")
    print(f"Database rows updated:  {updated_records}")
    print("Done!")


if __name__ == "__main__":
    main()

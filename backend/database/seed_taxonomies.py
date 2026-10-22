"""
Seed taxonomy tables (Category, Types, Tag, Genre) from imported Notion _taxonomies.json files.
Fully idempotent: can be executed repeatedly without duplicating records.
"""

import json
import logging
import os
import re
from pathlib import Path
from sqlmodel import Session, select

from database.models import Category, Types, Tag, Genre

log = logging.getLogger(__name__)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "default"


# Default Category mappings for imported Types
DEFAULT_TYPE_CATEGORY_MAP = {
    # Services types -> Technology
    "application": "Technology",
    "service": "Technology",
    "tools": "Technology",
    "plugin": "Technology",
    "model": "Technology",
    "multy": "Technology",
    "utils": "Technology",
    # Creatives types -> Creativity / Artists / Management
    "art": "Creativity",
    "artist": "Artists",
    "blogger": "Content Creation",
    "business": "Management",
    "chain": "Management",
    "creator": "Content Creation",
    "entrepreneur": "Management",
    "guest": "Traveller",
    "hotel": "Traveller",
    "magazine": "Writing",
    "network": "Management",
    "org": "Management",
    "personal": "Creativity",
    "photo-video": "Artists",
    "shop": "Goods & Commerce",
    "solo": "Creativity",
    "travel-service": "Traveller",
    # Goods types -> Goods & Commerce
    "accessories": "Goods & Commerce",
    "apparel": "Goods & Commerce",
    "appliances": "Goods & Commerce",
    "automation": "Technology",
    "bags-case": "Goods & Commerce",
    "bathroom": "Goods & Commerce",
    "beverage": "Goods & Commerce",
    "body": "Goods & Commerce",
    "books": "Writing",
    "cleaning": "Goods & Commerce",
    "doors": "Goods & Commerce",
    "electricity": "Technology",
    "food": "Goods & Commerce",
    "furniture": "Goods & Commerce",
    "gear": "Goods & Commerce",
    "health": "Goods & Commerce",
    "jewels": "Goods & Commerce",
    "kitchen": "Goods & Commerce",
    "lighting": "Design",
    "materials": "Design",
    "mechanical": "Technology",
    "music": "Artists",
    "printing": "Design",
    "replacement": "Goods & Commerce",
    "security": "Technology",
    "smoking": "Goods & Commerce",
    "stationary": "Design",
    "storage": "Technology",
    "sweets": "Goods & Commerce",
    "tech": "Technology",
    "tiles": "Design",
    "tool": "Technology",
    "toys": "Goods & Commerce",
}


def seed_taxonomies(session: Session, dbs_dir: Path | None = None) -> dict[str, int]:
    """Seed Category, Types, Tag, Genre tables from _taxonomies.json. Returns counts of newly created items."""
    if dbs_dir is None:
        dbs_dir = Path(__file__).resolve().parent.parent / "modules" / "_dbs"

    counts = {"categories": 0, "types": 0, "tags": 0, "genres": 0}

    # 1. Collect raw taxonomy values from json files
    services_tax_path = dbs_dir / "fp/work/services/_schema/_dbs/_taxonomies.json"
    creatives_tax_path = dbs_dir / "fp/profolio/creatives/_schema/_dbs/_taxonomies.json"
    shops_tax_path = dbs_dir / "qfwfq/goods/shops/_schema/_dbs/_taxonomies.json"
    categories_tax_path = dbs_dir / "qfwfq/goods/categories/_schema/_dbs/_taxonomies.json"

    raw_categories: list[str] = []
    raw_types: list[str] = []
    raw_tags: list[str] = []
    raw_genres: list[str] = []

    if services_tax_path.is_file():
        with open(services_tax_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            raw_categories.extend(data.get("Roles", []))
            raw_types.extend(data.get("Types", []))
            raw_tags.extend(data.get("Tags", []))

    if creatives_tax_path.is_file():
        with open(creatives_tax_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            raw_types.extend(data.get("Type", []))
            raw_genres.extend(data.get("Fields", []))
            raw_tags.extend(data.get("Tags", []))

    if categories_tax_path.is_file():
        with open(categories_tax_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            raw_categories.extend(data.get("Categories", []))

    if shops_tax_path.is_file():
        with open(shops_tax_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            raw_types.extend(data.get("Type", []))
            raw_tags.extend(data.get("Tags", []))

    # Add standard base category for goods
    raw_categories.append("Goods & Commerce")

    # 2. Seed Categories
    category_map: dict[str, Category] = {}
    for cat_name in raw_categories:
        cat_name = cat_name.strip()
        if not cat_name:
            continue
        slug = slugify(cat_name)
        existing = session.exec(select(Category).where(Category.slug == slug)).first()
        if not existing:
            cat_obj = Category(name=cat_name, slug=slug)
            session.add(cat_obj)
            session.flush()
            category_map[slug] = cat_obj
            category_map[cat_name.lower()] = cat_obj
            counts["categories"] += 1
        else:
            category_map[slug] = existing
            category_map[cat_name.lower()] = existing

    # Fallback default category
    default_cat = category_map.get("technology") or next(iter(category_map.values()), None)

    # 3. Seed Types
    for type_name in raw_types:
        type_name = type_name.strip()
        if not type_name:
            continue
        type_slug = slugify(type_name)
        existing = session.exec(select(Types).where(Types.slug == type_slug)).first()
        if not existing:
            parent_cat_name = DEFAULT_TYPE_CATEGORY_MAP.get(type_slug, "Technology")
            parent_cat = category_map.get(slugify(parent_cat_name), default_cat)
            if parent_cat and parent_cat.id is not None:
                type_obj = Types(
                    category_id=parent_cat.id,
                    name=type_name,
                    slug=type_slug,
                )
                session.add(type_obj)
                session.flush()
                counts["types"] += 1

    # 4. Seed Tags
    seen_tag_slugs = set()
    for tag_name in raw_tags:
        tag_name = tag_name.strip()
        if not tag_name:
            continue
        tag_slug = slugify(tag_name)
        if tag_slug in seen_tag_slugs:
            continue
        seen_tag_slugs.add(tag_slug)

        existing = session.exec(select(Tag).where(Tag.slug == tag_slug)).first()
        if not existing:
            tag_obj = Tag(name=tag_name, slug=tag_slug)
            session.add(tag_obj)
            session.flush()
            counts["tags"] += 1

    # 5. Seed Genre
    seen_genre_slugs = set()
    for genre_name in raw_genres:
        genre_name = genre_name.strip()
        if not genre_name:
            continue
        genre_slug = slugify(genre_name)
        if genre_slug in seen_genre_slugs:
            continue
        seen_genre_slugs.add(genre_slug)

        existing = session.exec(select(Genre).where(Genre.slug == genre_slug)).first()
        if not existing:
            genre_obj = Genre(name=genre_name, slug=genre_slug)
            session.add(genre_obj)
            session.flush()
            counts["genres"] += 1

    session.commit()
    log.info(
        f"Taxonomy seeding complete: +{counts['categories']} Categories, "
        f"+{counts['types']} Types, +{counts['tags']} Tags, +{counts['genres']} Genres"
    )
    return counts


if __name__ == "__main__":
    from sqlmodel import create_engine
    sqlite_url = os.getenv("SQLITE_URL", "sqlite:////Users/admin/webapps/soupre/soupre.db")
    engine = create_engine(sqlite_url)
    with Session(engine) as sess:
        res = seed_taxonomies(sess)
        print("Seeded:", res)

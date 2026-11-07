"""
Seed taxonomy tables (Category, Types, Tag, Genre) from imported Notion _taxonomies.json files.
Fully idempotent: can be executed repeatedly without duplicating records.
Reflects Notion:
# GOODS / Categories DB (16 categories, section='goods')
# Services / Roles DB (16 categories, section='services')
"""

import json
import logging
import os
import re
from pathlib import Path
from sqlmodel import Session, select, text

from database.models import Category, Types, Tag, Genre, Workspace, Element

log = logging.getLogger(__name__)

GOODS_NOTION_ID = "d46f6dbf-310c-4c4d-800e-813eeba04ab6"
SERVICES_NOTION_ID = "d74d19f8-6945-4eb0-8d40-e879f3b1d571"

CANONICAL_GOODS_CATEGORIES = [
    ("Snowboarding", "snowboarding", "Snowboards, bindings, winter sports equipment, outerwear, alpine apparel."),
    ("Cycling", "cycling", "Bicycles, cycling apparel, road bikes, gravel, gear, bike components, helmets."),
    ("Home", "home", "Home furnishings, interior decor, kitchenware, bathroom, furniture, appliances, lighting."),
    ("Street", "street", "Streetwear, casual fashion, denim, jeans, sneakers, sunglasses, eyewear, urban apparel, accessories."),
    ("Hiking", "hiking", "Hiking gear, trekking equipment, outdoor apparel, trail footwear, backpacks, outdoor exploration."),
    ("Yoga", "yoga", "Yoga mats, activewear, pilates apparel, meditation accessories, athletic leggings, mindful movement."),
    ("Other", "other", "Miscellaneous consumer products, general accessories, replacement parts, smoking, specialty."),
    ("Travel", "travel", "Luggage, travel bags, travel accessories, passport holders, packing cubes, weekenders."),
    ("Beverage", "beverage", "Coffee, specialty tea, wine, alcoholic beverages, craft drinks, roasteries, glassware."),
    ("Entertainment", "entertainment", "Toys, board games, vinyl records, entertainment collectibles, pop culture items."),
    ("Creativity", "goods-creativity", "Art supplies, sketchbooks, crafting materials, specialty paper, artist stationery."),
    ("Car", "car", "Automotive accessories, car parts, wheels, vehicle care, automotive tools, driving gear."),
    ("Food", "food", "Gourmet food, artisan snacks, sweets, chocolates, pantry staples, specialty ingredients."),
    ("Office", "office", "Office supplies, desk organization, stationery, notebooks, pens, workspace accessories."),
    ("Tech", "tech", "Consumer electronics, gadgets, computer hardware, audio equipment, chargers, tech accessories."),
    ("Wellness", "wellness", "Personal care, skincare, grooming, bath and body, cosmetics, clean beauty, wellness products."),
]

CANONICAL_SERVICES_CATEGORIES = [
    ("Traveller", "traveller", "Hotels, luxury resorts, hospitality stays, travel agencies, tour guides, vacation booking."),
    ("Joker", "joker", "Multipurpose utility tools, calculators, format converters, web helpers, generators."),
    ("Builder", "builder", "Website builders, no-code platforms, web scaffolds, page builders, CMS platforms."),
    ("Economics", "economics", "Economic research, market analysis, macroeconomic indicators, economic data, monetary policy."),
    ("Artists", "artists", "Visual artists, painters, sculptors, fine art galleries, independent musicians, illustrators."),
    ("Technology", "technology", "Software platforms, developer infrastructure, APIs, cloud services, software engineering, databases."),
    ("Assistant", "assistant", "AI assistants, copilots, virtual agents, intelligent chatbot services, automated task helpers."),
    ("Legal", "legal", "Legal services, law firms, legal compliance, attorneys, privacy law, regulatory advisory."),
    ("Writing", "writing", "Publications, journalism, digital magazines, editorial blogs, essays, technical writing."),
    ("Creativity", "services-creativity", "Creative agencies, design innovation, brand studios, visual direction, creative consulting."),
    ("Content Creation", "content-creation", "Content creators, podcasts, video streaming, YouTube channels, digital media producers."),
    ("Design", "design", "UI/UX design studios, brand identity, graphic design, typography, digital aesthetics, visual design."),
    ("Financial", "financial", "Banking, investment management, cryptocurrency, fintech platforms, wealth management."),
    ("Marketing", "marketing", "Digital marketing agencies, SEO, growth strategy, advertising campaigns, public relations."),
    ("Development", "development", "Web development agencies, custom software engineering studios, mobile app development."),
    ("Management", "management", "Business operations, enterprise management, corporate consulting, business networks."),
]


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "default"


# Default Category mappings for imported Types
DEFAULT_TYPE_CATEGORY_MAP = {
    # Services types
    "application": "technology",
    "model": "technology",
    "multy": "technology",
    "plugin": "technology",
    "service": "technology",
    "tools": "technology",
    "utils": "technology",
    "art": "artists",
    "artist": "artists",
    "photo-video": "artists",
    "music": "artists",
    "blogger": "content-creation",
    "creator": "content-creation",
    "business": "management",
    "chain": "management",
    "entrepreneur": "management",
    "network": "management",
    "org": "management",
    "guest": "traveller",
    "hotel": "traveller",
    "travel-service": "traveller",
    "magazine": "writing",
    "books": "writing",
    "personal": "services-creativity",
    "solo": "design",

    # Goods types
    "apparel": "street",
    "accessories": "street",
    "jewels": "street",
    "bags-case": "street",
    "shop": "street",
    "appliances": "home",
    "bathroom": "home",
    "cleaning": "home",
    "doors": "home",
    "furniture": "home",
    "kitchen": "home",
    "lighting": "home",
    "tiles": "home",
    "tech": "tech",
    "automation": "tech",
    "electricity": "tech",
    "mechanical": "tech",
    "security": "tech",
    "storage": "tech",
    "tool": "tech",
    "body": "wellness",
    "health": "wellness",
    "food": "food",
    "sweets": "food",
    "beverage": "beverage",
    "toys": "entertainment",
    "stationary": "office",
    "printing": "office",
    "materials": "goods-creativity",
    "smoking": "other",
    "replacement": "other",
    "gear": "hiking",
}


def seed_taxonomies(session: Session, dbs_dir: Path | None = None) -> dict[str, int]:
    """Seed Category, Types, Tag, Genre tables from _taxonomies.json. Returns counts of newly created items."""
    if dbs_dir is None:
        dbs_dir = Path(__file__).resolve().parent.parent / "modules" / "_dbs"

    counts = {"categories": 0, "types": 0, "tags": 0, "genres": 0, "elements": 0}

    # 1. Collect candidate directories for _taxonomies.json
    candidate_dirs = [dbs_dir]
    no_db_man_dbs = Path("/Users/admin/webapps/no-db-man/modules/_dbs")
    if no_db_man_dbs.is_dir() and no_db_man_dbs.resolve() != dbs_dir.resolve():
        candidate_dirs.append(no_db_man_dbs)

    raw_types: list[str] = []
    raw_genres: list[str] = []
    raw_elements: list[str] = []
    # Store tuples: (tag_name, ws_slug, section)
    tag_entries: list[tuple[str, str | None, str | None]] = []

    seen_tax_files = set()
    for c_dir in candidate_dirs:
        if not c_dir.exists():
            continue
        for tax_path in sorted(c_dir.rglob("_taxonomies.json")):
            resolved_p = tax_path.resolve()
            if resolved_p in seen_tax_files:
                continue
            seen_tax_files.add(resolved_p)

            parts = [p.lower() for p in tax_path.parts]
            is_fp = "fp" in parts
            is_qfwfq = "qfwfq" in parts
            ws_slug = "fp" if is_fp else ("qfwfq" if is_qfwfq else None)
            sec = "services" if is_fp else ("goods" if is_qfwfq else "general")

            try:
                with open(tax_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        continue

                    for t_key in ("Types", "Type", "types", "type"):
                        t_vals = data.get(t_key, [])
                        if isinstance(t_vals, list):
                            raw_types.extend([str(v) for v in t_vals if v])

                    for g_key in ("Fields", "fields", "Genres", "genres"):
                        g_vals = data.get(g_key, [])
                        if isinstance(g_vals, list):
                            raw_genres.extend([str(v) for v in g_vals if v])

                    for tag_key in ("Tags", "Tag", "tags", "tag"):
                        tag_vals = data.get(tag_key, [])
                        if isinstance(tag_vals, list):
                            for v in tag_vals:
                                if isinstance(v, str) and v.strip():
                                    tag_entries.append((v.strip(), ws_slug, sec))
                        elif isinstance(tag_vals, dict):
                            for v in tag_vals.keys():
                                if isinstance(v, str) and v.strip():
                                    tag_entries.append((v.strip(), ws_slug, sec))

                    for elem_key in ("Elements", "Element", "elements", "element"):
                        elem_vals = data.get(elem_key, [])
                        if isinstance(elem_vals, list):
                            for v in elem_vals:
                                if isinstance(v, str) and v.strip():
                                    raw_elements.append(v.strip())
                        elif isinstance(elem_vals, dict):
                            for v in elem_vals.keys():
                                if isinstance(v, str) and v.strip():
                                    raw_elements.append(v.strip())
            except Exception as e:
                log.debug(f"Failed parsing {tax_path}: {e}")

    # 2. Ensure Canonical Workspaces
    canonical_workspaces = [
        ("FP", "fp", "⚡", "mauve", "Services, creative direction, portfolio & tasks"),
        ("Qfwfq", "qfwfq", "🪐", "teal", "Goods, shops, items & commerce"),
        ("Full-Site", "full-site", "🌐", "sapphire", "Internal workspace for full-site scraping and content analysis (not linked to Notion)"),
    ]
    for w_name, w_slug, w_icon, w_color, w_desc in canonical_workspaces:
        existing_w = session.exec(select(Workspace).where(Workspace.slug == w_slug)).first()
        if not existing_w:
            session.add(
                Workspace(
                    name=w_name,
                    slug=w_slug,
                    icon=w_icon,
                    color=w_color,
                    description=w_desc,
                    notion_id=None,
                )
            )
            session.flush()

    # 3. Seed Canonical Categories (16 Goods + 16 Services)
    category_map: dict[str, Category] = {}

    fp_ws = session.exec(select(Workspace).where(Workspace.slug == "fp")).first()
    qfwfq_ws = session.exec(select(Workspace).where(Workspace.slug == "qfwfq")).first()
    fp_id = fp_ws.id if fp_ws else None
    qfwfq_id = qfwfq_ws.id if qfwfq_ws else None

    # Seed Services Categories
    for cat_name, slug, desc in CANONICAL_SERVICES_CATEGORIES:
        existing = session.exec(select(Category).where(
            (Category.section == "services") & (Category.slug == slug)
        )).first()
        if not existing:
            cat_obj = Category(
                name=cat_name,
                slug=slug,
                section="services",
                workspace_id=fp_id,
                notion_id=SERVICES_NOTION_ID,
                description=desc,
            )
            session.add(cat_obj)
            session.flush()
            category_map[slug] = cat_obj
            counts["categories"] += 1
        else:
            if existing.workspace_id is None and fp_id:
                existing.workspace_id = fp_id
                session.add(existing)
            category_map[slug] = existing

    # Seed Goods Categories
    for cat_name, slug, desc in CANONICAL_GOODS_CATEGORIES:
        existing = session.exec(select(Category).where(
            (Category.section == "goods") & (Category.slug == slug)
        )).first()
        if not existing:
            cat_obj = Category(
                name=cat_name,
                slug=slug,
                section="goods",
                workspace_id=qfwfq_id,
                notion_id=GOODS_NOTION_ID,
                description=desc,
            )
            session.add(cat_obj)
            session.flush()
            category_map[slug] = cat_obj
            counts["categories"] += 1
        else:
            if existing.workspace_id is None and qfwfq_id:
                existing.workspace_id = qfwfq_id
                session.add(existing)
            category_map[slug] = existing

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
            parent_cat_slug = DEFAULT_TYPE_CATEGORY_MAP.get(type_slug, "technology")
            parent_cat = category_map.get(parent_cat_slug, default_cat)
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
    for tag_name, ws_slug, sec in tag_entries:
        tag_name = tag_name.strip()
        if not tag_name:
            continue
        tag_slug = slugify(tag_name)
        if tag_slug in seen_tag_slugs:
            continue
        seen_tag_slugs.add(tag_slug)

        ws_id = fp_id if ws_slug == "fp" else (qfwfq_id if ws_slug == "qfwfq" else None)
        existing = session.exec(select(Tag).where(Tag.slug == tag_slug)).first()
        if not existing:
            tag_obj = Tag(
                name=tag_name,
                slug=tag_slug,
                workspace_id=ws_id,
                section=sec,
            )
            session.add(tag_obj)
            session.flush()
            counts["tags"] += 1
        else:
            updated = False
            if existing.workspace_id is None and ws_id is not None:
                existing.workspace_id = ws_id
                updated = True
            if existing.section is None and sec is not None:
                existing.section = sec
                updated = True
            if updated:
                session.add(existing)

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

    # 6. Seed Elements (scoped to Qfwfq / GOODS)
    icons_path = Path(__file__).resolve().parent.parent / "modules" / "notion" / "element_icons.json"
    element_icons_map: dict[str, str] = {}
    if icons_path.is_file():
        try:
            element_icons_map = json.loads(icons_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.debug(f"Failed to load element icons map: {e}")

    # Combine raw_elements from _taxonomies.json and keys from element_icons_map
    all_element_names = list(raw_elements) + list(element_icons_map.keys())
    seen_element_slugs = set()
    for el_name in all_element_names:
        el_name = el_name.strip()
        if not el_name:
            continue
        el_slug = slugify(el_name)
        if el_slug in seen_element_slugs:
            continue
        seen_element_slugs.add(el_slug)

        el_icon = element_icons_map.get(el_name, "clothes-button")
        existing_el = session.exec(select(Element).where(Element.slug == el_slug)).first()
        if not existing_el:
            element_obj = Element(
                name=el_name,
                slug=el_slug,
                icon=el_icon,
                workspace_id=qfwfq_id,
                section="goods",
            )
            session.add(element_obj)
            session.flush()
            counts["elements"] += 1
        else:
            updated = False
            if existing_el.workspace_id is None and qfwfq_id is not None:
                existing_el.workspace_id = qfwfq_id
                updated = True
            if existing_el.icon is None and el_icon:
                existing_el.icon = el_icon
                updated = True
            if updated:
                session.add(existing_el)

    session.commit()
    log.info(
        f"Taxonomy seeding complete: +{counts['categories']} Categories, "
        f"+{counts['types']} Types, +{counts['tags']} Tags, +{counts['genres']} Genres, "
        f"+{counts['elements']} Elements"
    )
    return counts


if __name__ == "__main__":
    from sqlmodel import create_engine
    sqlite_url = os.getenv("SQLITE_URL", "sqlite:////Users/admin/webapps/soupre/soupre.db")
    engine = create_engine(sqlite_url)
    with Session(engine) as sess:
        res = seed_taxonomies(sess)
        print("Seeded:", res)

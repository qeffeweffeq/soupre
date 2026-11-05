"""
Reorganize soupre.db taxonomies to perfectly reflect Notion categories:
# GOODS / Categories DB (16 items)
# Services / Roles DB (16 items)
and categorize all existing scraped pages into the most relevant category.
"""

import sys
import os
from pathlib import Path

# Add backend to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from sqlmodel import Session, create_engine, select, text
from database.models import Category, Types, Page, ScrapeJob

DB_PATH = Path("/Users/admin/webapps/soupre/soupre.db")
engine = create_engine(f"sqlite:///{DB_PATH}")

GOODS_NOTION_ID = "d46f6dbf-310c-4c4d-800e-813eeba04ab6"
SERVICES_NOTION_ID = "d74d19f8-6945-4eb0-8d40-e879f3b1d571"

GOODS_CATEGORIES = [
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

SERVICES_CATEGORIES = [
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

TYPE_PARENT_MAP = {
    # Services types
    "application": "Technology",
    "model": "Technology",
    "multy": "Technology",
    "plugin": "Technology",
    "service": "Technology",
    "tools": "Technology",
    "utils": "Technology",
    "art": "Artists",
    "artist": "Artists",
    "photo-video": "Artists",
    "music": "Artists",
    "blogger": "Content Creation",
    "creator": "Content Creation",
    "business": "Management",
    "chain": "Management",
    "entrepreneur": "Management",
    "network": "Management",
    "org": "Management",
    "guest": "Traveller",
    "hotel": "Traveller",
    "travel-service": "Traveller",
    "magazine": "Writing",
    "books": "Writing",
    "personal": "services-creativity",
    "solo": "Design",

    # Goods types
    "apparel": "Street",
    "accessories": "Street",
    "jewels": "Street",
    "bags-case": "Street",
    "shop": "Street",
    "appliances": "Home",
    "bathroom": "Home",
    "cleaning": "Home",
    "doors": "Home",
    "furniture": "Home",
    "kitchen": "Home",
    "lighting": "Home",
    "tiles": "Home",
    "tech": "Tech",
    "automation": "Tech",
    "electricity": "Tech",
    "mechanical": "Tech",
    "security": "Tech",
    "storage": "Tech",
    "tool": "Tech",
    "body": "Wellness",
    "health": "Wellness",
    "food": "Food",
    "sweets": "Food",
    "beverage": "Beverage",
    "toys": "Entertainment",
    "stationary": "Office",
    "printing": "Office",
    "materials": "goods-creativity",
    "smoking": "Other",
    "replacement": "Other",
    "gear": "Hiking",
}


def main():
    print("Beginning taxonomy and page reorganization...")
    with Session(engine) as session:
        # 1. Ensure table column and indexes
        try:
            session.exec(text("ALTER TABLE category ADD COLUMN section VARCHAR"))
            session.commit()
        except Exception:
            pass

        session.exec(text("DROP INDEX IF EXISTS ix_category_name"))
        session.exec(text("DROP INDEX IF EXISTS ix_category_slug"))
        session.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_category_section_slug ON category (section, slug)"))
        session.exec(text("CREATE INDEX IF NOT EXISTS ix_category_name ON category (name)"))
        session.exec(text("CREATE INDEX IF NOT EXISTS ix_category_slug ON category (slug)"))
        session.exec(text("CREATE INDEX IF NOT EXISTS ix_category_section ON category (section)"))
        session.commit()

        # 2. Seed / Update Services Categories
        services_map = {}
        for name, slug, desc in SERVICES_CATEGORIES:
            cat = session.exec(select(Category).where(
                (Category.section == "services") & ((Category.slug == slug) | (Category.name == name))
            )).first()

            if not cat:
                # Check if existing category without section matches
                cat = session.exec(select(Category).where(
                    (Category.name == name) & (Category.section.is_(None))
                )).first()

            if cat:
                cat.name = name
                cat.slug = slug
                cat.section = "services"
                cat.notion_id = SERVICES_NOTION_ID
                cat.description = desc
            else:
                cat = Category(
                    name=name,
                    slug=slug,
                    section="services",
                    notion_id=SERVICES_NOTION_ID,
                    description=desc
                )
            session.add(cat)
            session.flush()
            services_map[name] = cat
            services_map[slug] = cat

        print(f"Configured {len(SERVICES_CATEGORIES)} Services / Roles categories.")

        # 3. Seed / Update GOODS Categories
        goods_map = {}
        for name, slug, desc in GOODS_CATEGORIES:
            cat = session.exec(select(Category).where(
                (Category.section == "goods") & ((Category.slug == slug) | (Category.name == name))
            )).first()

            if not cat:
                # Check if existing category without section matches
                cat = session.exec(select(Category).where(
                    (Category.name == name) & (Category.section.is_(None))
                )).first()

            if cat:
                cat.name = name
                cat.slug = slug
                cat.section = "goods"
                cat.notion_id = GOODS_NOTION_ID
                cat.description = desc
            else:
                cat = Category(
                    name=name,
                    slug=slug,
                    section="goods",
                    notion_id=GOODS_NOTION_ID,
                    description=desc
                )
            session.add(cat)
            session.flush()
            goods_map[name] = cat
            goods_map[slug] = cat

        print(f"Configured {len(GOODS_CATEGORIES)} GOODS categories.")

        # 4. Update Types to appropriate categories
        all_types = session.exec(select(Types)).all()
        for t in all_types:
            parent_target = TYPE_PARENT_MAP.get(t.slug)
            parent_cat = None
            if parent_target:
                parent_cat = goods_map.get(parent_target) or services_map.get(parent_target)
            if not parent_cat:
                parent_cat = goods_map.get("Other") or services_map.get("Technology")

            if parent_cat:
                t.category_id = parent_cat.id
                session.add(t)

        session.commit()
        print(f"Remapped {len(all_types)} types to updated categories.")

        # 5. Get helper maps for page reclassification
        cat_street = goods_map["Street"]
        cat_home = goods_map["Home"]
        cat_tech = goods_map["Tech"]
        cat_traveller = services_map["Traveller"]
        cat_design = services_map["Design"]
        cat_writing = services_map["Writing"]
        cat_financial = services_map["Financial"]
        cat_artists = services_map["Artists"]

        type_apparel = session.exec(select(Types).where(Types.slug == "apparel")).first()
        type_accessories = session.exec(select(Types).where(Types.slug == "accessories")).first()
        type_bathroom = session.exec(select(Types).where(Types.slug == "bathroom")).first()
        type_furniture = session.exec(select(Types).where(Types.slug == "furniture")).first()
        type_tools = session.exec(select(Types).where(Types.slug == "tools")).first()
        type_hotel = session.exec(select(Types).where(Types.slug == "hotel")).first()
        type_solo = session.exec(select(Types).where(Types.slug == "solo")).first()
        type_business = session.exec(select(Types).where(Types.slug == "business")).first()
        type_magazine = session.exec(select(Types).where(Types.slug == "magazine")).first()
        type_artist = session.exec(select(Types).where(Types.slug == "artist")).first()

        # 6. Reorganize and categorize all scraped pages
        all_pages = session.exec(select(Page)).all()
        reclassified_count = 0

        # Mapping of Job ID to target (category, type, source)
        JOB_TAXONOMY_MAP = {
            # Prada & Brunello Cucinelli fashion items -> Street
            49: (cat_street, type_apparel, "consensus"),
            50: (cat_street, type_accessories, "consensus"),
            51: (cat_street, type_accessories, "consensus"),
            52: (cat_street, type_accessories, "consensus"),
            55: (cat_street, type_accessories, "consensus"),

            # IKEA Bathroom products -> Home (Bathroom)
            7:  (cat_home, type_bathroom, "consensus"),
            10: (cat_home, type_bathroom, "consensus"),
            11: (cat_home, type_bathroom, "consensus"),
            12: (cat_home, type_bathroom, "consensus"),
            13: (cat_home, type_bathroom, "consensus"),

            # USM Haller Designer Furniture -> Home (Furniture)
            25: (cat_home, type_furniture, "consensus"),
            27: (cat_home, type_furniture, "consensus"),
            28: (cat_home, type_furniture, "consensus"),
            29: (cat_home, type_furniture, "consensus"),

            # Old Windows Icons -> Tech (Tools)
            26: (cat_tech, type_tools, "consensus"),

            # Pentagram & Behance design portfolios -> Design
            1:  (cat_design, type_business, "consensus"),
            3:  (cat_design, type_business, "consensus"),
            14: (cat_design, type_solo, "consensus"),
            20: (cat_design, type_solo, "consensus"),
            21: (cat_design, type_solo, "consensus"),

            # It's Nice That magazine articles -> Writing (Magazine)
            5:  (cat_writing, type_magazine, "consensus"),
            47: (cat_writing, type_magazine, "consensus"),

            # Il Sole 24 Ore financial article -> Financial (Magazine)
            6:  (cat_financial, type_magazine, "consensus"),

            # J.M.W. Turner & Fine Art America -> Artists (Artist)
            18: (cat_artists, type_artist, "consensus"),
            19: (cat_artists, type_artist, "consensus"),

            # All Hotel & Resort scrape jobs (Tuscany / Meran / Porto Ercole) -> Traveller (Hotel)
            2:  (cat_traveller, type_hotel, "consensus"),
            4:  (cat_traveller, type_hotel, "consensus"),
            30: (cat_traveller, type_hotel, "consensus"),
            31: (cat_traveller, type_hotel, "consensus"),
            33: (cat_traveller, type_hotel, "consensus"),
            34: (cat_traveller, type_hotel, "consensus"),
            35: (cat_traveller, type_hotel, "consensus"),
            36: (cat_traveller, type_hotel, "consensus"),
            42: (cat_traveller, type_hotel, "consensus"),
            44: (cat_traveller, type_hotel, "consensus"),
            45: (cat_traveller, type_hotel, "consensus"),
            46: (cat_traveller, type_hotel, "consensus"),
        }

        for page in all_pages:
            mapping = JOB_TAXONOMY_MAP.get(page.job_id)
            if mapping:
                target_cat, target_type, source = mapping
                page.category_id = target_cat.id
                page.types_id = target_type.id if target_type else None
                page.categorization_source = source
                page.categorization_confidence = 1.0
                session.add(page)
                reclassified_count += 1

        session.commit()
        print(f"Successfully reclassified {reclassified_count} scraped pages into Notion categories.")

        # 7. Remove "Goods & Commerce" category
        old_cat = session.exec(select(Category).where(Category.name == "Goods & Commerce")).first()
        if old_cat:
            # Recheck any orphaned pages/types
            orphaned_pages = session.exec(select(Page).where(Page.category_id == old_cat.id)).all()
            for op in orphaned_pages:
                op.category_id = cat_street.id
                session.add(op)
            orphaned_types = session.exec(select(Types).where(Types.category_id == old_cat.id)).all()
            for ot in orphaned_types:
                ot.category_id = cat_street.id
                session.add(ot)
            session.delete(old_cat)
            session.commit()
            print("Successfully removed obsolete 'Goods & Commerce' category.")

        # 8. Summary of categories and pages
        print("\nReorganized Categories Summary:")
        all_cats = session.exec(select(Category).order_by(Category.section, Category.name)).all()
        for c in all_cats:
            p_count = session.exec(select(func.count(Page.id)).where(Page.category_id == c.id)).one()
            t_count = len(c.types)
            print(f"[{c.section.upper():<8}] {c.name:<18} (slug: {c.slug:<19}, types: {t_count:2d}, pages: {p_count:4d})")

        total_pages = session.exec(select(func.count(Page.id))).one()
        categorized_pages = session.exec(select(func.count(Page.id)).where(Page.category_id.is_not(None))).one()
        print(f"\nTotal pages: {total_pages} | Categorized pages: {categorized_pages} ({categorized_pages/total_pages*100:.1f}%)")


if __name__ == "__main__":
    from sqlmodel import func
    main()

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from .client import get_embedding
from .vector_store import find_similar, compute_similarity

log = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).resolve().parent / "cache" / "_taxonomy_embeddings.json"

CATEGORY_DESCRIPTIONS: Dict[str, str] = {
    # Services / Roles DB (16)
    "Technology": "Software development, technology products, cloud infrastructure, APIs, programming, web apps, SaaS, engineering, IT systems, databases.",
    "Creativity": "Creative services, branding, portfolio, artistic direction, visual innovation, digital concepts, creative agency, design studio.",
    "Artists": "Visual artists, illustrators, 3D artists, animators, painters, musicians, digital creators, fine arts, art exhibitions, galleries.",
    "Design": "UI/UX design, web design, graphic design, typography, brand identity, layout, industrial design, visual styling, portfolio.",
    "Writing": "Copywriting, technical documentation, journalism, blogs, content writing, storytelling, editorial publications, essays, magazines.",
    "Marketing": "Digital marketing, SEO, search engine advertising, social media campaigns, growth hacking, public relations, analytics, ads.",
    "Financial": "Finance, financial services, investments, banking, cryptocurrency, fintech, wealth management, accounting, loans.",
    "Management": "Business management, administration, consulting, legal compliance, operations, enterprise leadership, executive strategy.",
    "Traveller": "Travel guides, tourism, hotels, hospitality resorts, luxury accommodation, booking, destinations, trip planning, flight services, vacations.",
    "Assistant": "AI assistants, productivity bots, intelligent automation, copilot tools, task helpers, automated workflows.",
    "Builder": "Web builders, site generators, no-code platforms, frameworks, development scaffolds, page builders, CMS platforms.",
    "Content Creation": "Content creators, streaming, podcasting, video production, blogging, influencer media, publishing.",
    "Economics": "Economic research, market analysis, financial theory, global trade, macroeconomic data, socioeconomic studies.",
    "Joker": "General utilities, multipurpose helpers, miscellaneous tools, calculators, format converters, random web helpers.",
    "Legal": "Legal services, law firms, contracts, terms of service, regulatory compliance, privacy policies, attorneys.",
    "Development": "Software engineering firms, frontend and backend development agencies, mobile apps development, web dev shops.",

    # GOODS / Categories DB (16)
    "Snowboarding": "Snowboarding gear, snowboards, winter sports equipment, bindings, alpine winter apparel, snow outerwear, goggles.",
    "Cycling": "Bicycles, cycling apparel, bike gear, road bikes, gravel, mountain biking accessories, cycling helmets, components.",
    "Home": "Home furnishings, interior decor, kitchenware, bathroom accessories, furniture, appliances, lighting, living essentials.",
    "Street": "Streetwear, casual fashion, denim, jeans, sneakers, sunglasses, eyewear, urban apparel, accessories, shoes, clothing, luxury boutique.",
    "Hiking": "Hiking gear, trekking equipment, outdoor apparel, trail footwear, backpacks, camping gear, outdoor exploration.",
    "Yoga": "Yoga mats, activewear, pilates apparel, meditation accessories, athletic leggings, mindful movement gear.",
    "Other": "Miscellaneous consumer products, general accessories, replacement parts, smoking accessories, specialty goods.",
    "Travel": "Luggage, travel bags, travel accessories, passport holders, packing cubes, weekenders, journey gear.",
    "Beverage": "Coffee, specialty tea, wine, alcoholic beverages, craft drinks, roasteries, bottled drinks, glassware.",
    "Entertainment": "Toys, board games, vinyl records, entertainment collectibles, pop culture items, gaming accessories.",
    "Car": "Automotive accessories, car parts, wheels, vehicle care, automotive tools, driving gear, tyres.",
    "Food": "Gourmet food, artisan snacks, sweets, chocolates, pantry staples, specialty ingredients, edible goods.",
    "Office": "Office supplies, desk organization, stationery, notebooks, pens, printing supplies, workspace accessories.",
    "Tech": "Consumer electronics, gadgets, computer hardware, audio equipment, chargers, tech accessories, phone cases.",
    "Wellness": "Personal care, skincare, grooming, bath and body, cosmetics, clean beauty, wellness products, health essentials.",
}

TYPE_DESCRIPTIONS: Dict[str, str] = {
    "Application": "Full web application, desktop app, mobile software, SaaS platform, interactive tool interface.",
    "Service": "Online service, API, cloud backend, web service, subscription platform, infrastructure.",
    "Tools": "Utility tool, developer tool, converter, calculator, generator, inspection tool, testing tool.",
    "Plugin": "Plugin, browser extension, add-on, integration, module, widget.",
    "Model": "Machine learning model, LLM, diffusion model, AI neural network, trained weights, inference model.",
    "Utils": "General utility script, command line tool, helper library, micro-tool.",
    "Art": "Artistic work, fine arts exhibition, painting, sculpture, visual expression.",
    "Artist": "Individual artist, painter, digital artist, portfolio of fine art.",
    "Blogger": "Blog, personal journal, web articles, commentary, newsletter.",
    "Business": "Corporate business, commercial enterprise, company homepage, business consultancy.",
    "Chain": "Chain store, franchise, multi-location business network.",
    "Creator": "Online content creator, digital maker, creative producer.",
    "Entrepreneur": "Startup founder, entrepreneur personal site, venture builder.",
    "Guest": "Guest house, boutique accommodation, visitor lodging, bed and breakfast.",
    "Hotel": "Hotel, hospitality resort, luxury stay, travel accommodation.",
    "Magazine": "Online magazine, periodical publication, editorial essays, cultural journal.",
    "Network": "Professional network, community directory, affiliate alliance.",
    "Org": "Organization, non-profit, foundation, institutional association.",
    "Personal": "Personal website, resume, bio, personal links.",
    "Photo / Video": "Photography and videography portfolio, cinematography, photo studio.",
    "Shop": "E-commerce store, product catalog, online shopping cart, retail merchandise.",
    "Solo": "Solo practitioner, independent freelancer, single consultant.",
    "Travel Service": "Travel agency, tour booking, trip guide, excursion service.",
}

_TAXONOMY_STORE: Optional[Dict[str, Dict[str, List[float]]]] = None


def build_taxonomy_embeddings(force: bool = False) -> Dict[str, Dict[str, List[float]]]:
    """
    Generate and save vector representations for all canonical Categories and Types.
    Uses Vertex AI text-embedding-004 with local fallback.
    """
    global _TAXONOMY_STORE

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not force and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "categories" in data and "types" in data:
                    _TAXONOMY_STORE = data
                    return data
        except Exception as exc:
            log.warning(f"[TAX_EMBED] Error reading cache file {CACHE_FILE}: {exc}")

    log.info("[TAX_EMBED] Building taxonomy embeddings store...")
    categories: Dict[str, List[float]] = {}
    for cat_name, desc in CATEGORY_DESCRIPTIONS.items():
        vec = get_embedding(f"{cat_name}: {desc}")
        if vec:
            categories[cat_name] = vec

    types: Dict[str, List[float]] = {}
    for type_name, desc in TYPE_DESCRIPTIONS.items():
        vec = get_embedding(f"{type_name}: {desc}")
        if vec:
            types[type_name] = vec

    store = {"categories": categories, "types": types}
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f)
        log.info(
            f"[TAX_EMBED] Cached {len(categories)} Category vectors and "
            f"{len(types)} Type vectors to {CACHE_FILE}"
        )
    except Exception as exc:
        log.error(f"[TAX_EMBED] Failed writing cache to {CACHE_FILE}: {exc}")

    _TAXONOMY_STORE = store
    return store


def get_taxonomy_embeddings() -> Dict[str, Dict[str, List[float]]]:
    """Retrieve pre-computed taxonomy embeddings, loading or generating on first request."""
    global _TAXONOMY_STORE
    if _TAXONOMY_STORE is not None:
        return _TAXONOMY_STORE

    return build_taxonomy_embeddings(force=False)


def match_category_embedding(
    text: str,
    threshold: float = 0.0,
    top_k: int = 3,
) -> List[Tuple[str, float]]:
    """Match input text against canonical categories using vector cosine similarity."""
    target_vec = get_embedding(text)
    if not target_vec:
        return []

    store = get_taxonomy_embeddings()
    cat_cache = store.get("categories", {})
    return find_similar(target_vec, cat_cache, threshold=threshold, top_k=top_k)


def match_type_embedding(
    text: str,
    threshold: float = 0.0,
    top_k: int = 3,
) -> List[Tuple[str, float]]:
    """Match input text against canonical types using vector cosine similarity."""
    target_vec = get_embedding(text)
    if not target_vec:
        return []

    store = get_taxonomy_embeddings()
    type_cache = store.get("types", {})
    return find_similar(target_vec, type_cache, threshold=threshold, top_k=top_k)

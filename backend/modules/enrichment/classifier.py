import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlparse
from sqlmodel import Session, select, func

from modules.embeddings.taxonomy_embeddings import (
    match_category_embedding,
    match_type_embedding,
)
from modules.taxonomies.jev_matcher import match_taxonomy_values

log = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """Standard slugifier for database lookup."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "default"


# ─────────────────────────────────────────────────────────────────────────────
# JEV Branch Rules & Heuristics
# ─────────────────────────────────────────────────────────────────────────────

ROLE_KEYWORDS: Dict[str, str] = {
    r"\btech\b|\bdevelop\b|\bdeveloper\b|\bcode\b|\bcoding\b|\bapi\b|\bapis\b|\bprogramming\b|\bsoftware\b|\bcloud\b|\bdatabase\b|\bpostgres\b|\bsaas\b|\bpython\b|\bjavascript\b|\btypescript\b": "Technology",
    r"\bdesign\b|\bdesigner\b|\bui\b|\bux\b|\bgraphics\b|\btypography\b|\btypeface\b|\bbranding\b|\blayout\b": "Design",
    r"\bshop\b|\becommerce\b|\bstore\b|\bcart\b|\bcheckout\b|\bapparel\b|\bclothing\b|\bproducts?\b|\bmerchandise\b|\bboutique\b|\bretail\b": "Goods & Commerce",
    r"\bcreativ\w+|\bstudio\b|\bagency\b|\bportfolio\b|\bvisuals?\b|\bconsultancy\b": "Creativity",
    r"\bfinance\b|\bfinancial\b|\binvest\w+|\bbanking\b|\bcrypto\w*|\bfintech\b|\baccounting\b|\bloans?\b|\bwealth\b": "Financial",
    r"\bmanagement\b|\blegal\b|\badmin\b|\benterprise\b|\boperations\b|\bbusiness\b|\bexecutive\b|\bconsulting\b": "Management",
    r"\bmarketing\b|\bseo\b|\bads?\b|\badvertising\b|\bcampaign\b|\bsem\b|\bpublic relations\b|\bpr\b": "Marketing",
    r"\bwrite\b|\bwriting\b|\bcontent\b|\bcopywriting\b|\bjournal\b|\bmagazine\b|\bblog\b|\beditorial\b|\bessays?\b": "Writing",
    r"\bart\b|\bartist\b|\bartists\b|\bmusic\b|\bvideo\b|\bfilm\b|\billustrat\w+|\banimat\w+|\bpainting\b": "Artists",
    r"\btravel\b|\btour\w*|\bhotel\b|\bbooking\b|\bflight\b|\bdestination\b|\bresort\b|\bvacation\b|\bguest house\b": "Traveller",
    r"\bassistant\b|\bai\b|\bcopilot\b|\bchatbot\b|\bgpt\b|\bllm\b|\bvirtual assistant\b": "Assistant",
    r"\bbuilder\b|\bgenerator\b|\bno-code\b|\blow-code\b|\bscaffold\b|\bsite builder\b|\bpage builder\b": "Builder",
    r"\bcreator\b|\bpodcast\b|\byoutube\b|\bstreaming\b|\bmedia\b|\binfluencer\b": "Content Creation",
    r"\beconomics?\b|\bmacroeconomic\b|\bmarket analysis\b|\beconomy\b": "Economics",
    r"\blegal\b|\blaw\b|\blawyer\b|\battorney\b|\bcompliance\b|\bgdpr\b|\bregulatory\b": "Legal",
    r"\butil\b|\butility\b|\bhelper\b|\bconverter\b|\bcalculator\b": "Joker",
}

TYPE_RULES: List[Tuple[str, str]] = [
    (r"\bservice\b|\bsmtp\b|\bcdn\b|\bhosting\b|\bplatform\b|\bapi\b|\bcloud backend\b|\binfrastructure\b", "Service"),
    (r"\bplugin\b|\bextension\b|\baddon\b|\badd-on\b|\bchrome extension\b|\bfirefox addon\b", "Plugin"),
    (r"\bmodel\b|\bdiffusion\b|\bgpt\b|\bllm\b|\bweights\b|\bneural network\b", "Model"),
    (r"\bshop\b|\becommerce\b|\bstore\b|\bcheckout\b|\bcart\b|\bboutique\b|\bretail\b", "Shop"),
    (r"\bapp\b|\bapplication\b|\bide\b|\bdashboard\b|\bsaas\b|\bweb app\b|\bdesktop app\b", "Application"),
    (r"\btools?\b|\bconverter\b|\bcalculator\b|\bgenerator\b|\bchecker\b|\bvalidator\b|\binspector\b", "Tools"),
    (r"\butil\b|\butils\b|\bhelper\b|\bscript\b", "Utils"),
    (r"\bstudio\b|\bagency\b|\bconsultancy\b|\bfirm\b", "Business"),
    (r"\bhotel\b|\bresort\b|\blodging\b", "Hotel"),
    (r"\bguest house\b|\bbed and breakfast\b|\bb&b\b", "Guest"),
    (r"\bmagazine\b|\bjournal\b|\bperiodical\b", "Magazine"),
    (r"\bportfolio\b|\bworks\b|\bshowcase\b", "Solo"),
    (r"\bartist\b|\bpainter\b|\bmusician\b|\billustrator\b", "Artist"),
]

TAG_PATTERNS: Dict[str, str] = {
    "iconics": r"\b(?:icons?|iconics?|iconography|favicons?)\b",
    "archive": r"\b(?:archives?|archiving|archived|wayback)\b",
    "calc": r"\b(?:calc|calculat(?:or|ors|ion|ions))\b",
    "convert": r"\b(?:convert|convert(?:er|ers|ion|ing))\b",
    "fonts": r"\b(?:fonts?|typefaces?|typography)\b",
    "db": r"\b(?:db|databases?|sql|nosql|postgres|mysql|sqlite)\b",
    "domains": r"\b(?:domains?|whois|tld|registrars?)\b",
    "flights": r"\b(?:flights?|airfare|airline)\b",
    "maps": r"\b(?:maps?|mapping|geolocat\w+|coordinates)\b",
    "tables": r"\b(?:tables?|spreadsheets?|csv|tsv)\b",
    "trips": r"\b(?:trips?|travel|tourism)\b",
    "cronjob": r"\b(?:cron|crontabs?|cronjobs?|scheduled\s*tasks?)\b",
    "free stock": r"\b(?:free\s*stock|stock\s*(?:photos?|images?|vectors?|videos?))\b",
    "qr": r"\bqr(?:\s*codes?)?\b",
    "grammar": r"\b(?:grammar|spell(?:ing)?\s*check(?:er)?)\b",
    "inspection": r"\b(?:inspect|inspector|inspection)\b",
    "case": r"\b(?:case\s*convert(?:er)?|camelcase|snake_case|kebab-case|title\s*case)\b",
    "weather": r"\b(?:weather|forecast|meteorolog\w+)\b",
    "crypto": r"\b(?:crypto|cryptocurrency|bitcoin|ethereum|blockchain)\b",
    "currency": r"\b(?:currency|currencies|exchange\s*rate|forex)\b",
    "email": r"\b(?:emails?|newsletter|inbox|smtp|mail)\b",
    "privacy": r"\b(?:privacy|gdpr|cookie\s*consent)\b",
    "api": r"\b(?:api|rest\s*api|graphql|endpoints?)\b",
    "hosting": r"\b(?:hosting|deploy|deployment|vps|serverless)\b",
    "ai": r"\b(?:ai|artificial intelligence|llm|gpt|machine learning)\b",
    "design": r"\b(?:design|ui|ux|branding|visual)\b",
    "ecommerce": r"\b(?:ecommerce|store|shop|online store)\b",
}

GENRE_KEYWORDS: Dict[str, str] = {
    r"\bai\b|\bartificial intelligence\b|\bmachine learning\b|\bllm\b": "AI",
    r"\b3d\b|\bthreejs\b|\bblender\b|\bcgi\b": "3D",
    r"\bdesign\b|\bui\b|\bux\b": "Design",
    r"\btypography\b|\btype\b|\bfont\b": "Typography",
    r"\bbranding\b|\bidentity\b": "Branding",
    r"\badv\b|\badvertising\b": "Adv",
    r"\bagency\b|\bstudio\b": "Agency",
    r"\banimation\b|\bmotion\b": "Animation",
    r"\barchitecture\b|\binterior\b": "Architecture",
    r"\bart\b|\bcreative\b": "Art",
    r"\bbackend\b|\bserver\b": "Backend",
    r"\bfrontend\b|\bweb development\b": "Frontend",
    r"\bdigital\b": "Digital",
    r"\bfashion\b|\bapparel\b": "Fashion",
    r"\bphotography\b|\bphoto\b": "Photography",
    r"\bproduct\b|\bindustrial\b": "Product",
    r"\bhotellerie\b|\bhotel\b": "Hotellerie",
    r"\bvideo\b|\bfilm\b": "Video",
    r"\bweb\b": "Web",
}


@dataclass
class ClassificationResult:
    category: str
    category_id: Optional[int] = None
    type: str = "Utils"
    types_id: Optional[int] = None
    genre: Optional[str] = None
    genre_id: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    tag_ids: List[int] = field(default_factory=list)
    source: str = "dual_hybrid"  # "jev" | "embedding" | "consensus" | "dual_hybrid"
    confidence: float = 0.0
    scores: Dict[str, Any] = field(default_factory=dict)


def _extract_text_features(
    title: Optional[str] = None,
    description: Optional[str] = None,
    body_text: Optional[str] = None,
    url: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    Extract and structure text into primary text, body preview, and semantic representation.
    """
    t = (title or "").strip()
    d = (description or "").strip()
    b = (body_text or "").strip()

    # Extract URL hints
    url_hint = ""
    if url:
        pu = urlparse(url)
        url_hint = f"{pu.netloc} {pu.path}".replace("/", " ").replace("-", " ").replace("_", " ")

    primary = f"{t} {d} {url_hint}".strip()
    body_preview = b[:2500]
    semantic_text = f"Title: {t}\nDescription: {d}\nURL: {url or ''}\n\n{body_preview}".strip()

    return primary, body_preview, semantic_text


def _evaluate_jev(primary: str, body: str) -> Dict[str, Any]:
    """Evaluate JEV heuristic and regex patterns for Category, Type, Tags, and Genre."""
    primary_lower = primary.lower()
    body_lower = body.lower() if body else ""

    # 1. Category / Role Scoring
    cat_scores: Dict[str, float] = {}
    for pat, role in ROLE_KEYWORDS.items():
        score = 0.0
        if re.search(pat, primary_lower):
            score += 3.0  # Explicit keyword match in primary text
        if body_lower and re.search(pat, body_lower):
            occ = len(re.findall(pat, body_lower))
            if occ >= 2:
                score += min(2.0, occ * 0.5)
        if score > 0:
            cat_scores[role] = max(cat_scores.get(role, 0.0), score)

    best_cat = None
    best_cat_score = 0.0
    if cat_scores:
        sorted_cats = sorted(cat_scores.items(), key=lambda x: x[1], reverse=True)
        best_cat, best_cat_score = sorted_cats[0]

    # 2. Type Scoring
    type_scores: Dict[str, float] = {}
    for pat, type_val in TYPE_RULES:
        score = 0.0
        if re.search(pat, primary_lower):
            score += 3.0
        elif body_lower and re.search(pat, body_lower):
            score += 1.0
        if score > 0:
            type_scores[type_val] = max(type_scores.get(type_val, 0.0), score)

    best_type = None
    best_type_score = 0.0
    if type_scores:
        sorted_types = sorted(type_scores.items(), key=lambda x: x[1], reverse=True)
        best_type, best_type_score = sorted_types[0]

    # 3. Tags Extraction
    extracted_tags: List[str] = []
    combined_text = f"{primary_lower} {body_lower}"
    for tag_name, pat in TAG_PATTERNS.items():
        if re.search(pat, primary_lower):
            extracted_tags.append(tag_name)
        elif body_lower and len(re.findall(pat, body_lower)) >= 2:
            extracted_tags.append(tag_name)

    # 4. Genre Extraction
    matched_genre = None
    for pat, genre_name in GENRE_KEYWORDS.items():
        if re.search(pat, primary_lower):
            matched_genre = genre_name
            break

    return {
        "best_category": best_cat,
        "category_score": best_cat_score,
        "all_category_scores": cat_scores,
        "best_type": best_type,
        "type_score": best_type_score,
        "all_type_scores": type_scores,
        "tags": list(dict.fromkeys(extracted_tags))[:5],
        "genre": matched_genre,
    }


def _arbitrate_selection(
    jev: Dict[str, Any],
    emb_cats: List[Tuple[str, float]],
    emb_types: List[Tuple[str, float]],
) -> Tuple[str, str, str, float]:
    """
    Arbitrate between JEV rules and Vector Embeddings.
    Rules:
    - If JEV has a strong explicit keyword match (score >= 3.0) and agrees with embedding:
      consensus (source='consensus', confidence=1.0)
    - If JEV has strong explicit match (score >= 3.0):
      source='jev', confidence=1.0
    - If both agree on Category:
      source='consensus', confidence=1.0
    - If JEV is ambiguous/empty (score < 3.0):
      rescue using Embedding top match if similarity >= 0.78 (source='embedding', confidence=similarity)
    - Returns: (chosen_category, chosen_type, final_source, final_confidence)
    """
    cat_jev = jev.get("best_category")
    cat_score = jev.get("category_score", 0.0)

    top_emb_cat = emb_cats[0][0] if emb_cats else None
    top_emb_sim = emb_cats[0][1] if emb_cats else 0.0

    type_jev = jev.get("best_type")
    type_score = jev.get("type_score", 0.0)

    top_emb_type = emb_types[0][0] if emb_types else None
    top_emb_type_sim = emb_types[0][1] if emb_types else 0.0

    chosen_category: str = "Technology"
    cat_source: str = "dual_hybrid"
    cat_conf: float = 0.5

    # Category Arbitration
    if cat_jev and top_emb_cat and cat_jev.lower() == top_emb_cat.lower():
        # Both agree!
        chosen_category = cat_jev
        cat_source = "consensus"
        cat_conf = 1.0
    elif cat_score >= 3.0 and cat_jev:
        # Strong explicit keyword match in primary text
        chosen_category = cat_jev
        cat_source = "jev"
        cat_conf = 1.0
    elif (cat_score < 3.0 or not cat_jev) and top_emb_cat and top_emb_sim >= 0.78:
        # High confidence embedding rescue
        chosen_category = top_emb_cat
        cat_source = "embedding"
        cat_conf = float(round(top_emb_sim, 2))
    elif top_emb_cat and (not cat_jev or top_emb_sim >= 0.45):
        # Moderate embedding match
        chosen_category = top_emb_cat
        cat_source = "embedding"
        cat_conf = float(round(top_emb_sim, 2))
    elif cat_jev:
        chosen_category = cat_jev
        cat_source = "jev"
        cat_conf = float(round(min(1.0, cat_score / 3.0), 2))

    # Type Arbitration
    chosen_type: str = "Utils"
    if type_jev and top_emb_type and type_jev.lower() == top_emb_type.lower():
        chosen_type = type_jev
    elif type_score >= 3.0 and type_jev:
        chosen_type = type_jev
    elif top_emb_type and top_emb_type_sim >= 0.40:
        chosen_type = top_emb_type
    elif type_jev:
        chosen_type = type_jev
    elif top_emb_type:
        chosen_type = top_emb_type

    # Final combined source
    if cat_source == "consensus":
        final_source = "consensus"
    elif cat_source == "jev":
        final_source = "jev"
    elif cat_source == "embedding":
        final_source = "embedding"
    else:
        final_source = "dual_hybrid"

    return chosen_category, chosen_type, final_source, cat_conf


def classify_page(
    title: Optional[str] = None,
    description: Optional[str] = None,
    body_text: Optional[str] = None,
    url: Optional[str] = None,
    db_session: Optional[Session] = None,
) -> ClassificationResult:
    """
    Main entry point for Dual Auto-Categorization Engine.
    Combines JEV rule-based heuristics and Vertex AI / vector store embeddings,
    arbitrates according to consensus and confidence thresholds, and optionally
    resolves relational database foreign keys in SQLite.
    """
    primary, body_preview, semantic_text = _extract_text_features(
        title=title,
        description=description,
        body_text=body_text,
        url=url,
    )

    # Branch A: JEV Rule Heuristics
    jev_result = _evaluate_jev(primary, body_preview)

    # Branch B: Vector Embeddings Cosine Similarity
    emb_cats = match_category_embedding(semantic_text, top_k=3)
    emb_types = match_type_embedding(semantic_text, top_k=3)

    # Dual Arbitration
    category, type_val, source, confidence = _arbitrate_selection(
        jev=jev_result,
        emb_cats=emb_cats,
        emb_types=emb_types,
    )

    tags = jev_result.get("tags", [])
    genre = jev_result.get("genre")

    scores_meta = {
        "jev": jev_result,
        "embedding_categories": emb_cats,
        "embedding_types": emb_types,
    }

    result = ClassificationResult(
        category=category,
        type=type_val,
        genre=genre,
        tags=tags,
        source=source,
        confidence=confidence,
        scores=scores_meta,
    )

    # Resolve Database Foreign Keys if session is provided
    if db_session is not None:
        try:
            from database.models import Category, Types, Tag, Genre

            # Resolve Category ID
            cat_slug = slugify(category)
            cat_obj = db_session.exec(
                select(Category).where(
                    (Category.slug == cat_slug) | (func.lower(Category.name) == category.lower())
                )
            ).first()
            if cat_obj and cat_obj.id is not None:
                result.category_id = cat_obj.id

            # Resolve Type ID
            type_slug = slugify(type_val)
            type_obj = db_session.exec(
                select(Types).where(
                    (Types.slug == type_slug) | (func.lower(Types.name) == type_val.lower())
                )
            ).first()
            if type_obj and type_obj.id is not None:
                result.types_id = type_obj.id
            elif cat_obj and cat_obj.id is not None:
                # If specific type not found, check if a generic type for this category exists
                fallback_type = db_session.exec(
                    select(Types).where(Types.category_id == cat_obj.id)
                ).first()
                if fallback_type:
                    result.types_id = fallback_type.id

            # Resolve Genre ID
            if genre:
                genre_slug = slugify(genre)
                genre_obj = db_session.exec(
                    select(Genre).where(
                        (Genre.slug == genre_slug) | (func.lower(Genre.name) == genre.lower())
                    )
                ).first()
                if genre_obj and genre_obj.id is not None:
                    result.genre_id = genre_obj.id

            # Resolve Tag IDs
            tag_ids: List[int] = []
            for tag_name in tags:
                tag_slug = slugify(tag_name)
                tag_obj = db_session.exec(
                    select(Tag).where(
                        (Tag.slug == tag_slug) | (func.lower(Tag.name) == tag_name.lower())
                    )
                ).first()
                if tag_obj and tag_obj.id is not None:
                    tag_ids.append(tag_obj.id)
            result.tag_ids = tag_ids

        except Exception as exc:
            log.warning(f"[CLASSIFIER] Error resolving database taxonomy IDs: {exc}")

    return result

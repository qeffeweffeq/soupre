import os
import re
import shutil
import asyncio
import logging
from typing import Optional, List
from urllib.parse import urlparse
import httpx

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from database.models import ScrapeJob, Page, Category, Types, Workspace, Tag, PageTagLink, Element, PageElementLink
from database.session import get_session, engine as default_engine
from schemas.jobs import (
    JobTaxonomyUpdate,
    BatchPreviewItem,
    BatchPreviewResponse,
    BatchCuratedItem,
    BatchExecuteRequest,
    UrlVerifyInput,
    UrlVerifyResult,
    BatchVerifyUrlsRequest,
    BatchVerifyUrlsResponse,
)
from modules.serializers import serialize_job_enriched, serialize_page_enriched
from modules.config import DOWNLOADS_DIR, PROJECT_ROOT
from modules.core.job_logger import (
    job_log_manager,
    current_job_id,
    DONE_SENTINEL,
)
from modules.utils.url_utils import (
    normalize_target_url,
    sanitize_url,
    extract_urls_from_text,
    extract_and_analyze_urls_from_text,
    get_domain_from_url,
)
from modules.scrapers.sitemap_fetcher import determine_content_type
from modules.rules import TaxonomyRuleContext, evaluate_taxonomy_rules
from modules.utils.page_recovery import (
    get_job_dir,
    reset_job_retryable_pages,
    reconcile_job_statuses,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _get_engine():
    """Retrieve active engine, respecting any test fixture override on main.engine."""
    try:
        import main
        return getattr(main, "engine", default_engine)
    except Exception:
        return default_engine


async def _run_job_with_logging(
    job_id: int,
    target_url: str,
    scrape_mode: str = "single",
    max_pages: int = 0,
    delay_min: float = 60.0,
    delay_max: float = 120.0,
    session_min: int = 30,
    session_max: int = 50,
    cooldown_min: float = 180.0,
    cooldown_max: float = 300.0,
    large_threshold: int = 20,
    large_pause_sec: int = 120,
):
    """Wrapper that tracks job_id in contextvars and closes job log streams upon exit."""
    active_engine = _get_engine()
    token = current_job_id.set(job_id)
    job_log_manager.mark_job_active(job_id)
    try:
        from modules.scrapers.engine import run_scrape_job
        await run_scrape_job(
            job_id,
            target_url,
            active_engine,
            scrape_mode,
            max_pages,
            delay_min,
            delay_max,
            session_min,
            session_max,
            cooldown_min,
            cooldown_max,
            large_threshold,
            large_pause_sec,
        )
    except Exception as e:
        logging.error(f"Job {job_id} encountered unhandled exception: {e}", exc_info=True)
        try:
            with Session(active_engine) as session:
                job = session.get(ScrapeJob, job_id)
                if job and job.status not in ("cancelled", "paused"):
                    job.status = "failed"
                    session.add(job)
                pending_pages = session.exec(select(Page).where(Page.job_id == job_id, Page.status == "pending")).all()
                for p in pending_pages:
                    p.status = "failed"
                    session.add(p)
                session.commit()
        except Exception:
            pass
    finally:
        job_log_manager.mark_job_inactive(job_id)
        current_job_id.reset(token)
        await job_log_manager.close_job(job_id)


async def _dispatch_run_job(*args, **kwargs):
    import main
    runner = getattr(main, "_run_job_with_logging", _run_job_with_logging)
    res = runner(*args, **kwargs)
    if asyncio.iscoroutine(res):
        return await res
    return res


@router.post("/batch-upload", response_model=BatchPreviewResponse)
async def batch_upload_urls(
    request: Request,
    file: Optional[UploadFile] = File(None),
    raw_text: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    """
    Extract, sanitize, and analyze URLs from an uploaded file (.txt, .csv, .md, .html)
    or a raw text form/JSON payload, returning rich preview metrics and taxonomy predictions.
    """
    content = ""
    filename = None
    if file is not None:
        filename = file.filename
        file_bytes = await file.read()
        content = file_bytes.decode("utf-8", errors="ignore")
    elif raw_text:
        content = raw_text
    else:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body_json = await request.json()
                if isinstance(body_json, dict):
                    content = str(body_json.get("raw_text") or "")
                    filename = body_json.get("filename")
                elif isinstance(body_json, str):
                    content = body_json
            except Exception:
                pass
        if not content:
            try:
                body_bytes = await request.body()
                if body_bytes:
                    content = body_bytes.decode("utf-8", errors="ignore")
            except Exception:
                pass

    analyses = extract_and_analyze_urls_from_text(content)
    if not analyses:
        return BatchPreviewResponse(
            count=0,
            filename=filename,
            items=[],
            urls=[],
        )

    # Fetch categories, types, tags, elements, and workspaces for smart taxonomy prediction
    categories = session.exec(select(Category)).all()
    types_list = session.exec(select(Types)).all()
    workspaces = session.exec(select(Workspace)).all()
    tags = session.exec(select(Tag)).all()
    elements = session.exec(select(Element)).all()
    ws_by_id = {w.id: w for w in workspaces}
    ws_by_slug = {w.slug.lower(): w for w in workspaces}
    ffff_ws = ws_by_slug.get("ffff")

    cat_by_name = {c.name.lower(): c for c in categories}
    cat_by_slug = {c.slug.lower(): c for c in categories}
    type_by_name = {t.name.lower(): t for t in types_list}
    type_by_slug = {t.slug.lower(): t for t in types_list}
    tag_by_name = {t.name.lower(): t for t in tags}
    tag_by_slug = {t.slug.lower(): t for t in tags}
    elem_by_name = {e.name.lower(): e for e in elements}
    elem_by_slug = {e.slug.lower(): e for e in elements}

    FFFF_BRANDS_RE = re.compile(r"(pentagram\.com|behance\.(?:net|com)|itsnicethat\.com|its-nice-that)", re.I)
    FASHION_RE = re.compile(r"\b(jeans|denim|occhiali|sunglasses|eyewear|trousers|pants|chino|chinos|apparel|clothing|shoes|sneakers|espadrillas|scarpe|stivali|sandali|mocassini|dress|skirt|jacket|coat|sweater|velvet|gabardine|cotton|tela|pleats|accessories|boutique|cucinelli|prada|aspesi|nike|gucci|zara|armani|fur|leather|scarf|hat|bag|bags|borsa|borse|shirt|shirts|camicia|maglia|pantalone|pantaloni|giacca|abito|tee|t-shirt|polo|hoodie|viscose|silk|wool|cashmere|linen|knitwear|knit|socks|paulsmith|ralphlauren)\b", re.I)
    HOME_RE = re.compile(r"\b(furniture|arredo|arredamento|mobile|mobili|tavol[io]|sedie|sedia|poltron[ae]|divan[io]|scaffal[ei]|ikea|usm|haller|vitra|cassina|flos|kartell|tiles?|piastrell[ae]|ceramica|ceramiche|marazzi|bathroom|kitchen|cucina|bagno|lighting|lampad[ae]|electricity|elettric\w*|interruttor\w*|bticino|vimar)\b", re.I)
    SHOP_RE = re.compile(r"\b(shop|store|products?|prodott[io]|item|p|articolo)\b", re.I)
    HOSPITALITY_RE = re.compile(r"\b(hotel\w*|resort\w*|suites?|camera|camere|allogg\w+|relais|spa|wellness|travel|traveller|booking|bed and breakfast|b&b)\b", re.I)
    TECH_RE = re.compile(r"\b(tech|develop\w*|code|coding|api|apis|software|saas|python|javascript|typescript|app|application|plugin|tools?|github|dev)\b", re.I)
    DESIGN_RE = re.compile(r"\b(design|designer|branding|graphics?|typography|identity|visuals?|portfolio|studio|agency)\b", re.I)
    CONTENT_RE = re.compile(r"\b(article|articles|blog|posts?|magazine|journal|editorial|writing|news|notizie)\b", re.I)

    items: List[BatchPreviewItem] = []
    urls: List[str] = []

    for idx, analysis in enumerate(analyses):
        clean = analysis["clean_url"]
        urls.append(clean)
        domain = get_domain_from_url(clean)
        content_type = determine_content_type(clean) or "pages"

        parsed_url = urlparse(clean)
        path = parsed_url.path.lower()
        full_str = f"{domain} {path}".replace("/", " ").replace("-", " ").replace("_", " ").replace(".", " ")

        matched_ws = None
        matched_cat = None
        matched_type = None

        is_ffff = bool(FFFF_BRANDS_RE.search(domain) or FFFF_BRANDS_RE.search(clean))
        if is_ffff and ffff_ws:
            matched_ws = ffff_ws
            matched_cat = None
            matched_type = None
        elif FASHION_RE.search(full_str) or (SHOP_RE.search(full_str) and any(b in domain for b in ("prada", "aspesi", "cucinelli", "nike", "gucci", "zara", "armani", "luisaviaroma", "paulsmith", "paul-smith", "ralphlauren"))):
            matched_cat = cat_by_name.get("street") or cat_by_slug.get("street")
            if re.search(r"\b(occhiali|sunglasses|eyewear|accessories|scarf|belt|wallet|borsa|borse|bag|bags)\b", full_str, re.I):
                matched_type = type_by_name.get("accessories") or type_by_slug.get("accessories")
            elif re.search(r"\b(jeans|denim|trousers|pants|pantalone|pantaloni|chino|apparel|clothing|cotton|tela|velvet|pleats|jacket|giacca|coat|dress|abito|skirt|shirt|shirts|camicia|maglia|tee|t-shirt|polo|hoodie|sweater|knitwear|knit|socks|suit|blazer|espadrillas|scarpe|shoes|sneakers|stivali|sandali|mocassini)\b", full_str, re.I):
                matched_type = type_by_name.get("apparel") or type_by_slug.get("apparel")
            elif re.search(r"\b(shop|store|boutique)\b", full_str, re.I):
                matched_type = type_by_name.get("shop") or type_by_slug.get("shop")
        elif HOME_RE.search(full_str):
            matched_cat = cat_by_name.get("home") or cat_by_slug.get("home")
            if re.search(r"\b(tiles?|piastrell[ae]|ceramica|ceramiche|marazzi)\b", full_str, re.I):
                matched_type = type_by_name.get("tiles") or type_by_slug.get("tiles")
            elif re.search(r"\b(electricity|elettric\w*|interruttor\w*|bticino|vimar)\b", full_str, re.I):
                matched_type = type_by_name.get("electricity") or type_by_slug.get("electricity")
                matched_cat = cat_by_name.get("tech") or cat_by_slug.get("tech") or matched_cat
            else:
                matched_type = type_by_name.get("furniture") or type_by_slug.get("furniture")
        elif HOSPITALITY_RE.search(full_str):
            matched_cat = cat_by_name.get("traveller") or cat_by_slug.get("traveller")
            matched_type = type_by_name.get("hotel") or type_by_slug.get("hotel")
        elif TECH_RE.search(full_str):
            matched_cat = cat_by_name.get("technology") or cat_by_slug.get("technology")
            if re.search(r"\b(plugin|extension)\b", full_str, re.I):
                matched_type = type_by_name.get("plugin")
            elif re.search(r"\b(tools?|utils?)\b", full_str, re.I):
                matched_type = type_by_name.get("tools")
            else:
                matched_type = type_by_name.get("application")
        elif DESIGN_RE.search(full_str):
            matched_cat = cat_by_name.get("design") or cat_by_name.get("creativity")
            matched_type = type_by_name.get("solo") or type_by_name.get("business")
        elif CONTENT_RE.search(full_str):
            matched_cat = cat_by_name.get("writing") or cat_by_name.get("content creation")
            matched_type = type_by_name.get("magazine")
        elif content_type == "products":
            matched_cat = cat_by_name.get("street")
            matched_type = type_by_name.get("shop")
        elif content_type in ("hotels", "rooms", "dining", "experiences"):
            matched_cat = cat_by_name.get("traveller")
            matched_type = type_by_name.get("hotel")
        elif content_type in ("blog", "news"):
            matched_cat = cat_by_name.get("writing")
            matched_type = type_by_name.get("magazine")

        if not is_ffff:
            # Fallback to direct token matching against database
            if not matched_cat:
                tokens = [tok.lower() for tok in re.split(r"[/_\-\.\s]+", path) if tok]
                for tok in tokens:
                    if tok in cat_by_name:
                        matched_cat = cat_by_name[tok]
                        break
                    if tok in cat_by_slug:
                        matched_cat = cat_by_slug[tok]
                        break

            if not matched_type:
                tokens = [tok.lower() for tok in re.split(r"[/_\-\.\s]+", path) if tok]
                for tok in tokens:
                    if tok in type_by_name:
                        matched_type = type_by_name[tok]
                        break
                    if tok in type_by_slug:
                        matched_type = type_by_slug[tok]
                        break

            if not matched_cat and matched_type and matched_type.category_id:
                for c in categories:
                    if c.id == matched_type.category_id:
                        matched_cat = c
                        break

            if matched_cat and matched_cat.workspace_id:
                matched_ws = ws_by_id.get(matched_cat.workspace_id)

        # Determine Notion DB mapping (Full-Site and FFFF are strictly unlinked/disabled)
        notion_id = None
        notion_db_name = None
        if matched_ws and matched_ws.slug in ("full-site", "ffff"):
            notion_id = None
            notion_db_name = None
        elif matched_cat and matched_cat.notion_id:
            notion_id = matched_cat.notion_id
            notion_db_name = "Services" if (matched_cat.section == "services" or matched_cat.notion_id == "d74d19f8-6945-4eb0-8d40-e879f3b1d571") else "Goods"
        elif matched_ws and matched_ws.notion_id:
            notion_id = matched_ws.notion_id
            notion_db_name = "Goods" if matched_ws.slug == "qfwfq" else "Services"

        # Smart tag predictions - STRICT ZERO-CREATION POLICY:
        # Only existing canonical tags from the database are ever matched or assigned.
        pred_tag_ids: List[int] = []
        pred_tag_names: List[str] = []

        keyword_heuristics = [
            (re.compile(r"\b(beach\s*hotel|beach)\b", re.I), "Beach Hotel"),
            (re.compile(r"\b(resort\w*)\b", re.I), "Beach Resort"),
            (re.compile(r"\b(hotel\w*|suites?|stay|hospitality)\b", re.I), "Hotel"),
            (re.compile(r"\b(ai|artificial intelligence|llm|copilot|gpt)\b", re.I), "AI"),
            (re.compile(r"\b(3d|threejs|webgl|cgi|render\w*|blender)\b", re.I), "3D"),
            (re.compile(r"\b(audio|sound|podcast|music)\b", re.I), "Audio"),
            (re.compile(r"\b(luxury|luss\w+)\b", re.I), "Luxury"),
            (re.compile(r"\b(car|automotive|auto|motors)\b", re.I), "Car"),
            (re.compile(r"\b(vinted|secondhand|preloved)\b", re.I), "Vinted"),
            (re.compile(r"\b(photo|photography|film)\b", re.I), "Photo"),
            (re.compile(r"\b(sneakers?|shoes?)\b", re.I), "K Classic Sneakers"),
        ]

        for pattern, tag_name in keyword_heuristics:
            if pattern.search(full_str):
                cand_slug = re.sub(r"[^a-z0-9]+", "-", tag_name.lower()).strip("-")
                t = tag_by_slug.get(cand_slug) or tag_by_name.get(tag_name.lower())
                if t:
                    # Filter by workspace compatibility if workspace is matched
                    if matched_ws and t.workspace_id and t.workspace_id != matched_ws.id:
                        continue
                    if t.id is not None and t.id not in pred_tag_ids:
                        pred_tag_ids.append(t.id)
                    if t.name not in pred_tag_names:
                        pred_tag_names.append(t.name)

        tokens = [tok.lower() for tok in re.split(r"[/_\-\.\s]+", path) if tok]
        for tok in tokens:
            t = tag_by_slug.get(tok) or tag_by_name.get(tok)
            if t:
                if matched_ws and t.workspace_id and t.workspace_id != matched_ws.id:
                    continue
                if t.id is not None and t.id not in pred_tag_ids:
                    pred_tag_ids.append(t.id)
                if t.name not in pred_tag_names:
                    pred_tag_names.append(t.name)

        # Smart Element predictions (matching 271 canonical GOODS elements - Goods / Qfwfq only)
        pred_elem_ids: List[int] = []
        pred_elem_names: List[str] = []
        if not is_ffff and (not matched_ws or matched_ws.slug == "qfwfq"):
            for tok in tokens:
                el = elem_by_slug.get(tok) or elem_by_name.get(tok)
                if not el and tok in ("sunglasses", "occhiali", "eyewear"):
                    el = elem_by_slug.get("sunglassess") or elem_by_name.get("sunglassess")
                if el:
                    if el.id is not None and el.id not in pred_elem_ids:
                        pred_elem_ids.append(el.id)
                    if el.name not in pred_elem_names:
                        pred_elem_names.append(el.name)
            for el in elements:
                if " " in el.name or "-" in el.name:
                    if el.name.lower() in full_str:
                        if el.id is not None and el.id not in pred_elem_ids:
                            pred_elem_ids.append(el.id)
                        if el.name not in pred_elem_names:
                            pred_elem_names.append(el.name)

        # Evaluate Taxonomy Instruction Rules (e.g. apply Renovation to Furniture/Electricity/Tiles)
        rule_applied_tags: List[str] = []
        rule_ctx = TaxonomyRuleContext(
            workspace_id=matched_ws.id if matched_ws else None,
            workspace_slug=matched_ws.slug if matched_ws else None,
            dashboard=notion_db_name.lower() if notion_db_name else None,
            notion_db_name=notion_db_name,
            notion_id=notion_id,
            category_id=matched_cat.id if matched_cat else None,
            category_name=matched_cat.name if matched_cat else None,
            type_id=matched_type.id if matched_type else None,
            type_name=matched_type.name if matched_type else None,
            element_names=pred_elem_names,
            element_ids=pred_elem_ids,
            url=clean,
        )
        resolved_rule_tags = evaluate_taxonomy_rules(rule_ctx, session)
        for r_tag in resolved_rule_tags:
            if r_tag.tag_id not in pred_tag_ids:
                pred_tag_ids.append(r_tag.tag_id)
            if r_tag.tag_name not in pred_tag_names:
                pred_tag_names.append(r_tag.tag_name)
            rule_applied_tags.append(f"{r_tag.tag_name} (Rule: {r_tag.rule_name})")

        items.append(
            BatchPreviewItem(
                id=f"url-{idx + 1}",
                raw_url=analysis["raw_url"],
                clean_url=clean,
                domain=domain,
                stripped_params=analysis["stripped_params"],
                has_stripped_params=analysis["has_stripped_params"],
                content_type=content_type,
                workspace_id=matched_ws.id if matched_ws else None,
                workspace_name=matched_ws.name if matched_ws else None,
                workspace_slug=matched_ws.slug if matched_ws else None,
                workspace_icon=matched_ws.icon if matched_ws else None,
                workspace_color=matched_ws.color if matched_ws else None,
                notion_id=notion_id,
                notion_db_name=notion_db_name,
                predicted_category_id=matched_cat.id if matched_cat else None,
                predicted_category_name=matched_cat.name if matched_cat else None,
                predicted_types_id=matched_type.id if matched_type else None,
                predicted_types_name=matched_type.name if matched_type else None,
                tag_ids=pred_tag_ids,
                predicted_tags=pred_tag_names,
                rule_applied_tags=rule_applied_tags,
                element_ids=pred_elem_ids,
                predicted_elements=pred_elem_names,
                selected=True,
            )
        )

    return BatchPreviewResponse(
        count=len(items),
        filename=filename,
        items=items,
        urls=urls,
    )


_LOCALE_ROOT_RE = re.compile(
    r"^/(?:[a-z]{2}(?:-[a-z]{2})?|eu|uk|us|int|global)(?:/(?:home|index(?:\.html?)?)?)?/?$",
    re.I,
)
_ERROR_PATH_RE = re.compile(
    r"(?:/|^)(?:404|not-found|notfound|page-not-found|pagenotfound|error|item-not-found)(?:/|\.html?|$)",
    re.I,
)


def _is_deep_content_path(path: str) -> bool:
    norm = (path or "/").strip()
    if norm in ("", "/", "/index.html", "/index.htm", "/home"):
        return False
    if _LOCALE_ROOT_RE.match(norm):
        return False
    return True


def _detect_redirect_to_home_or_404(orig_url: str, final_url: str, status_code: int) -> tuple[bool, str]:
    """
    Returns (is_bad, reason_label) if the URL returned 404/410 or redirected from a deep
    item/page URL to the site homepage, locale root, or a 404 error page.
    """
    if status_code in (404, 410):
        return True, f"{status_code} Not Found"

    orig_parsed = urlparse(orig_url)
    final_parsed = urlparse(final_url)
    orig_path = orig_parsed.path or "/"
    final_path = final_parsed.path or "/"

    if _ERROR_PATH_RE.search(final_path) or "404" in (final_parsed.query or ""):
        return True, "Redirected to 404"

    if _is_deep_content_path(orig_path):
        norm_final = final_path.rstrip("/") or "/"
        if norm_final in ("/", "/home", "/index.html", "/index.htm") or _LOCALE_ROOT_RE.match(final_path):
            return True, "Redirected to Homepage"

    return False, "OK"


async def _probe_url_reachability(
    client: httpx.AsyncClient, target_url: str
) -> tuple[bool, str, Optional[int], Optional[str]]:
    """
    Probe a single URL and return (is_bad, reason_label, status_code, final_url).
    """
    try:
        resp = await client.get(target_url, follow_redirects=True)
        final_url = str(resp.url)
        status_code = resp.status_code
        is_bad, reason = _detect_redirect_to_home_or_404(target_url, final_url, status_code)
        return is_bad, reason, status_code, final_url
    except Exception as exc:
        logging.debug(f"URL probe exception for {target_url}: {exc}")
        return False, "Unverified", None, None


async def _verify_single_url_item(
    client: httpx.AsyncClient, item: UrlVerifyInput, sem: asyncio.Semaphore
) -> UrlVerifyResult:
    async with sem:
        is_bad_clean, reason_clean, code_clean, final_clean = await _probe_url_reachability(
            client, item.clean_url
        )
        if not is_bad_clean:
            return UrlVerifyResult(
                id=item.id,
                url_status="ok",
                url_status_code=code_clean,
                url_status_label="Live URL",
                final_url=final_clean,
                use_raw_url=False,
            )

        # Clean URL returned 404 or redirected to homepage/404.
        # Double-check original parameterized URL if parameters were stripped!
        if item.has_stripped_params and item.raw_url and item.raw_url != item.clean_url:
            is_bad_raw, reason_raw, code_raw, final_raw = await _probe_url_reachability(
                client, item.raw_url
            )
            if not is_bad_raw:
                return UrlVerifyResult(
                    id=item.id,
                    url_status="params_required",
                    url_status_code=code_raw,
                    url_status_label=f"Params Required (Clean URL {reason_clean})",
                    final_url=final_raw,
                    use_raw_url=True,
                )
            return UrlVerifyResult(
                id=item.id,
                url_status="not_found",
                url_status_code=code_raw or code_clean,
                url_status_label=f"Page / Item Not Found ({reason_raw})",
                final_url=final_raw or final_clean,
                use_raw_url=False,
            )

        return UrlVerifyResult(
            id=item.id,
            url_status="not_found",
            url_status_code=code_clean,
            url_status_label=f"Page / Item Not Found ({reason_clean})",
            final_url=final_clean,
            use_raw_url=False,
        )


@router.post("/batch-verify-urls", response_model=BatchVerifyUrlsResponse)
async def batch_verify_urls(payload: BatchVerifyUrlsRequest):
    """
    Verify batch URLs for 404 Not Found or Homepage Redirects.
    If a stripped clean_url returns 404 or redirects to homepage, double-checks the original
    parameterized raw_url before marking as 'not_found' (Page / Item Not Found) vs 'params_required'.
    """
    if not payload.items:
        return BatchVerifyUrlsResponse(results=[])

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(headers=headers, timeout=6.0, verify=False) as client:
        tasks = [_verify_single_url_item(client, item, sem) for item in payload.items]
        results = await asyncio.gather(*tasks)

    return BatchVerifyUrlsResponse(results=list(results))


@router.post("/batch-execute")
def batch_execute_jobs(
    payload: BatchExecuteRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """
    Enqueue multiple curated scrape jobs with pre-assigned workspace and taxonomies.
    """
    workspace_id = payload.workspace_id
    if payload.scrape_mode == "sitemap" and workspace_id is None:
        full_site_ws = session.exec(select(Workspace).where(Workspace.slug == "full-site")).first()
        if full_site_ws:
            workspace_id = full_site_ws.id

    results = []
    all_jobs = session.exec(select(ScrapeJob)).all()
    jobs_by_norm = {normalize_target_url(j.target_url): j for j in all_jobs}

    for item in payload.items:
        raw_target = item.clean_url or item.url
        target_url = sanitize_url(raw_target)
        if not target_url:
            continue

        item_ws_id = item.workspace_id if item.workspace_id is not None else workspace_id

        normalized_input = normalize_target_url(target_url)
        existing = jobs_by_norm.get(normalized_input)

        if existing:
            if existing.status == "running":
                results.append({"job_id": existing.id, "status": "already_running", "url": target_url})
                continue

            reset_count = reset_job_retryable_pages(session, existing)
            existing.target_url = target_url
            existing.status = "running"
            if item_ws_id is not None:
                existing.workspace_id = item_ws_id
            if item.category_id is not None:
                existing.category_id = item.category_id
            if item.types_id is not None:
                existing.types_id = item.types_id

            session.add(existing)
            session.commit()
            session.refresh(existing)

            # Link tags to existing pages for this job
            if item.tag_ids:
                pages = list(existing.pages) if existing.pages else []
                if not pages:
                    pages = session.exec(select(Page).where((Page.url == target_url) | (Page.clean_url == target_url))).all()
                for p in pages:
                    for tid in item.tag_ids:
                        if not session.get(Tag, tid):
                            continue
                        link_exists = session.exec(
                            select(PageTagLink).where(
                                (PageTagLink.page_id == p.id) & (PageTagLink.tag_id == tid)
                            )
                        ).first()
                        if not link_exists:
                            session.add(PageTagLink(page_id=p.id, tag_id=tid))
                session.commit()

            # Link elements to existing or pending pages for this job
            if item.element_ids:
                target_job = existing if existing else job
                pages = list(target_job.pages) if target_job.pages else []
                if not pages:
                    pages = session.exec(select(Page).where((Page.url == target_url) | (Page.clean_url == target_url))).all()
                if not pages and payload.scrape_mode == "single":
                    new_page = Page(
                        job_id=target_job.id,
                        url=target_url,
                        clean_url=item.clean_url or target_url,
                        title="Untitled",
                        status="pending",
                        workspace_id=item_ws_id,
                        category_id=item.category_id,
                        types_id=item.types_id,
                    )
                    session.add(new_page)
                    session.commit()
                    session.refresh(new_page)
                    pages = [new_page]

                for p in pages:
                    for eid in item.element_ids:
                        link_exists = session.exec(
                            select(PageElementLink).where(
                                (PageElementLink.page_id == p.id) & (PageElementLink.element_id == eid)
                            )
                        ).first()
                        if not link_exists:
                            session.add(PageElementLink(page_id=p.id, element_id=eid))
                session.commit()

            # Link elements to existing pages for this job
            if item.element_ids:
                pages = list(existing.pages) if existing.pages else []
                if not pages:
                    pages = session.exec(select(Page).where((Page.url == target_url) | (Page.clean_url == target_url))).all()
                for p in pages:
                    for eid in item.element_ids:
                        link_exists = session.exec(
                            select(PageElementLink).where(
                                (PageElementLink.page_id == p.id) & (PageElementLink.element_id == eid)
                            )
                        ).first()
                        if not link_exists:
                            session.add(PageElementLink(page_id=p.id, element_id=eid))
                session.commit()

            job_log_manager.mark_job_active(existing.id)
            background_tasks.add_task(
                _dispatch_run_job,
                existing.id,
                existing.target_url,
                payload.scrape_mode,
                0,
                payload.delay_min,
                payload.delay_max,
                payload.session_min,
                payload.session_max,
                payload.cooldown_min,
                payload.cooldown_max,
                payload.large_threshold,
                payload.large_pause_sec,
            )
            results.append({"job_id": existing.id, "status": "restored", "url": target_url, "queued_pages": reset_count})
        else:
            job = ScrapeJob(
                target_url=target_url,
                scrape_mode=payload.scrape_mode,
                workspace_id=item_ws_id,
                category_id=item.category_id,
                types_id=item.types_id,
            )
            session.add(job)
            session.commit()
            session.refresh(job)

            jobs_by_norm[normalized_input] = job

            # Link tags to existing or pending pages for this job
            if item.tag_ids:
                target_job = existing if existing else job
                pages = list(target_job.pages) if target_job.pages else []
                if not pages:
                    pages = session.exec(select(Page).where((Page.url == target_url) | (Page.clean_url == target_url))).all()
                if not pages and payload.scrape_mode == "single":
                    new_page = Page(
                        job_id=target_job.id,
                        url=target_url,
                        clean_url=item.clean_url or target_url,
                        title="Untitled",
                        status="pending",
                        workspace_id=item_ws_id,
                        category_id=item.category_id,
                        types_id=item.types_id,
                    )
                    session.add(new_page)
                    session.commit()
                    session.refresh(new_page)
                    pages = [new_page]

                for p in pages:
                    for tid in item.tag_ids:
                        if not session.get(Tag, tid):
                            continue
                        link_exists = session.exec(
                            select(PageTagLink).where(
                                (PageTagLink.page_id == p.id) & (PageTagLink.tag_id == tid)
                            )
                        ).first()
                        if not link_exists:
                            session.add(PageTagLink(page_id=p.id, tag_id=tid))
                session.commit()

            # Link elements to existing or pending pages for this job
            if item.element_ids:
                target_job = existing if existing else job
                pages = list(target_job.pages) if target_job.pages else []
                if not pages:
                    pages = session.exec(select(Page).where((Page.url == target_url) | (Page.clean_url == target_url))).all()
                if not pages and payload.scrape_mode == "single":
                    new_page = Page(
                        job_id=target_job.id,
                        url=target_url,
                        clean_url=item.clean_url or target_url,
                        title="Untitled",
                        status="pending",
                        workspace_id=item_ws_id,
                        category_id=item.category_id,
                        types_id=item.types_id,
                    )
                    session.add(new_page)
                    session.commit()
                    session.refresh(new_page)
                    pages = [new_page]

                for p in pages:
                    for eid in item.element_ids:
                        link_exists = session.exec(
                            select(PageElementLink).where(
                                (PageElementLink.page_id == p.id) & (PageElementLink.element_id == eid)
                            )
                        ).first()
                        if not link_exists:
                            session.add(PageElementLink(page_id=p.id, element_id=eid))
                session.commit()

            job_log_manager.mark_job_active(job.id)
            background_tasks.add_task(
                _dispatch_run_job,
                job.id,
                target_url,
                payload.scrape_mode,
                0,
                payload.delay_min,
                payload.delay_max,
                payload.session_min,
                payload.session_max,
                payload.cooldown_min,
                payload.cooldown_max,
                payload.large_threshold,
                payload.large_pause_sec,
            )
            results.append({"job_id": job.id, "status": "started", "url": target_url})

    return {
        "queued": len(results),
        "jobs": results,
    }


@router.post("")
def create_job(
    target_url: str,
    background_tasks: BackgroundTasks,
    scrape_mode: str = "single",
    workspace_id: Optional[int] = None,
    max_pages: int = 0,
    delay_min: float = 60.0,
    delay_max: float = 120.0,
    session_min: int = 30,
    session_max: int = 50,
    cooldown_min: float = 180.0,
    cooldown_max: float = 300.0,
    large_threshold: int = 20,
    large_pause_sec: int = 120,
    session: Session = Depends(get_session),
):
    if scrape_mode == "sitemap" and workspace_id is None:
        full_site_ws = session.exec(select(Workspace).where(Workspace.slug == "full-site")).first()
        if full_site_ws:
            workspace_id = full_site_ws.id

    target_url = sanitize_url(target_url)

    normalized_input = normalize_target_url(target_url)
    all_jobs = session.exec(select(ScrapeJob)).all()
    for existing in all_jobs:
        if normalize_target_url(existing.target_url) == normalized_input:
            if existing.status == "running":
                return {"job_id": existing.id, "status": "already_running"}

            # Restore existing job (resetting both failed and Untitled/empty pages)
            reset_count = reset_job_retryable_pages(session, existing)

            existing.target_url = (existing.target_url or "").strip()
            existing.status = "running"
            if workspace_id is not None:
                existing.workspace_id = workspace_id
            session.add(existing)
            session.commit()
            session.refresh(existing)

            job_log_manager.mark_job_active(existing.id)
            background_tasks.add_task(
                _dispatch_run_job,
                existing.id,
                existing.target_url,
                scrape_mode,
                max_pages,
                delay_min,
                delay_max,
                session_min,
                session_max,
                cooldown_min,
                cooldown_max,
                large_threshold,
                large_pause_sec,
            )
            return {"job_id": existing.id, "status": "restored", "queued_pages": reset_count}

    job = ScrapeJob(target_url=target_url, scrape_mode=scrape_mode, workspace_id=workspace_id)
    session.add(job)
    session.commit()
    session.refresh(job)

    job_log_manager.mark_job_active(job.id)
    background_tasks.add_task(
        _dispatch_run_job,
        job.id,
        target_url,
        scrape_mode,
        max_pages,
        delay_min,
        delay_max,
        session_min,
        session_max,
        cooldown_min,
        cooldown_max,
        large_threshold,
        large_pause_sec,
    )
    return {"job_id": job.id, "status": "started"}


@router.get("")
def get_jobs(
    workspace_id: Optional[str] = None,
    session: Session = Depends(get_session)
):
    reconcile_job_statuses(session)
    query = select(ScrapeJob)
    if workspace_id and workspace_id not in ("all", "__all__"):
        if workspace_id.isdigit():
            query = query.where(ScrapeJob.workspace_id == int(workspace_id))
        else:
            ws = session.exec(select(Workspace).where(Workspace.slug == workspace_id)).first()
            if ws:
                query = query.where(ScrapeJob.workspace_id == ws.id)
    jobs = session.exec(query.order_by(ScrapeJob.created_at.desc())).all()
    return [serialize_job_enriched(j) for j in jobs]


@router.get("/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)):
    reconcile_job_statuses(session)
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return serialize_job_enriched(job)


@router.get("/{job_id}/logs")
async def stream_job_logs(job_id: int, session: Session = Depends(get_session)):
    """SSE endpoint: streams log lines for a job with history replay."""
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    is_active = job.status in ("running", "paused")
    fallback_path = os.path.join(PROJECT_ROOT, job.log_path) if job.log_path else None

    async def event_generator():
        history, queue = job_log_manager.subscribe(job_id, fallback_path=fallback_path)
        try:
            # 1. Replay past logs
            for line in history:
                safe = line.replace("\n", " ").replace("\r", "")
                yield f"data: {safe}\n\n"

            # 2. If job is not active, finish immediately
            if not is_active:
                yield f"data: {DONE_SENTINEL}\n\n"
                return

            # 3. Stream live logs
            while True:
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                if line == DONE_SENTINEL:
                    yield f"data: {DONE_SENTINEL}\n\n"
                    break

                safe = line.replace("\n", " ").replace("\r", "")
                yield f"data: {safe}\n\n"
        finally:
            job_log_manager.unsubscribe(job_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{job_id}/logs/history")
def get_job_log_history(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    fallback_path = os.path.join(PROJECT_ROOT, job.log_path) if job.log_path else None
    history = job_log_manager.get_history(job_id, fallback_path=fallback_path)
    return {
        "job_id": job_id,
        "status": job.status,
        "log_path": job.log_path,
        "logs": history,
    }


@router.get("/{job_id}/pages")
def get_job_pages(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    pages = session.exec(select(Page).where(Page.job_id == job_id)).all()
    return [serialize_page_enriched(p, job) for p in pages]


@router.post("/{job_id}/cancel")
def cancel_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        return {"status": "not found"}
    if job.status == "running":
        job.status = "cancelled"
        session.commit()
    return {"status": "cancelled"}


@router.post("/{job_id}/pause")
def pause_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        return {"status": "not found"}
    if job.status == "running":
        job.status = "paused"
        session.commit()
    return {"status": "paused"}


@router.post("/{job_id}/resume")
def resume_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    session_min: int = 30,
    session_max: int = 50,
    cooldown_min: float = 180.0,
    cooldown_max: float = 300.0,
    delay_min: float = 60.0,
    delay_max: float = 120.0,
    session: Session = Depends(get_session),
):
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")

    reset_count = reset_job_retryable_pages(session, job)

    job.target_url = (job.target_url or "").strip()
    job.status = "running"
    session.add(job)
    session.commit()
    session.refresh(job)

    max_pages = max(0, job.total_pages or 0)
    job_log_manager.mark_job_active(job.id)
    background_tasks.add_task(
        _dispatch_run_job,
        job.id,
        job.target_url,
        job.scrape_mode,
        max_pages,
        delay_min,
        delay_max,
        session_min,
        session_max,
        cooldown_min,
        cooldown_max,
    )
    return {"status": "resumed", "queued_pages": reset_count}


@router.post("/{job_id}/rescrape-untitled")
def rescrape_untitled_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    session_min: int = 30,
    session_max: int = 50,
    cooldown_min: float = 180.0,
    cooldown_max: float = 300.0,
    delay_min: float = 60.0,
    delay_max: float = 120.0,
    session: Session = Depends(get_session),
):
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")

    reset_count = reset_job_retryable_pages(session, job)
    pending_count = len(
        session.exec(select(Page).where(Page.job_id == job_id, Page.status == "pending")).all()
    )
    if reset_count == 0 and pending_count == 0 and job.scrape_mode == "sitemap":
        return {"status": "nothing_to_rescrape", "job_id": job.id, "queued_pages": 0}

    job.target_url = (job.target_url or "").strip()
    job.status = "running"
    session.add(job)
    session.commit()
    session.refresh(job)

    max_pages = max(0, job.total_pages or 0)
    job_log_manager.mark_job_active(job.id)
    background_tasks.add_task(
        _dispatch_run_job,
        job.id,
        job.target_url,
        job.scrape_mode,
        max_pages,
        delay_min,
        delay_max,
        session_min,
        session_max,
        cooldown_min,
        cooldown_max,
    )
    return {"status": "resumed", "job_id": job.id, "queued_pages": reset_count}


@router.delete("/{job_id}")
async def delete_job(
    job_id: int,
    keep_logs: bool = False,
    session: Session = Depends(get_session),
):
    job = session.get(ScrapeJob, job_id)
    if not job:
        return {"status": "not found"}

    # 1. Determine folder to delete
    domain = urlparse(job.target_url).netloc.replace(".", "_")
    folder_to_delete = os.path.join(DOWNLOADS_DIR, f"job_{job_id}_{domain}")
    if job.log_path:
        rel_dir = os.path.dirname(job.log_path).lstrip("/").lstrip("./")
        if rel_dir.startswith("_downloads/"):
            rel_dir = rel_dir.replace("_downloads/", "", 1)
        folder_to_delete = os.path.join(DOWNLOADS_DIR, rel_dir)

    fallback_path = os.path.join(PROJECT_ROOT, job.log_path) if job.log_path else None

    # 2. Preserve logs if requested
    if keep_logs:
        archive_dir = os.path.join(DOWNLOADS_DIR, "_logs")
        job_log_manager.preserve_log_file(job_id, archive_dir, fallback_path=fallback_path)

    # 3. Clean up database pages and files
    pages = session.exec(select(Page).where(Page.job_id == job_id)).all()
    for page in pages:
        if not os.path.exists(folder_to_delete):
            path = page.markdown_path or page.screenshot_path
            if path and "_downloads/" in path:
                parts = path.split("/")
                if "_downloads" in parts:
                    idx = parts.index("_downloads")
                    if idx + 1 < len(parts):
                        folder_name = parts[idx + 1]
                        folder_to_delete = os.path.join(DOWNLOADS_DIR, folder_name)
        session.delete(page)

    if os.path.exists(folder_to_delete):
        try:
            shutil.rmtree(folder_to_delete)
        except Exception as e:
            logging.error(f"Error deleting folder {folder_to_delete}: {e}")

    session.delete(job)
    session.commit()
    return {"status": "deleted"}


@router.patch("/{job_id}/taxonomy")
@router.put("/{job_id}/taxonomy")
def update_job_taxonomy(
    job_id: int,
    payload: JobTaxonomyUpdate,
    session: Session = Depends(get_session)
):
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    fields_set = payload.model_fields_set

    if "workspace_id" in fields_set:
        if payload.workspace_id is not None:
            ws = session.get(Workspace, payload.workspace_id)
            if not ws:
                raise HTTPException(status_code=404, detail="Workspace not found")
        job.workspace_id = payload.workspace_id

    if "category_id" in fields_set:
        if payload.category_id is not None:
            cat = session.get(Category, payload.category_id)
            if not cat:
                raise HTTPException(status_code=404, detail="Category not found")
            if "workspace_id" not in fields_set and cat.workspace_id:
                job.workspace_id = cat.workspace_id
        job.category_id = payload.category_id

    if "types_id" in fields_set:
        if payload.types_id is not None:
            t = session.get(Types, payload.types_id)
            if not t:
                raise HTTPException(status_code=404, detail="Types not found")
            if "category_id" not in fields_set and t.category_id:
                job.category_id = t.category_id
        job.types_id = payload.types_id

    # If category changed and types_id was not explicitly specified, verify if existing types_id matches category
    if "category_id" in fields_set and "types_id" not in fields_set:
        if job.types_id is not None:
            existing_type = session.get(Types, job.types_id)
            if existing_type and existing_type.category_id != job.category_id:
                job.types_id = None

    session.add(job)

    # Bulk update all pages belonging to this job
    pages = session.exec(select(Page).where(Page.job_id == job_id)).all()
    for page in pages:
        if "workspace_id" in fields_set or ("category_id" in fields_set and job.workspace_id is not None):
            page.workspace_id = job.workspace_id

        if "category_id" in fields_set:
            page.category_id = job.category_id
            page.categorization_source = "user_manual"
            page.categorization_confidence = 1.0
            if job.types_id is None:
                page.types_id = None

        if "types_id" in fields_set:
            page.types_id = job.types_id
            page.categorization_source = "user_manual"
            page.categorization_confidence = 1.0
            if job.category_id is not None:
                page.category_id = job.category_id

        session.add(page)

    session.commit()
    session.refresh(job)
    for page in pages:
        session.refresh(page)

    return {
        "job": serialize_job_enriched(job),
        "pages": [serialize_page_enriched(p, job) for p in pages],
        "updated_pages_count": len(pages),
    }

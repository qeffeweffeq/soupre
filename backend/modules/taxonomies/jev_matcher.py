import json
import os
import logging
from difflib import get_close_matches, SequenceMatcher
from pathlib import Path

log = logging.getLogger(__name__)

_TAXONOMY_CACHE: dict[str, dict] = {}

# Canonical synonyms and keyword stems mapped to standard taxonomy terms
SYNONYMS: dict[str, str] = {
    "tech": "Technology",
    "developer": "Technology",
    "developers": "Technology",
    "development": "Technology",
    "dev": "Technology",
    "code": "Technology",
    "coding": "Technology",
    "software": "Technology",
    "programming": "Technology",
    "programmer": "Technology",
    "engineer": "Technology",
    "engineering": "Technology",
    "app": "Application",
    "application": "Application",
    "apps": "Application",
    "plugin": "Plugin",
    "plugins": "Plugin",
    "extension": "Plugin",
    "extensions": "Plugin",
    "service": "Service",
    "services": "Service",
    "tool": "Tools",
    "tools": "Tools",
    "utility": "Utils",
    "utilities": "Utils",
    "util": "Utils",
    "model": "Model",
    "models": "Model",
    "llm": "Model",
    "gpt": "Model",
    "designer": "Design",
    "design": "Design",
    "designs": "Design",
    "ui": "Design",
    "ux": "Design",
    "graphics": "Design",
    "writer": "Writing",
    "writing": "Writing",
    "copywriter": "Writing",
    "copywriting": "Writing",
    "content": "Writing",
    "art": "Artists",
    "artist": "Artists",
    "artists": "Artists",
    "creative": "Creativity",
    "creativity": "Creativity",
    "finance": "Financial",
    "financial": "Financial",
    "investing": "Financial",
    "investment": "Financial",
    "business": "Management",
    "management": "Management",
    "legal": "Legal",
    "marketing": "Marketing",
    "seo": "Marketing",
    "ads": "Marketing",
    "advertising": "Marketing",
    "shop": "Goods & Commerce",
    "shopping": "Goods & Commerce",
    "store": "Goods & Commerce",
    "ecommerce": "Goods & Commerce",
    "commerce": "Goods & Commerce",
    "travel": "Traveller",
    "traveller": "Traveller",
    "tourism": "Traveller",
}


def _find_dbs_dir() -> Path:
    """Find modules/_dbs directory across different execution contexts."""
    candidates = [
        Path("backend/modules/_dbs"),
        Path("modules/_dbs"),
        Path(__file__).resolve().parent.parent / "_dbs",
        Path(__file__).resolve().parent.parent.parent / "modules" / "_dbs",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return Path(__file__).resolve().parent.parent / "_dbs"


def load_taxonomy(db_key: str) -> dict:
    """
    Loads the exhaustive taxonomy dictionary for the given database.
    Looks under modules/_dbs/{workspace}/{dashboard}/{db}/_schema/_dbs/_taxonomies.json first,
    falling back to dashboard-level _taxonomies.json if present.
    Caches results in memory for performance.
    """
    key_clean = (db_key or "").strip().lower().replace("_", "-")
    if key_clean in _TAXONOMY_CACHE:
        return _TAXONOMY_CACHE[key_clean]

    dbs_base = _find_dbs_dir()

    if dbs_base.exists() and dbs_base.is_dir():
        # Check config registry if available
        try:
            from modules import config
            cfg = getattr(config, "DATABASES", {}).get(db_key) or getattr(config, "DATABASES", {}).get(key_clean)
            if cfg:
                ws_slug = (cfg.get("workspace", "") or "").lower().replace("_", "-")
                dash_slug = (cfg.get("dashboard", "") or "").lower().replace("_", "-")
                specific_tax = dbs_base / ws_slug / dash_slug / key_clean / "_schema" / "_dbs" / "_taxonomies.json"
                if specific_tax.is_file():
                    with open(specific_tax, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        _TAXONOMY_CACHE[key_clean] = data
                        return data
        except Exception:
            pass

        matching_tax: Path | None = None
        other_tax_files: list[Path] = []

        all_tax_files = sorted(
            list(dbs_base.glob("*/*/*/_schema/_dbs/_taxonomies.json"))
            + list(dbs_base.glob("*/*/_schema/_dbs/_taxonomies.json"))
            + list(dbs_base.glob("*/_schema/_dbs/_taxonomies.json"))
        )

        for tax_file in all_tax_files:
            rel_parts = tax_file.relative_to(dbs_base).parts
            if len(rel_parts) >= 4 and rel_parts[-4].lower().replace("_", "-") == key_clean:
                matching_tax = tax_file
                break
            if len(rel_parts) >= 3 and rel_parts[-3].lower().replace("_", "-") == key_clean:
                matching_tax = tax_file
                break
            schema_dir = tax_file.parent
            if (schema_dir / f"{key_clean}.json").is_file():
                matching_tax = tax_file
                break
            other_tax_files.append(tax_file)

        target_files = [matching_tax] if matching_tax else other_tax_files
        for path in target_files:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _TAXONOMY_CACHE[key_clean] = data
                    return data
            except Exception as exc:
                log.error(f"[JEV] Failed to load taxonomy from {path}: {exc}")

    log.warning(
        f"[JEV] Taxonomy file not found for {db_key} under modules/_dbs/. "
        "Relation matching will degrade to raw inputs."
    )
    _TAXONOMY_CACHE[key_clean] = {}
    return {}


def match_taxonomy_values(
    db_key: str,
    relation_key: str,
    proposed_values: list[str],
    auto_create: bool = False,
    fuzzy_cutoff: float = 0.75,
) -> list[str]:
    """
    JEV-like Validator: Matches proposed values against existing taxonomy values.
    Uses exact, case-insensitive, synonym dictionary, prefix matching, and
    difflib.SequenceMatcher fuzzy matching to enforce valid relation options.

    Returns canonical string matches that safely align with taxonomy schema.
    """
    if not proposed_values:
        return []

    taxonomy = load_taxonomy(db_key)
    valid_options = taxonomy.get(relation_key)

    if valid_options is None:
        log.debug(
            f"[JEV:{db_key}:{relation_key}] No predefined taxonomy options found; "
            f"accepting proposed values: {proposed_values}"
        )
        return proposed_values

    matched: list[str] = []
    lower_options = {opt.lower(): opt for opt in valid_options}

    for val in proposed_values:
        val_clean = val.strip()
        if not val_clean:
            continue
        val_lower = val_clean.lower()

        # 1. Exact match
        if val_clean in valid_options:
            log.debug(f"[JEV:{db_key}:{relation_key}]   ✓ Exact match: '{val_clean}'")
            matched.append(val_clean)
            continue

        # 2. Case-insensitive match
        if val_lower in lower_options:
            canonical = lower_options[val_lower]
            log.debug(f"[JEV:{db_key}:{relation_key}]   ℹ Case-insensitive match: '{val}' -> '{canonical}'")
            matched.append(canonical)
            continue

        # 3. Canonical synonym / stem dictionary match
        if val_lower in SYNONYMS:
            syn_target = SYNONYMS[val_lower]
            if syn_target in valid_options:
                log.debug(f"[JEV:{db_key}:{relation_key}]   ★ Synonym match: '{val}' -> '{syn_target}'")
                matched.append(syn_target)
                continue
            if syn_target.lower() in lower_options:
                canonical = lower_options[syn_target.lower()]
                log.debug(f"[JEV:{db_key}:{relation_key}]   ★ Synonym match (lower): '{val}' -> '{canonical}'")
                matched.append(canonical)
                continue

        # 4. Prefix or Substring match (e.g. 'tech' in 'Technology')
        prefix_matched = None
        if len(val_lower) >= 3:
            for opt_lower, opt_canon in lower_options.items():
                if opt_lower.startswith(val_lower) or (len(val_lower) >= 4 and val_lower in opt_lower):
                    prefix_matched = opt_canon
                    break
        if prefix_matched:
            log.debug(f"[JEV:{db_key}:{relation_key}]   ~ Prefix match: '{val}' -> '{prefix_matched}'")
            matched.append(prefix_matched)
            continue

        # 5. Fuzzy match via SequenceMatcher / get_close_matches
        matches = get_close_matches(val_clean, valid_options, n=1, cutoff=fuzzy_cutoff)
        if matches:
            ratio = SequenceMatcher(None, val_lower, matches[0].lower()).ratio()
            log.debug(
                f"[JEV:{db_key}:{relation_key}]   ~ Fuzzy match: '{val}' -> '{matches[0]}' "
                f"(similarity={ratio:.2f}, cutoff={fuzzy_cutoff:.2f})"
            )
            matched.append(matches[0])
            continue

        # SequenceMatcher scan across lower_options with relaxed ratio
        best_opt = None
        best_ratio = 0.0
        for opt_lower, opt_canon in lower_options.items():
            r = SequenceMatcher(None, val_lower, opt_lower).ratio()
            if r > best_ratio and r >= fuzzy_cutoff:
                best_ratio = r
                best_opt = opt_canon
        if best_opt:
            log.debug(
                f"[JEV:{db_key}:{relation_key}]   ~ Fuzzy ratio match: '{val}' -> '{best_opt}' "
                f"(ratio={best_ratio:.2f})"
            )
            matched.append(best_opt)
            continue

        # 6. Auto-create fallback
        if auto_create:
            log.debug(f"[JEV:{db_key}:{relation_key}]   + Auto-create enabled: accepted new option '{val_clean}'")
            matched.append(val_clean)
        else:
            log.debug(f"[JEV:{db_key}:{relation_key}]   ✗ Rejected: '{val_clean}' has no close match in taxonomy")

    # Deduplicate while preserving order
    result = list(dict.fromkeys(matched))
    log.info(f"[JEV:{db_key}:{relation_key}] Resolved {len(proposed_values)} input(s) -> {len(result)} option(s): {result}")
    return result


def extract_taxonomies_from_text(db_key: str, relation_key: str, text: str) -> list[str]:
    """
    Scans a block of text and returns all taxonomy options that appear within it.
    Matches are case-insensitive and enforce word boundaries.
    """
    if not text:
        return []

    import re
    taxonomy = load_taxonomy(db_key)
    valid_options = taxonomy.get(relation_key, [])

    if not valid_options:
        log.debug(f"[JEV:{db_key}:{relation_key}] No options in taxonomy to scan for.")
        return []

    matched = []
    text_lower = text.lower()

    for option in valid_options:
        # Skip tiny words to avoid false positives, except specific ones like AI, 3D
        if len(option) < 3 and option.upper() not in ("AI", "3D"):
            continue

        # Use regex word boundaries for safety
        pattern = r"\b" + re.escape(option.lower()) + r"\b"
        if re.search(pattern, text_lower):
            matched.append(option)

    return list(dict.fromkeys(matched))

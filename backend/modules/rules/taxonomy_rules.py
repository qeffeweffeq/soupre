"""Taxonomy Instruction Rules Engine.

Declarative rules engine for enforcing canonical taxonomy relationships and tag assignment,
strictly disallowing dynamic creation of non-existent tags.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field
from sqlmodel import Session, select

try:
    from database.models import Tag, Workspace
except ImportError:
    from backend.database.models import Tag, Workspace

logger = logging.getLogger("taxonomy_rules")

# Multilingual and domain synonyms for canonical tags
# Keys: lowercase canonical or alias name -> Value: lowercase canonical DB tag name or slug
TAG_SYNONYMS: Dict[str, str] = {
    "renovation": "ristrutturazione",
    "ristrutturazione": "ristrutturazione",
}


class TaxonomyRuleContext(BaseModel):
    """Context information used to evaluate taxonomy instruction rules."""
    workspace_id: Optional[int] = None
    workspace_slug: Optional[str] = None
    dashboard: Optional[str] = None
    notion_db_name: Optional[str] = None
    notion_id: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    type_id: Optional[int] = None
    type_name: Optional[str] = None
    element_names: List[str] = Field(default_factory=list)
    element_ids: List[int] = Field(default_factory=list)
    url: Optional[str] = None


class TaxonomyInstructionRule(BaseModel):
    """Declarative rule specification for auto-applying canonical tags."""
    id: str
    name: str
    description: str
    workspace_slug: Optional[str] = None
    dashboard: Optional[str] = None
    database_name: Optional[str] = None
    database_id: Optional[str] = None
    match_types: List[str] = Field(default_factory=list)
    match_categories: List[str] = Field(default_factory=list)
    match_elements: List[str] = Field(default_factory=list)
    url_patterns: List[str] = Field(default_factory=list)
    apply_tag_names: List[str] = Field(default_factory=list)
    priority: int = 100
    is_active: bool = True

    def matches(self, ctx: TaxonomyRuleContext) -> bool:
        if not self.is_active:
            return False

        # Match workspace if specified
        if self.workspace_slug:
            if not ctx.workspace_slug or ctx.workspace_slug.lower() != self.workspace_slug.lower():
                return False

        # Match dashboard if specified (e.g. "goods")
        if self.dashboard:
            ctx_dash = (ctx.dashboard or ctx.notion_db_name or "").lower()
            if ctx_dash != self.dashboard.lower():
                return False

        # Match database name/id if specified
        if self.database_name:
            ctx_db = (ctx.notion_db_name or "").lower()
            if self.database_name.lower() not in (ctx_db, "items" if ctx_db == "goods" else ctx_db):
                return False

        if self.database_id and ctx.notion_id:
            if self.database_id.lower() != ctx.notion_id.lower():
                return False

        # Match Type condition (case-insensitive)
        if self.match_types:
            if not ctx.type_name:
                return False
            norm_types = {t.lower().strip() for t in self.match_types}
            if ctx.type_name.lower().strip() not in norm_types:
                return False

        # Match Category condition if present
        if self.match_categories:
            if not ctx.category_name:
                return False
            norm_cats = {c.lower().strip() for c in self.match_categories}
            if ctx.category_name.lower().strip() not in norm_cats:
                return False

        # Match Elements if present
        if self.match_elements:
            norm_rule_elems = {e.lower().strip() for e in self.match_elements}
            ctx_elems = {e.lower().strip() for e in ctx.element_names}
            if not norm_rule_elems.intersection(ctx_elems):
                return False

        # Match URL patterns if present
        if self.url_patterns:
            if not ctx.url:
                return False
            matched_pattern = False
            for pat in self.url_patterns:
                if re.search(pat, ctx.url, re.IGNORECASE):
                    matched_pattern = True
                    break
            if not matched_pattern:
                return False

        return True


class ResolvedRuleTag(BaseModel):
    """A canonical tag successfully resolved in the database by an instruction rule."""
    tag_id: int
    tag_name: str
    rule_id: str
    rule_name: str


# Pre-defined built-in taxonomy instruction rules
BUILTIN_TAXONOMY_RULES: List[TaxonomyInstructionRule] = [
    TaxonomyInstructionRule(
        id="goods-items-renovation",
        name="Goods Items: Renovation Tagging",
        description="Apply 'Renovation' (Ristrutturazione) tag to all items in the GOODS dashboard of Items DB where Type is Furniture, Electricity, or Tiles.",
        workspace_slug="qfwfq",
        dashboard="goods",
        database_name="goods",
        match_types=["Furniture", "Electricity", "Tiles"],
        apply_tag_names=["Renovation", "Ristrutturazione"],
        priority=10,
    ),
    TaxonomyInstructionRule(
        id="ffff-design-brands-routing",
        name="FFFF: Design Brands Workspace Routing",
        description="Auto-route Pentagram, Behance, and It's Nice That to dedicated FFFF workspace (isolated from Notion).",
        workspace_slug="ffff",
        url_patterns=[
            r"pentagram\.com",
            r"behance\.(?:net|com)",
            r"itsnicethat\.com",
            r"its-nice-that",
        ],
        priority=1,
    ),
]


def resolve_canonical_tag_in_db(
    tag_identifier: str,
    workspace_id: Optional[int],
    session: Session,
) -> Optional[Tag]:
    """Strictly resolve a tag name/slug against existing Tag records in SQLite.
    
    Zero-creation policy: Under NO circumstances creates a new tag record.
    Returns None if no matching tag is found in the database.
    """
    clean_ident = tag_identifier.strip().lower()
    canonical_name = TAG_SYNONYMS.get(clean_ident, clean_ident)
    cand_slug = re.sub(r"[^a-z0-9]+", "-", canonical_name).strip("-")

    # 1. Try matching by slug or name within target workspace
    query = select(Tag)
    if workspace_id is not None:
        # Check workspace-specific first, then global (workspace_id is None)
        query = query.where((Tag.workspace_id == workspace_id) | (Tag.workspace_id == None))  # noqa: E711

    tags = session.exec(query).all()

    # Priority 1: exact match on workspace
    for t in tags:
        if t.workspace_id == workspace_id:
            if t.slug.lower() == cand_slug or t.name.lower() == canonical_name or t.name.lower() == clean_ident:
                return t

    # Priority 2: global match
    for t in tags:
        if t.slug.lower() == cand_slug or t.name.lower() == canonical_name or t.name.lower() == clean_ident:
            return t

    logger.warning(
        "Taxonomy rule requested tag '%s' (normalized: '%s'), but it does not exist in Tag database for workspace %s. Zero-creation policy strictly enforced.",
        tag_identifier,
        canonical_name,
        workspace_id,
    )
    return None


def evaluate_taxonomy_rules(
    ctx: TaxonomyRuleContext,
    session: Session,
    rules: Optional[List[TaxonomyInstructionRule]] = None,
) -> List[ResolvedRuleTag]:
    """Evaluate active taxonomy rules against the given context and return resolved canonical tags."""
    if rules is None:
        rules = BUILTIN_TAXONOMY_RULES

    # Sort by priority
    sorted_rules = sorted(rules, key=lambda r: r.priority)
    resolved: List[ResolvedRuleTag] = []
    seen_tag_ids: Set[int] = set()

    for rule in sorted_rules:
        if not rule.matches(ctx):
            continue

        for tag_name in rule.apply_tag_names:
            matched_tag = resolve_canonical_tag_in_db(tag_name, ctx.workspace_id, session)
            if matched_tag and matched_tag.id is not None and matched_tag.id not in seen_tag_ids:
                seen_tag_ids.add(matched_tag.id)
                resolved.append(
                    ResolvedRuleTag(
                        tag_id=matched_tag.id,
                        tag_name=matched_tag.name,
                        rule_id=rule.id,
                        rule_name=rule.name,
                    )
                )

    return resolved


def get_all_rules() -> List[TaxonomyInstructionRule]:
    """Return all configured taxonomy rules."""
    return BUILTIN_TAXONOMY_RULES

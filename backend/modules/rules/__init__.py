"""Rules module for taxonomy and workflow automation."""
try:
    from modules.rules.taxonomy_rules import (
        TaxonomyInstructionRule,
        TaxonomyRuleContext,
        ResolvedRuleTag,
        BUILTIN_TAXONOMY_RULES,
        evaluate_taxonomy_rules,
        resolve_canonical_tag_in_db,
        get_all_rules,
    )
except ImportError:
    from backend.modules.rules.taxonomy_rules import (
        TaxonomyInstructionRule,
        TaxonomyRuleContext,
        ResolvedRuleTag,
        BUILTIN_TAXONOMY_RULES,
        evaluate_taxonomy_rules,
        resolve_canonical_tag_in_db,
        get_all_rules,
    )

__all__ = [
    "TaxonomyInstructionRule",
    "TaxonomyRuleContext",
    "ResolvedRuleTag",
    "BUILTIN_TAXONOMY_RULES",
    "evaluate_taxonomy_rules",
    "resolve_canonical_tag_in_db",
    "get_all_rules",
]

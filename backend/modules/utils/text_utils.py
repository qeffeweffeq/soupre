import re
import time
import random
import string


def random_sleep(min_seconds=1, max_seconds=5, human_like=True):
    """Sleep for a random amount of time within the given range or using human-like patterns"""
    if human_like:
        time.sleep(human_like_delay())
    else:
        time.sleep(random.uniform(min_seconds, max_seconds))


def clean_text(text):
    """Clean text while preserving meaningful whitespace and formatting"""
    if not text:
        return ""

    # Handle case where text is a list
    if isinstance(text, list):
        return ' '.join([clean_text(item) for item in text])

    # Convert to string if not already
    if not isinstance(text, str):
        text = str(text)

    # Convert non-breaking spaces to regular spaces
    text = text.replace('\xa0', ' ')

    # Normalize whitespace (but preserve paragraph breaks)
    text = re.sub(r'[\t ]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Remove leading/trailing whitespace
    text = text.strip()

    # Remove form input placeholders and labels that got mixed in
    text = re.sub(r'input type=.*?>', '', text)
    text = re.sub(r'<label.*?label>', '', text)

    return text


def sanitize_filename(filename):
    """Create a valid filename from a string"""
    # Remove invalid characters
    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    sanitized = ''.join(c for c in filename if c in valid_chars)
    # Replace spaces with underscores
    sanitized = sanitized.replace(' ', '_')
    # Limit length
    return sanitized[:100]


def human_like_delay():
    """Mimic human browsing patterns with natural timing variations"""
    # Base delay
    delay = random.normalvariate(2.5, 1.2)  # Normal distribution

    # Add occasional longer pauses (e.g., "reading" time)
    if random.random() < 0.2:  # 20% chance
        delay += random.uniform(4, 12)

    return max(0.5, delay)  # Ensure minimum delay


def is_meaningful_content(text, min_length=50):
    """Check if text contains meaningful content (not just navigation or footer text)"""
    # Clean the text
    text = clean_text(text)

    # Check length
    if len(text) < min_length:
        return False

    # Check for common non-content phrases
    non_content_phrases = [
        'cookie policy', 'privacy policy', 'terms of service',
        'all rights reserved', 'copyright', 'menu', 'navigation',
        'search', 'login', 'sign in', 'register'
    ]

    text_lower = text.lower()
    if any(phrase in text_lower for phrase in non_content_phrases) and len(text) < 200:
        return False

    # Count the ratio of links to text (high ratio suggests navigation)
    link_ratio = text.count('http') / (len(text) + 1)
    if link_ratio > 0.1:  # More than 10% of text is links
        return False

    return True


def get_element_attr_as_string(element, attr_name, default=''):
    """Safely get an element attribute as a string"""
    attr_value = element.get(attr_name, default)

    if isinstance(attr_value, list):
        return ' '.join(str(item) for item in attr_value)
    elif not isinstance(attr_value, str):
        return str(attr_value)

    return attr_value


def ensure_string(value):
    """Convert any value to a string safely"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        # Convert each item to string and join
        return ' '.join(ensure_string(item) for item in value)
    # For any other type, use str()
    return str(value)


def to_param_case(text: str) -> str:
    """
    Slugifies arbitrary string into param-case (kebab-case).
    Example: 'GOODS' -> 'goods', '🛍 Shops' -> 'shops', 'Web Services' -> 'web-services'
    """
    if not text:
        return ""

    # Replace underscores with spaces
    s = text.replace("_", " ")

    # Insert space before camelCase / PascalCase capital letters
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)

    # Lowercase
    s = s.lower()

    # Replace any non-alphanumeric character sequences with a hyphen
    s = re.sub(r"[^a-z0-9]+", "-", s)

    # Strip leading and trailing hyphens
    s = s.strip("-")

    return s or "unnamed"


slugify = to_param_case


CITY_COUNTRY = {
    "milano": "Italy", "milan": "Italy", "roma": "Italy", "rome": "Italy",
    "torino": "Italy", "turin": "Italy", "firenze": "Italy", "florence": "Italy",
    "venezia": "Italy", "venice": "Italy", "bologna": "Italy", "napoli": "Italy",
    "naples": "Italy", "treviso": "Italy", "genova": "Italy",
    "london": "United Kingdom", "manchester": "United Kingdom",
    "edinburgh": "United Kingdom", "bristol": "United Kingdom",
    "berlin": "Germany", "hamburg": "Germany", "munich": "Germany",
    "münchen": "Germany",
    "paris": "France", "lyon": "France", "marseille": "France",
    "barcelona": "Spain", "madrid": "Spain", "valencia": "Spain",
    "lisbon": "Portugal", "lisboa": "Portugal", "porto": "Portugal",
    "amsterdam": "Netherlands", "rotterdam": "Netherlands",
    "brussels": "Belgium", "bruxelles": "Belgium",
    "zurich": "Switzerland", "zürich": "Switzerland", "basel": "Switzerland",
    "geneva": "Switzerland",
    "copenhagen": "Denmark", "københavn": "Denmark",
    "stockholm": "Sweden", "oslo": "Norway", "helsinki": "Finland",
    "vienna": "Austria", "wien": "Austria",
    "prague": "Czech Republic", "praha": "Czech Republic",
    "warsaw": "Poland",
    "tokyo": "Japan", "osaka": "Japan", "kyoto": "Japan",
    "new york": "United States", "los angeles": "United States",
    "san francisco": "United States", "chicago": "United States",
    "portland": "United States", "austin": "United States",
    "brooklyn": "United States",
    "toronto": "Canada", "montreal": "Canada", "vancouver": "Canada",
    "sydney": "Australia", "melbourne": "Australia",
    "são paulo": "Brazil", "buenos aires": "Argentina",
    "dubai": "UAE", "singapore": "Singapore", "seoul": "South Korea",
    "taipei": "Taiwan", "mumbai": "India",
}

COUNTRY_CODES = {
    "it": "Italy", "es": "Spain", "uk": "United Kingdom",
    "us": "United States", "de": "Germany", "fr": "France",
    "pt": "Portugal", "ch": "Switzerland", "jp": "Japan",
    "nl": "Netherlands", "be": "Belgium", "se": "Sweden",
    "dk": "Denmark", "no": "Norway", "at": "Austria",
    "au": "Australia", "ca": "Canada", "br": "Brazil",
    "global": "Global",
}


def expand_location(hint: str) -> str:
    """Expand a bare city name to ``City, Country`` using known lookups."""
    if not hint:
        return hint
    if "," in hint:
        return hint
    key = hint.strip().lower()
    if key in COUNTRY_CODES:
        return COUNTRY_CODES[key]
    if key in CITY_COUNTRY:
        return f"{hint.strip()}, {CITY_COUNTRY[key]}"
    return hint.strip()


_LOWERCASE_WORDS = {
    'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
    'for', 'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were',
    'y', 'e', 'di', 'da', 'de', 'del', 'la', 'le', 'il', 'et', 'und', 'en',
}


def _looks_like_proper_noun(segment: str) -> bool:
    """Return True if *segment* <= 4 words and most words start uppercase."""
    words = segment.split()
    if not words or len(words) > 4:
        return False
    significant = [w for w in words if w.lower() not in _LOWERCASE_WORDS]
    if not significant:
        return False

    generic_terms = {"portfolio", "home", "website", "welcome", "official", "site", "page", "index"}
    if any(w.lower() in generic_terms for w in words):
        return False

    upper_count = sum(1 for w in significant if w[0].isupper())
    return upper_count > len(significant) / 2


_DOMAIN_PATTERN = re.compile(
    r"\b[a-zA-Z0-9][-a-zA-Z0-9]*\.(?:org|com|net|io|ai|app|dev|co|edu|gov|it|de|fr|uk|cc|me|fm|so|is|tv)\b",
    re.IGNORECASE,
)

_LEADING_DOMAIN = re.compile(
    r"^([a-zA-Z0-9][-a-zA-Z0-9]*\.(?:org|com|net|io|ai|app|dev|co|edu|gov|it|de|fr|uk|cc|me|fm|so|is|tv))\s+(.+)$",
    re.IGNORECASE,
)


def parse_name(raw: str) -> tuple[str, str | None, str | None]:
    """Clean a raw page title / name into (cleaned_name, location_hint, short_about)."""
    if not raw or not raw.strip():
        return ("", None, None)

    text = raw.strip()
    location_hint: str | None = None
    short_about: str | None = None

    parts: list[str] | None = None
    m_dom = _LEADING_DOMAIN.match(text)
    if m_dom:
        parts = [m_dom.group(1).strip(), m_dom.group(2).strip()]
    else:
        for sep in (" | ", " – ", " — ", " - ", " : ", ": ", " · ", " • ", " // "):
            if sep in text:
                parts = [p.strip() for p in text.split(sep, 1)]
                break

    if parts and len(parts) == 2:
        left_seg, right_seg = parts[0], parts[1]

        def _is_known_loc(seg: str) -> bool:
            s = seg.lower()
            if s in CITY_COUNTRY or s in COUNTRY_CODES:
                return True
            if s in (v.lower() for v in COUNTRY_CODES.values()):
                return True
            if "," in s:
                sparts = [p.strip() for p in s.split(",")]
                if sparts and sparts[0] in CITY_COUNTRY:
                    return True
            return False

        def _is_generic(seg: str) -> bool:
            return seg.lower() in {"portfolio", "home", "website", "welcome", "official", "site", "page", "index"}

        def _is_domain(seg: str) -> bool:
            return bool(_DOMAIN_PATTERN.search(seg))

        if _is_known_loc(right_seg):
            location_hint = right_seg
            text = left_seg
        elif _is_known_loc(left_seg):
            location_hint = left_seg
            text = right_seg
        elif _is_generic(left_seg):
            text = right_seg
            short_about = None
        elif _is_generic(right_seg):
            text = left_seg
            short_about = None
        elif _is_domain(left_seg) and not _is_domain(right_seg):
            text = left_seg
            short_about = right_seg
        elif _is_domain(right_seg) and not _is_domain(left_seg):
            text = right_seg
            short_about = left_seg
        else:
            if len(right_seg) <= len(left_seg):
                shorter, longer = right_seg, left_seg
            else:
                shorter, longer = left_seg, right_seg

            if _looks_like_proper_noun(shorter):
                text = shorter
                short_about = longer.strip()
            else:
                text = left_seg
                short_about = right_seg.strip()

    return (text.strip(), location_hint, short_about)

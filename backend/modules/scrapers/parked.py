"""
scrapers/parked.py
─────────────────────────────────────────────────────────────────────────────
Checks if a domain is parked, spam, or blank based on its body text and DOM.
"""

import logging
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

PARKED_PHRASES = [
    "domain has been purchased and parked",
    "parked at loopia",
    "this domain is parked",
    "registered at namecheap",
    "this site is parked",
    "godaddy parking",
    "this domain is for sale",
    "domain is registered and parked",
    "domain parking",
    "buy this domain",
    "acquista questo dominio",
    "questo dominio potrebbe essere in vendita",
    "seo.domains",
    "loopiawhois",
    "get this domain name before someone else does",
    "live casino action",
    "real money games",
    "premium slots",
    "fast payouts",
    "online casino",
    "sports betting",
    "slot deposit",
    "situs judi",
    "judi online",
]


def check_parked_domain(full_text: str, url: str) -> bool:
    """Returns True if the full_text contains parked/spam domain phrases."""
    if not full_text:
        return False
    text_lower = full_text.lower()
    for phrase in PARKED_PHRASES:
        if phrase in text_lower:
            log.warning(f"Scraper identified parked/spam domain for {url}: found '{phrase}'")
            return True
    return False


def is_blank_page(soup: BeautifulSoup | None) -> bool:
    """Returns True if the page has fewer than 50 chars of body text and at most 1 image."""
    if not soup:
        return True
    body_text = soup.body.get_text(strip=True) if soup.body else soup.get_text(strip=True)
    imgs = soup.find_all("img")
    return len(body_text) < 50 and len(imgs) <= 1

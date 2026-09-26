import re
from urllib.parse import urlparse

# SPA fingerprints — if the plain HTML contains any of these the page needs JS
SPA_MARKERS = (
    '<app-root',       # Angular root element
    'ng-version',      # Angular version attribute
    '_nghost',         # Angular host binding
    'data-reactroot',  # React SSR marker (sometimes used in client-only apps too, but usually it's fine)
)

def is_spa_page(html: str) -> bool:
    """Returns True if the HTML looks like an unrendered SPA shell."""
    return any(marker in html for marker in SPA_MARKERS)

def clean_page_title(title: str) -> str:
    """Cleans the page title by removing site names/brand suffixes."""
    if not title:
        return "Untitled"
    
    # Common separators: |, -, –, —
    # Note: We use literal strings in the split, or regex with escapes
    separators = [r' \| ', r' \- ', r' – ', r' — ']
    pattern = '|'.join(separators)
    
    parts = re.split(pattern, title)
    if parts:
        # Take the first part which is usually the product name
        cleaned = parts[0].strip()
        return cleaned if cleaned else "Untitled"
    return title.strip()

def extract_sku_from_url(url: str) -> list[str]:
    """Attempts to extract SKU parts (e.g., base SKU + color code) from the URL."""
    from urllib.parse import parse_qs
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    sku_parts = []
    
    if path:
        segments = path.split('/')
        if segments:
            sku = segments[-1]
            # Strip common file extensions
            sku = re.sub(r'\.(html?|php)$', '', sku)
            
            if len(sku) > 4 and sku not in ['product', 'p', 'search', 'category']:
                # Detect and ignore SEO slugs (e.g., relaxed-fit-navy-wool-trousers)
                if sku.islower() and sku.count('-') >= 1 and not any(char.isdigit() for char in sku):
                    pass
                else:
                    # Extract the alphanumeric base prefix
                    base_match = re.match(r'^([a-zA-Z0-9]+)', sku)
                    if base_match:
                        base_sku = base_match.group(1)
                        if len(base_sku) >= 4:
                            sku_parts.append(base_sku)
                        else:
                            sku_parts.append(sku)
                    else:
                        sku_parts.append(sku)
                    
    # Also extract color/variant from query params
    query_params = parse_qs(parsed.query)
    for key in ['color', 'variant', 'sku']:
        if key in query_params and query_params[key]:
            val = query_params[key][0]
            if val not in sku_parts:
                sku_parts.append(val)
                
    return sku_parts

def extract_sku_from_html(html: str) -> list[str]:
    """Attempts to extract the main product SKU directly from the HTML source (e.g., from ld+json or meta tags)."""
    import json
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    sku_parts = []
    found_sku = None

    # 1. Look in meta tags
    meta_tags = [
        soup.find('meta', property='product:retailer_item_id'),
        soup.find('meta', property='og:upc'),
        soup.find('meta', itemprop='sku'),
        soup.find('meta', attrs={'name': 'sku'})
    ]
    for tag in meta_tags:
        if tag and tag.get('content'):
            found_sku = tag['content'].strip()
            break

    # 2. Look in application/ld+json
    if not found_sku:
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                # Handle cases where ld+json is a list
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get('@type') in ('Product', 'ProductGroup') and item.get('sku'):
                            found_sku = str(item['sku']).strip()
                            break
                elif isinstance(data, dict):
                    if data.get('@type') in ('Product', 'ProductGroup') and data.get('sku'):
                        found_sku = str(data['sku']).strip()
            except Exception:
                continue
            if found_sku:
                break

    if found_sku:
        # Split the SKU into its core components (style, color) to ensure we match
        # all variants of the product without being overly strict on size codes.
        found_sku = re.sub(r'\.(html?|php)$', '', found_sku)
        for part in found_sku.replace('_', '-').split('-')[:2]:
            if len(part) >= 2:
                sku_parts.append(part.upper())

    return sku_parts

def clean_media_url(url):
    """Cleans a URL to attempt to get the highest resolution/original version."""
    if not url: return url
    url = re.sub(r'/rx/[^/]+/', '/', url)
    url = re.sub(r'[?&](w|width|h|height|resize)=\d+', '', url)
    return url.rstrip('?')

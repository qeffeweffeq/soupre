from bs4 import BeautifulSoup
import re
from modules.config import BREADCRUMB_SELECTORS, CONTENT_SELECTORS, logger
from modules.utils.text_utils import clean_text, get_element_attr_as_string


def parse_html(html_content):
    """Parse HTML content with BeautifulSoup"""
    soup = BeautifulSoup(html_content, 'html.parser')

    # Remove script and style elements
    for script in soup(["script", "style", "noscript", "iframe"]):
        script.decompose()

    return soup


def get_breadcrumb_title(soup):
    """Extract breadcrumb title from the page if available"""
    for selector in BREADCRUMB_SELECTORS:
        try:
            breadcrumb = soup.select_one(selector)
            if breadcrumb:
                # Get the last item in the breadcrumb
                items = breadcrumb.find_all(['li', 'span', 'a'])
                if items:
                    return clean_text(items[-1].get_text())
        except Exception:
            continue

    # If no breadcrumb, try to use the page title
    if soup.title:
        return clean_text(soup.title.string)

    return None


def get_page_title(soup):
    """Get the page title"""
    return soup.title.string if soup.title else "Untitled Page"


def find_main_content(soup):
    """Find the main content area of the page with improved detection"""
    # First try standard content selectors
    for selector in CONTENT_SELECTORS:
        try:
            if selector.startswith('.') or selector.startswith('#'):
                main_content = soup.select_one(selector)
            else:
                main_content = soup.find(selector)

            if main_content and len(main_content.get_text(strip=True)) > 100:
                return main_content
        except Exception:
            continue

    # If standard selectors fail, try to find the largest text container
    candidates = []

    # Check common container elements
    for tag in ['div', 'section', 'article', 'main']:
        for element in soup.find_all(tag):
            # Skip very small elements and elements with no text
            text = element.get_text(strip=True)
            if len(text) < 100:
                continue

            # Skip elements that are likely navigation, footer, etc.
            class_name = get_element_attr_as_string(element, 'class')
            id_name = get_element_attr_as_string(element, 'id')

            # Skip elements that are likely not main content
            skip_keywords = ['menu', 'nav', 'header', 'footer', 'sidebar', 'widget', 'comment', 'cookie',
                             'popup', 'modal', 'banner', 'ad-', 'advertisement']

            if any(keyword in class_name.lower() or keyword in id_name.lower() for keyword in skip_keywords):
                continue

            # Add to candidates with text length as score
            candidates.append((element, len(text)))

    # Sort by text length (largest first)
    candidates.sort(key=lambda x: x[1], reverse=True)

    # Return the element with the most text, if any
    if candidates:
        return candidates[0][0]

    # If no suitable content found, use body
    return soup.body


# def extract_meta_content(soup):
#     """Extract meta tags content from the page"""
#     meta_data = []
#
#     # Extract standard meta tags
#     for meta in soup.find_all('meta'):
#         name = meta.get('name', meta.get('property', ''))
#         content = meta.get('content', '')
#         if name and content:
#             meta_data.append((name, content))
#
#     # Extract Open Graph meta tags (using attrs to avoid keyword conflicts)
#     for meta in soup.find_all('meta', attrs={'property': re.compile(r'^og:')}):
#         name = meta.get('property', '')
#         content = meta.get('content', '')
#         if name and content:
#             meta_data.append((name, content))
#
#     # Extract Twitter Card meta tags (using attrs to avoid keyword conflicts)
#     for meta in soup.find_all('meta', attrs={'name': re.compile(r'^twitter:')}):
#         name = meta.get('name', '')
#         content = meta.get('content', '')
#         if name and content:
#             meta_data.append((name, content))
#
#     # Extract canonical URL
#     canonical = soup.find('link', rel='canonical')
#     if canonical and canonical.get('href'):
#         meta_data.append(('canonical', canonical.get('href')))
#
#     # Extract hreflang tags
#     for hreflang in soup.find_all('link', rel='alternate', hreflang=True):
#         lang = hreflang.get('hreflang', '')
#         href = hreflang.get('href', '')
#         if lang and href:
#             meta_data.append((f'hreflang:{lang}', href))
#
#     return meta_data


def extract_meta_content(soup):
    """Extract meta tags content from the page"""
    meta_data = []

    # Extract description meta tag
    description = soup.find('meta', attrs={'name': 'description'})
    if description and description.get('content'):
        meta_data.append(('description', description.get('content')))

    # Try Open Graph description if standard description is not available
    if not description:
        og_description = soup.find('meta', attrs={'property': 'og:description'})
        if og_description and og_description.get('content'):
            meta_data.append(('og:description', og_description.get('content')))

    return meta_data


def should_skip_element(element, skip_patterns=None):
    if skip_patterns is None:
        skip_patterns = ['nav', 'menu', 'footer', 'header', 'sidebar', 'widget', 'cookie',
                         'popup', 'modal', 'banner', 'ad-', 'advertisement', 'swiper-slide-duplicate', 'clone']

    try:
        # Check the element's own class and ID
        element_classes = element.get('class', [])
        if isinstance(element_classes, list):
            element_classes = ' '.join(element_classes)
        elif not isinstance(element_classes, str):
            element_classes = str(element_classes)

        element_id = element.get('id', '')
        if not isinstance(element_id, str):
            element_id = str(element_id)

        if any(pattern in element_classes.lower() or pattern in element_id.lower() for pattern in skip_patterns):
            return True

        # Check parent elements' classes and IDs
        parent_classes = []
        parent_ids = []

        for parent in element.parents:
            parent_class = parent.get('class', [])
            if isinstance(parent_class, list):
                parent_class = ' '.join(parent_class)
            elif not isinstance(parent_class, str):
                parent_class = str(parent_class)

            parent_id = parent.get('id', '')
            if not isinstance(parent_id, str):
                parent_id = str(parent_id)

            if parent_class:
                parent_classes.append(parent_class)
            if parent_id:
                parent_ids.append(parent_id)

        parent_classes_str = ' '.join(parent_classes)
        parent_ids_str = ' '.join(parent_ids)

        if any(pattern in parent_classes_str.lower() or pattern in parent_ids_str.lower() for pattern in skip_patterns):
            return True

        return False
    except Exception as e:
        # If there's any error, log it and return False to be safe
        logger.warning(f"Error in should_skip_element: {e}")
        return False


def should_exclude_image(src, alt=''):
    return False
    """Determine if an image should be excluded from the markdown output"""
    # Convert to lowercase for case-insensitive matching
    src_lower = src.lower()
    alt_lower = alt.lower() if alt else ''

    # Exclude social media icons
    social_patterns = [
        'social/', 'icon-', 'icon_', 'icons/',
        'facebook', 'twitter', 'instagram', 'linkedin', 'youtube',
        'pinterest', 'whatsapp', 'telegram', 'tiktok', 'snapchat'
    ]

    # Exclude logos
    logo_patterns = ['logo', 'brand', 'poweredby', 'powered-by', 'powered_by']

    # Exclude flags and country icons
    flag_patterns = ['/nations/', '/flags/', 'flag-', 'flag_', 'country-', 'country_',
                     'ita.', 'eng.', 'fra.', 'deu.', 'esp.', 'rus.', 'usa.']

    # Exclude cookie-related images
    cookie_patterns = [
        'cookie', 'gdpr', 'iab', 'tcf', 'cmp', 'consent',
        'cookieman', 'privacy'
    ]

    # Exclude by source patterns
    if any(pattern in src_lower for pattern in social_patterns + logo_patterns + flag_patterns + cookie_patterns):
        return True

    # Exclude by alt text
    if alt and any(
            pattern in alt_lower for pattern in social_patterns + logo_patterns + flag_patterns + cookie_patterns):
        return True

    # Exclude specific domains
    exclude_domains = ['cookieman.it', 'iubenda.com', 'privacy', 'cookie']
    if any(domain in src_lower for domain in exclude_domains):
        return True

    # Exclude images with numeric filenames (often pagination or gallery indicators)
    if re.search(r'/\d+\.(jpg|jpeg|png|gif|webp)$', src_lower):
        return True

    # Exclude tiny images (likely icons)
    if re.search(r'_small\.', src_lower) or re.search(r'-small\.', src_lower):
        return True

    return False

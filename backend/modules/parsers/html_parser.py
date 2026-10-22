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
            skip_keywords = ['menu', 'nav', 'footer', 'sidebar', 'widget', 'comment', 'cookie',
                             'popup', 'modal', 'ad-', 'advertisement']

            if any(keyword in class_name.lower() or keyword in id_name.lower() for keyword in skip_keywords):
                continue

            if ('site-header' in class_name.lower() or 'main-header' in class_name.lower() or class_name.lower() == 'header' or id_name.lower() == 'header'):
                if not element.find(['h1', 'h2', 'h3']) and len(text) < 300:
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
        skip_patterns = ['nav', 'menu', 'footer', 'sidebar', 'cookie',
                         'popup', 'modal', 'ad-', 'advertisement', 'swiper-slide-duplicate', 'clone', 'cloned']

    try:
        tag_name = getattr(element, 'name', '') or ''
        # Never skip img elements based on modal/slider wrappers
        if tag_name == 'img':
            return False

        # Check the element's own class and ID
        element_classes = element.get('class', [])
        if isinstance(element_classes, list):
            element_classes = ' '.join(element_classes)
        elif not isinstance(element_classes, str):
            element_classes = str(element_classes)

        element_id = element.get('id', '')
        if not isinstance(element_id, str):
            element_id = str(element_id)

        classes_lower = element_classes.lower()
        id_lower = element_id.lower()

        # Do not skip modal-opener, product media, or lightbox wrappers
        if 'modal-opener' in tag_name or 'modal-opener' in classes_lower or 'product__media' in classes_lower or 'lightbox' in classes_lower:
            return False

        # Protect explicit content elements: headings, headlines, subheaders, captions, card titles
        is_content = (
            tag_name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'] or
            any(k in classes_lower or k in id_lower for k in [
                'subheader', 'sub-header', 'headline', 'title', 'overlaytext',
                'caption', 'boxcontent', 'roomname', 'boxcontentheader'
            ])
        )

        if is_content:
            # Only skip content elements if strictly nested in an actual nav, footer, or cookie consent
            for parent in element.parents:
                parent_tag = getattr(parent, 'name', '') or ''
                if parent_tag in ['nav', 'footer']:
                    return True
                p_cls = parent.get('class', [])
                p_cls_str = (' '.join(p_cls) if isinstance(p_cls, list) else str(p_cls)).lower()
                p_id_str = str(parent.get('id', '')).lower()
                p_combined = p_cls_str + ' ' + p_id_str
                if any(p in p_combined for p in ['cookie', 'consent', 'gdpr', 'popup', 'modal']):
                    return True
            return False

        # For header: only skip if it's explicitly a site navigation header
        if ('site-header' in classes_lower or 'main-header' in classes_lower or classes_lower == 'header' or id_lower == 'header'):
            if not element.find(['h1', 'h2', 'h3']) and len(element.get_text(strip=True)) < 200:
                return True

        if tag_name in ['nav', 'menu', 'footer'] or any(pattern in classes_lower or pattern in id_lower for pattern in skip_patterns):
            return True

        # Check parent elements' classes and IDs
        parent_classes = []
        parent_ids = []

        for parent in element.parents:
            parent_tag = getattr(parent, 'name', '') or ''
            parent_class = parent.get('class', [])
            if isinstance(parent_class, list):
                parent_class = ' '.join(parent_class)
            elif not isinstance(parent_class, str):
                parent_class = str(parent_class)

            parent_id = parent.get('id', '')
            if not isinstance(parent_id, str):
                parent_id = str(parent_id)

            p_class_lower = parent_class.lower()
            p_id_lower = parent_id.lower()

            # Ignore 'modal' match if the parent is a modal-opener, product-media, or lightbox wrapper
            if 'modal-opener' in parent_tag or 'modal-opener' in p_class_lower or 'product__media' in p_class_lower or 'lightbox' in p_class_lower:
                continue

            if parent_class:
                parent_classes.append(parent_class)
            if parent_id:
                parent_ids.append(parent_id)

        parent_classes_str = ' '.join(parent_classes)
        parent_ids_str = ' '.join(parent_ids)
        parent_combined = (parent_classes_str + ' ' + parent_ids_str).lower()

        # Check skip patterns on parents, filtering out generic 'header' and 'banner' substrings
        effective_patterns = [p for p in skip_patterns if p not in ['header', 'banner']]
        if any(pattern in parent_combined for pattern in effective_patterns):
            return True

        if 'header' in skip_patterns and ('site-header' in parent_combined or 'main-header' in parent_combined):
            for parent in element.parents:
                p_cls = (' '.join(parent.get('class', [])) if isinstance(parent.get('class', []), list) else str(parent.get('class', ''))).lower()
                p_id = str(parent.get('id', '')).lower()
                if 'site-header' in p_cls or 'site-header' in p_id or 'main-header' in p_cls or 'main-header' in p_id:
                    if not parent.find(['h1', 'h2', 'h3']) and len(parent.get_text(strip=True)) < 200:
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

import logging
import os
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from modules.utils.network import get_domain_from_url
from modules.utils.fs import sanitize_filename
from .helpers import clean_media_url, clean_page_title, extract_sku_from_url, extract_sku_from_html
from .html import extract_from_text, extract_urls_from_srcset, extract_url_from_style_background

def _extract_from_element(element, base_url, config, page_title, full_html_for_greedy=None, exclusion_keywords=None):
    """Internal helper to extract media from a specific BeautifulSoup element."""
    extracted_urls = []
    if exclusion_keywords is None:
        exclusion_keywords = []

    def add_media_url(url, media_type, source_tag="unknown", rating=0):
        if url:
            cleaned_url = clean_media_url(url)
            full_url = urljoin(base_url, cleaned_url)
            parsed_url = urlparse(full_url)
            path_without_params = parsed_url.path
            
            extensions = getattr(config, f"{media_type.upper()}_EXTENSIONS", [])
            # Filename/URL Keyword exclusion
            url_lower = full_url.lower()
            if any(kw in url_lower for kw in exclusion_keywords):
                 logging.debug(f"Skipping excluded keyword URL: {full_url}")
                 return

            if any(path_without_params.lower().endswith(ext) for ext in extensions):
                if full_url not in [m['url'] for m in extracted_urls]:
                    extracted_urls.append({
                        'type': media_type, 
                        'url': full_url, 
                        'rating': rating,
                        'source': source_tag,
                        'title': page_title,
                        'source_domain': get_domain_from_url(base_url)
                    })
                    logging.debug(f"Extracted {media_type} URL from {source_tag}: {full_url}")

    # 1. Extract from <meta property="og:image"> (only if element is the whole soup)
    if hasattr(element, 'find'):
        og_image = element.find('meta', property="og:image")
        if og_image and og_image.get('content'):
            add_media_url(og_image['content'], 'img', "meta og:image")

    # 2. Extract from <img> tags
    for img in element.find_all('img'):
        add_media_url(img.get('src'), 'img', f"img src ({img.get('alt', '')})")
        add_media_url(img.get('data-src'), 'img', f"img data-src ({img.get('alt', '')})")

    # 3. Extract from <source srcset="...">
    for source in element.find_all('source', srcset=True):
        for url, rating in extract_urls_from_srcset(source['srcset']):
            add_media_url(url, 'img', "source srcset", rating)
    for source in element.find_all('source', {'data-srcset': True}):
        for url, rating in extract_urls_from_srcset(source['data-srcset']):
            add_media_url(url, 'img', "source data-srcset", rating)

    # 4. Background images
    for tag in element.find_all(style=True):
        style = tag.get('style')
        if style and 'background-image' in style:
            url = extract_url_from_style_background(style)
            add_media_url(url, 'img', f"style background-image ({tag.name})")

    # 5. data-media-url
    for tag in element.find_all(attrs={'data-media-url': True}):
        for url in tag['data-media-url'].split(','):
            clean_u = url.split('?')[0].strip().replace('&amp;', '&')
            add_media_url(clean_u, 'img', f"data-media-url ({tag.name})")

    # 6. <video>
    for video in element.find_all('video'):
        add_media_url(video.get('src'), 'video', "video src")
        add_media_url(video.get('data-src'), 'video', "video data-src")
        for s in video.find_all('source', src=True):
            add_media_url(s['src'], 'video', "video source src")

    # 7. <audio>
    for audio in element.find_all('audio'):
        add_media_url(audio.get('src'), 'audio', "audio src")
        add_media_url(audio.get('data-src'), 'audio', "audio data-src")
        for s in audio.find_all('source', src=True):
            add_media_url(s['src'], 'audio', "audio source src")

    # 8. Greedy from <script>
    for script in element.find_all('script'):
        content = script.get_text()
        if content:
            for m_type in config.MEDIA_TYPES:
                for url in extract_from_text(content, m_type, config):
                    add_media_url(url, m_type, f"script greedy ({m_type})")

    # 9. Greedy from full HTML if provided
    if full_html_for_greedy:
        for m_type in config.MEDIA_TYPES:
            for url in extract_from_text(full_html_for_greedy, m_type, config):
                add_media_url(url, m_type, f"whole-page greedy ({m_type})")

    # 10. helper data attributes
    for tag in element.find_all(attrs={'data-video-url': True}):
        add_media_url(tag['data-video-url'], 'video', "data-video-url")
    for tag in element.find_all(attrs={'data-full-src': True}):
        add_media_url(tag['data-full-src'], 'img', "data-full-src")

    return extracted_urls

def extract_media_urls(html_content, base_url, config):
    """Extracts media URLs from HTML content with structural and priority filtering."""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 0. Clean Title
    raw_title = soup.title.string if soup.title and soup.title.string else "Untitled"
    cleaned_title = clean_page_title(raw_title)
    page_title = sanitize_filename(cleaned_title)

    # 0.1 Define Structural Filters
    priority_selectors = ['main', 'article', '.gallery', '.product-gallery', '.hero', '.main-content', '#main-content']
    exclusion_selectors = ['nav', 'footer', '.sidebar', '.social-links', '.navigation', '.menu', '.related-products', '.upsell', '.recommendations']
    
    # 0.2 Define Keyword-based Exclusions (for URLs/Filenames)
    exclusion_keywords = ['social', 'whatsapp', 'wechat', 'facebook', 'twitter', 'instagram', 'icon', 'logo', 'shuffle', 'snake', 'menu', 'arrow', 'chevron']

    # 0.3 SKU Detection for Strict Filtering
    with open('ps_debug.html', 'w') as f:
        f.write(html_content)
    
    sku_parts = extract_sku_from_url(base_url)
    if not sku_parts:
        # Fallback to looking in the HTML (e.g., meta tags or ld+json) for SEO URLs
        sku_parts = extract_sku_from_html(html_content)
        
    if sku_parts:
        logging.info(f"SKU STRICT MODE: Detected SKU parts {sku_parts}. Filtering all results.")

    all_media = []

    # 1. Targeted Extraction (Priority Containers)
    found_in_priority = False
    for selector in priority_selectors:
        containers = soup.select(selector)
        for container in containers:
            # Check if this container is NOT inside an excluded selector
            is_excluded = any(container.find_parents(sel) for sel in exclusion_selectors)
            if not is_excluded:
                extracted = _extract_from_element(container, base_url, config, page_title, exclusion_keywords=exclusion_keywords)
                if extracted:
                    all_media.extend(extracted)
                    found_in_priority = True
    
    if found_in_priority:
        logging.info(f"Targeted extraction successful via priority selectors. Found {len(all_media)} items.")
        # We still might want to check the head (meta tags)
        head_media = _extract_from_element(soup.head, base_url, config, page_title, exclusion_keywords=exclusion_keywords) if soup.head else []
        all_media.extend([m for m in head_media if m['url'] not in [x['url'] for x in all_media]])
    else:
        # 2. Fallback: Extraction from the whole body EXCLUDING UI components
        if soup.body:
            # We could recursively clean the body of excluded elements for simpler extraction
            # but let's just use the greedy approach on the whole thing and filter later if needed,
            # or better: filter the soup itself.
            body_copy = BeautifulSoup(str(soup.body), 'html.parser')
            for sel in exclusion_selectors:
                for excluded in body_copy.select(sel):
                    excluded.decompose()
            
            all_media = _extract_from_element(body_copy, base_url, config, page_title, full_html_for_greedy=html_content, exclusion_keywords=exclusion_keywords)
        else:
            # Last resort: just the whole soup
            all_media = _extract_from_element(soup, base_url, config, page_title, full_html_for_greedy=html_content, exclusion_keywords=exclusion_keywords)

    # 3. Final Filtering (Deduplication + Strict SKU Match)
    unique_media = []
    seen = set()
    skipped_count = 0

    for m in all_media:
        m_url = m['url'].lower()
        # If SKU mode is on, item MUST contain all SKU parts.
        if sku_parts:
             filename = os.path.basename(urlparse(m['url']).path).lower()
             missing_part = False
             for part in sku_parts:
                 part_lower = part.lower()
                 if part_lower not in m_url and part_lower not in filename:
                     missing_part = True
                     break
                     
             if missing_part:
                  logging.debug(f"STRICT MODE: Skipping media (no SKU match): {m['url']}")
                  skipped_count += 1
                  continue
        
        if m['url'] not in seen:
            unique_media.append(m)
            seen.add(m['url'])

    if sku_parts and skipped_count > 0:
        logging.info(f"SKU STRICT MODE: Successfully filtered out {skipped_count} non-matching items.")
        logging.info(f"Final items matching SKU to download: {len(unique_media)}")

    return unique_media

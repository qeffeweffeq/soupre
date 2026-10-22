from urllib.parse import urljoin
import re
from modules.config import logger, UNWANTED_CONTENT_PATTERNS, META_TABLE_LABELS, RAW_URL_PATTERN
from modules.parsers.html_parser import should_skip_element, should_exclude_image
from modules.utils.text_utils import clean_text, get_element_attr_as_string
from modules.utils.file_utils import download_image




def get_best_image(element):
    best_src = None
    candidates = []
    
    # helper to parse srcset
    def parse_srcset(srcset_str):
        if not srcset_str: return
        for part in srcset_str.split(','):
            part = part.strip()
            if not part: continue
            tokens = part.split(' ')
            url = tokens[0]
            width = 0
            if len(tokens) > 1:
                w_str = tokens[1].strip().lower()
                if w_str.endswith('w'):
                    w_val = w_str[:-1]
                    if w_val.isdigit():
                        width = int(w_val)
                elif w_str.endswith('x'):
                    w_val = w_str[:-1]
                    try:
                        width = int(float(w_val) * 1000)
                    except:
                        pass
            candidates.append((width, url))
            
    # Check the element itself
    parse_srcset(element.get('data-srcset') or element.get('srcset'))

    # Check high-res attributes on element itself (e.g. USM data-lightbox-src, zoom images, computed bg)
    for big_attr in [
        'data-lightbox-src', 'data-lightbox', 'data-zoom-image', 'data-zoom-src',
        'data-full-src', 'data-full-image', 'data-high-res-src', 'data-highres',
        'data-large_image', 'data-large', 'data-big-photo', 'data-imagehd',
        'data-bg-src', 'data-thumbnail', 'data-background', 'data-bg'
    ]:
        val = element.get(big_attr) if hasattr(element, 'get') else None
        if val and not val.startswith('data:'):
            candidates.append((3000, val))

    # If the src contains downscaled thumbnail dimensions (e.g. USM 550x550), provide the high-res 1140x1140 counterpart
    src_val = element.get('src') if hasattr(element, 'get') else None
    if src_val and not src_val.startswith('data:') and '.550x550_' in src_val:
        candidates.append((2500, src_val.replace('.550x550_', '.1140x1140_')))
    
    # If it's an img, check its parent picture sources and parent div data-media-url
    if element.name == 'img':
        parent = element.parent
        while parent and parent.name in ['picture', 'div', 'a', 'figure']:
            if parent.name == 'picture':
                for source in parent.find_all('source'):
                    parse_srcset(source.get('data-srcset') or source.get('srcset'))
            
            # check data-media-url on parent
            media_url = parent.get('data-media-url')
            if media_url and ',' in media_url:
                urls = media_url.split(',')
                for u in urls:
                    u = u.strip()
                    if u and not u.startswith('data:'):
                        # crude width estimation from imgix URL for sorting
                        width = 1000
                        import re
                        w_match = re.search(r'[?&]w=(\d+)', u)
                        if w_match:
                            width = int(w_match.group(1))
                        candidates.append((width, u))

            # check high-res attributes on parent (e.g. Tumblr data-big-photo, USM data-lightbox-src)
            for big_attr in [
                'data-lightbox-src', 'data-lightbox', 'data-zoom-image', 'data-zoom-src',
                'data-full-src', 'data-full-image', 'data-high-res-src', 'data-highres',
                'data-large_image', 'data-large', 'data-big-photo', 'data-imagehd'
            ]:
                big_url = parent.get(big_attr)
                if big_url and not big_url.startswith('data:'):
                    candidates.append((3000, big_url))
                        
            parent = parent.parent
            
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_src = candidates[0][1]
        if best_src and not best_src.startswith('data:'):
            return best_src
        
    for attr in [
        'data-lightbox-src', 'data-lightbox', 'data-zoom-image', 'data-zoom-src',
        'data-full-src', 'data-full-image', 'data-high-res-src', 'data-highres',
        'data-media-url', 'data-large_image', 'data-large', 'data-full-url',
        'data-bg-src', 'data-thumbnail', 'data-background', 'data-bg',
        'data-src', 'data-lazy-src', 'src'
    ]:
        val = element.get(attr)
        if val and not val.startswith('data:'):
            if attr == 'data-media-url' and ',' in val:
                urls = val.split(',')
                for u in urls:
                    u = u.strip()
                    if u and not u.startswith('data:'):
                        return u
            return val

    if hasattr(element, 'get') and element.get('data-settings'):
        import json
        try:
            settings = json.loads(element.get('data-settings'))
            if isinstance(settings, dict):
                bg_img = settings.get('background_image') or settings.get('background_video_fallback')
                if isinstance(bg_img, dict) and bg_img.get('url') and not str(bg_img['url']).startswith('data:'):
                    return str(bg_img['url'])
        except Exception:
            pass
            
    style = element.get('style', '')
    if 'background-image' in style or 'background' in style:
        match = re.search(r'url\([\'"]?(.*?)[\'"]?\)', style)
        if match:
            bg_src = match.group(1)
            if not bg_src.startswith('data:'):
                return bg_src
                
    return None

def html_to_markdown(element, base_url, driver=None, page_dir=None, processed_elements=None):
    """Convert HTML elements to Markdown with improved formatting"""
    try:
        if processed_elements is None:
            processed_elements = set()

        # Skip already processed elements to avoid repetition
        if element in processed_elements:
            return ""

        processed_elements.add(element)
        
        video_md = ""
        if hasattr(element, 'get'):
            vid_attr = element.get('data-media-video') or element.get('data-media-autoplayvideo')
            if vid_attr:
                import html
                decoded = html.unescape(vid_attr)
                src_match = re.search(r'src=["\']([^"\']+)["\']', decoded)
                if src_match:
                    vid_src = src_match.group(1)
                    vid_src = vid_src.split('?')[0]
                    video_md = f"\n\n[Video]({vid_src})\n\n"

        if getattr(element, 'name', None) and should_skip_element(element):
            return ""

        # Skip empty elements
        if element.name is None:
            text = element.string if element.string else ''
            return clean_text(text) if text.strip() else ''

        # Skip form elements and inputs
        if element.name in ['form', 'input', 'select', 'option', 'button', 'script', 'style', 'noscript', 'svg', 'iframe', 'dialog', 'template']:
            return ""

        # Skip elements with certain classes/IDs that indicate non-content
        element_classes = get_element_attr_as_string(element, 'class')
        element_id = get_element_attr_as_string(element, 'id')
        classes_lower = element_classes.lower()
        id_lower = element_id.lower()
        tag_name = (getattr(element, 'name', '') or '').lower()
        elementor_type = (element.get('data-elementor-type') or '').lower() if hasattr(element, 'get') else ''

        # Exception: do not skip root containers, Elementor page roots, gallery media, modal openers, or lightboxes
        is_media_or_root_container = (
            tag_name in ('body', 'html', 'main', 'article', 'img') or
            elementor_type in ('wp-page', 'wp-post', 'single', 'single-page', 'single-post', 'archive') or
            'modal-opener' in tag_name or
            'modal-opener' in classes_lower or
            'product__media' in classes_lower or
            'lightbox' in classes_lower
        )

        skip_patterns = ['cookie', 'consent', 'gdpr', 'moove', 'popup', 'modal', 'banner', 'sidebar',
                         'footer', 'header', 'menu', 'nav', 'search', 'cart', 'login']

        if not is_media_or_root_container and any(pattern in classes_lower or pattern in id_lower for pattern in skip_patterns):
            return ""

        # Process headings with proper spacing
        if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            heading_level = int(element.name[1])
            heading_text = re.sub(r'\s+', ' ', clean_text(element.get_text(separator=" "))).strip()
            if heading_text:
                return f"\n\n{'#' * heading_level} {heading_text}\n\n"
            return ""

        # Process paragraphs with proper spacing
        elif element.name == 'p':
            text = clean_text(element.get_text(separator=" "))
            if text:
                # Check if paragraph contains only an image or link
                if element.find('img') and len(element.find_all()) == 1:
                    img = element.find('img')
                    return html_to_markdown(img, base_url, driver, page_dir, processed_elements)
                elif element.find('a') and len(element.find_all()) == 1:
                    link = element.find('a')
                    return html_to_markdown(link, base_url, driver, page_dir, processed_elements)
                return f"{text}\n\n"
            return ""

        # Process links with proper spacing
        elif element.name == 'a':
            # Extract images within the link first
            img_md = ""
            if element.find('img'):
                seen_img_urls = set()
                for img in element.find_all('img'):
                    if img.find_parent('noscript'):
                        continue
                    src = get_best_image(img)
                    if src:
                        full_src = urljoin(base_url, src)
                        if full_src in seen_img_urls:
                            continue
                        seen_img_urls.add(full_src)
                    img_md += html_to_markdown(img, base_url, driver, page_dir, processed_elements)

            href = (element.get('href') or '').strip()
            link_text = clean_text(element.get_text(separator=" "))

            if href and not href.startswith(('javascript:', '#')):
                if href.startswith(('mailto:', 'tel:')):
                    if link_text:
                        return img_md + f"[{link_text}]({href})\n\n"
                    return img_md
                href = urljoin(base_url, href)
                if link_text:
                    return img_md + f"[{link_text}]({href})\n\n"
                return img_md
            else:
                if img_md:
                    return img_md + (f"{link_text}\n\n" if link_text else "")
                return f"{clean_text(link_text)}\n\n" if link_text else ""

        # Process images and containers with background images / videos
        elif element.name == 'img' or (
            element.name in ['div', 'span', 'section', 'article', 'figure'] and (
                element.has_attr('data-bg-src') or
                element.has_attr('data-thumbnail') or
                element.has_attr('data-background') or
                element.has_attr('data-bg') or
                element.has_attr('data-media-video') or
                element.has_attr('data-media-autoplayvideo') or
                (element.has_attr('data-settings') and 'background_image' in str(element.get('data-settings', ''))) or
                (element.has_attr('style') and ('background-image' in element.get('style', '') or 'background:' in element.get('style', '')))
            )
        ):
            src = get_best_image(element)
            alt = element.get('alt', '') if element.name == 'img' else ''
            
            if element.name == 'img':
                if element.get('width') and str(element.get('width')).isdigit() and int(element.get('width')) < 50:
                    return ""
                if element.get('height') and str(element.get('height')).isdigit() and int(element.get('height')) < 50:
                    return ""
                    
            if src:
                if should_exclude_image(src, alt):
                    if element.name != 'img':
                        return video_md + process_children_recursively(element, base_url, driver, page_dir, processed_elements)
                    return ""
                
                src = urljoin(base_url, src)
                img_md = f"![{alt}]({src})\n\n"
                
                if element.name != 'img':
                    # For divs with background image, we still want to process their children text!
                    children_md = process_children_recursively(element, base_url, driver, page_dir, processed_elements)
                    return img_md + video_md + children_md
                    
                return img_md + video_md
            
            if element.name != 'img':
                return video_md + process_children_recursively(element, base_url, driver, page_dir, processed_elements)
            return video_md

        # Process lists with proper formatting
        elif element.name == 'ul':
            has_media = bool(element.find('img') or element.find(attrs={'data-media-video': True}))
            md = "\n\n"
            for li in element.find_all('li', recursive=False):
                if has_media and (li.find('img') or li.find(attrs={'data-media-video': True})):
                    li_content = process_children_recursively(li, base_url, driver, page_dir, processed_elements)
                    if li_content and li_content.strip():
                        md += f"{li_content.strip()}\n\n"
                else:
                    li_text = clean_text(li.get_text(separator=" "))
                    if li_text:
                        md += f"* {li_text}\n"
            return md + "\n" if md != "\n\n" else ""

        elif element.name == 'ol':
            has_media = bool(element.find('img') or element.find(attrs={'data-media-video': True}))
            md = "\n\n"
            for i, li in enumerate(element.find_all('li', recursive=False), 1):
                if has_media and (li.find('img') or li.find(attrs={'data-media-video': True})):
                    li_content = process_children_recursively(li, base_url, driver, page_dir, processed_elements)
                    if li_content and li_content.strip():
                        md += f"{li_content.strip()}\n\n"
                else:
                    li_text = clean_text(li.get_text(separator=" "))
                    if li_text:
                        md += f"{i}. {li_text}\n"
            return md + "\n" if md != "\n\n" else ""

        # Process blockquotes
        elif element.name == 'blockquote':
            lines = element.get_text(separator=" ").strip().split('\n')
            if lines:
                return '\n\n> ' + '\n> '.join(lines) + '\n\n'
            return ""

        # Process code blocks
        elif element.name == 'pre' or element.name == 'code':
            code_text = element.get_text(separator=" ").strip()
            if code_text:
                return f"\n\n```\n{code_text}\n```\n\n"
            return ""

        # Process tables
        elif element.name == 'table':
            # Basic table support
            return process_table(element)

        # Process divs and other container elements
        elif element.name in ['div', 'section', 'article', 'main', 'aside', 'span']:
            # Upgrade leaf Elementor/div headings to actual headings (only when no real heading or block children exist)
            element_classes = element.get('class', [])
            if isinstance(element_classes, list):
                class_str = ' '.join(element_classes).lower()
            else:
                class_str = str(element_classes).lower()

            if (
                element.name in ['div', 'p', 'span']
                and ('heading-title' in class_str or 'widget-title' in class_str or 'section-title' in class_str)
                and not element.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'div'])
                and len(clean_text(element.get_text(separator=' '))) < 150
            ):
                heading_text = re.sub(r'\s+', ' ', clean_text(element.get_text(separator=' '))).strip()
                if heading_text:
                    return f"\n\n## {heading_text}\n\n"

            return process_children_recursively(element, base_url, driver, page_dir, processed_elements)

        # Process other elements
        else:
            # Process children recursively
            return process_children_recursively(element, base_url, driver, page_dir, processed_elements)

    except Exception as e:
        logger.error(f"Error in html_to_markdown: {e}")
        return f"Error converting element to markdown: {e}\n\n"


def process_all_elements(soup, url, driver=None, page_dir=None):
    """Process all visible elements in the page with improved content selection"""
    markdown = ""
    processed_elements = set()

    try:
        # First, try to find the main content container
        main_containers = []

        # Look for main content containers (including Elementor page/post containers)
        for selector in [
            '[data-elementor-type="wp-page"]',
            '[data-elementor-type="wp-post"]',
            '[data-elementor-type="single"]',
            '[data-elementor-type="single-page"]',
            '[data-elementor-type="single-post"]',
            '[data-elementor-type="archive"]',
            'main', 'article', '.entry-content', '.post-content', '.page-content',
            '.main-content', '.site-content', '#content', '.content', '#main', '.main', 'div[role="main"]'
        ]:
            try:
                elements = soup.select(selector)
                for element in elements:
                    # Skip header/footer/popup containers
                    if should_skip_element(element):
                        continue
                    # Accept containers with meaningful text or media
                    if len(element.get_text(strip=True)) > 40 or element.find('img'):
                        main_containers.append(element)
            except Exception:
                continue

        # If we found main containers, process them
        if main_containers:
            # Sort by content length (largest first)
            main_containers.sort(key=lambda x: len(x.get_text(strip=True)), reverse=True)

            # Process the largest container
            markdown += html_to_markdown(main_containers[0], url, driver, page_dir, processed_elements)
        elif soup.body:
            # Process body directly in DOM order (header/footer/nav/cookie elements are already decomposed or skipped)
            markdown += html_to_markdown(soup.body, url, driver, page_dir, processed_elements)
        else:
            # Fallback to processing key elements
            for i in range(1, 7):
                for heading in soup.find_all(f'h{i}'):
                    if should_skip_element(heading, ['nav', 'menu', 'footer', 'header', 'sidebar']):
                        continue
                    markdown += html_to_markdown(heading, url, driver, page_dir, processed_elements)

            for p in soup.find_all('p'):
                if should_skip_element(p, ['nav', 'menu', 'footer', 'header', 'sidebar', 'cookie']):
                    continue
                if len(p.get_text(strip=True)) < 20:
                    continue
                markdown += html_to_markdown(p, url, driver, page_dir, processed_elements)

            for ul in soup.find_all('ul'):
                if should_skip_element(ul, ['nav', 'menu', 'footer', 'header', 'sidebar']):
                    continue
                markdown += html_to_markdown(ul, url, driver, page_dir, processed_elements)

            for ol in soup.find_all('ol'):
                if should_skip_element(ol, ['nav', 'menu', 'footer', 'header', 'sidebar']):
                    continue
                markdown += html_to_markdown(ol, url, driver, page_dir, processed_elements)

        # Clean up the markdown
        # Remove excessive newlines
        markdown = re.sub(r'\n{3,}', '\n\n', markdown)

    except Exception as e:
        logger.error(f"Error in process_all_elements: {e}")
        # Return a minimal markdown string with error information
        return f"Error processing page elements: {e}\n\n"

    # Ensure we're returning a string
    if not isinstance(markdown, str):
        if isinstance(markdown, list):
            markdown = '\n\n'.join([str(item) for item in markdown])
        else:
            markdown = str(markdown)

    return markdown


def meta_to_markdown_table(meta_data, title):
    """Convert meta data to markdown table with only title and description"""
    markdown = "\n## Meta Content\n\n"
    markdown += "| Name | Content |\n"
    markdown += "|------|--------|\n"

    # Add title - Fix the backslash issue
    if title:
        escaped_title = title.replace('|', '\\' + '|')
        markdown += f"| title | {escaped_title} |\n"

    # Find description
    description = ""
    for name, content in meta_data:
        if name.lower() == 'description' or name.lower() == 'og:description':
            description = content
            break

    if description:
        # Fix the backslash issue
        escaped_description = description.replace('|', '\\' + '|')
        markdown += f"| description | {escaped_description} |\n"

    return markdown


def convert_content_to_markdown(main_content, soup, url, driver, page_dir):
    """Convert page content to markdown with improved formatting and error handling"""
    # Extract Elementor CSS background-image rules from inline <style> and elementor-post-* stylesheets onto data-bg-src
    try:
        css_blobs = [st.string or st.get_text() for st in soup.find_all('style') if st.string or st.get_text()]
        if soup.select_one('[data-elementor-type="wp-page"], [data-elementor-type="wp-post"]'):
            import requests
            for link_tag in soup.find_all('link', rel='stylesheet', href=True):
                link_id = str(link_tag.get('id') or '').lower()
                href = str(link_tag.get('href') or '')
                if 'elementor-post-' in link_id or '/elementor/css/post-' in href:
                    try:
                        css_resp = requests.get(urljoin(url, href), timeout=(3, 5))
                        if css_resp.ok and css_resp.text:
                            css_blobs.append(css_resp.text)
                    except Exception:
                        pass
        for css_text in css_blobs:
            for el_id, bg_url in re.findall(
                r'\.elementor-element-([a-zA-Z0-9_-]+)[^{]*\{[^}]*background-image\s*:\s*url\(["\']?(https?://[^"\')\s]+)["\']?\)',
                css_text,
                flags=re.IGNORECASE,
            ):
                if bg_url.startswith('data:'):
                    continue
                target_el = soup.find(attrs={'data-id': el_id})
                if target_el and not target_el.has_attr('data-bg-src'):
                    target_el['data-bg-src'] = bg_url
    except Exception:
        pass

    for tag in soup.find_all(['noscript', 'script', 'style', 'template', 'dialog']):
        tag.decompose()

    # Start building markdown content
    title = soup.title.string if soup.title else "Untitled Page"
    markdown_content = ""

    # Remove cookie banners, forms, hidden SEO cloaking, and non-content chrome across soup
    for selector in [
        '[data-seo-content]',
        '[style*="clip-path:inset(50%)"]', '[style*="clip-path: inset(50%)"]',
        '[style*="clip:rect(1px"]', '[style*="clip: rect(1px"]',
        'header', 'footer', 'nav',
        '[data-elementor-type="header"]', '[data-elementor-type="footer"]', '[data-elementor-type="popup"]',
        '.elementor-location-header', '.elementor-location-footer', '.elementor-location-popup',
        '#moove_gdpr_cookie_info_bar', '#moove_gdpr_cookie_modal',
        '[id*="moove_gdpr"]', '[class*="moove-gdpr"]',
        '.cookie', '.cookies', '.cookie-banner', '.cookie-consent', '.consent',
        '.gdpr', '.privacy-alert', '[id*="cookie"]', '[class*="cookie"]',
        'form', '.form', '#form', 'input', 'select', 'button',
        '.menu', '#menu', '.nav', '#nav', '.navigation', '#navigation',
        '.footer', '#footer', '.header', '#header', '.sidebar', '#sidebar',
        '.ad', '.advertisement', '.popup', '.modal', '.overlay',
        '.elementor-hidden-desktop',
        '.d-none', '.hidden', '[aria-hidden="true"]',
        '[style*="display: none"]', '[style*="display:none"]'
    ]:
        try:
            for element in soup.select(selector):
                if element.name in ('html', 'body', 'main', 'article'):
                    continue
                if element.get('data-elementor-type') in ('wp-page', 'wp-post', 'single', 'single-page', 'single-post'):
                    continue
                element.decompose()
        except Exception:
            continue

    # Process the content
    try:
        content = process_all_elements(soup, url, driver, page_dir)

        # Ensure content is a string
        if isinstance(content, list):
            content = '\n\n'.join([str(item) for item in content])
        elif not isinstance(content, str):
            content = str(content)

        # Add the processed content
        markdown_content += content

        # Clean up the markdown - fix newlines
        markdown_content = re.sub(r'\n{3,}', '\n\n', markdown_content)  # Replace 3+ newlines with 2

        # Fix specific issues with headings and links
        markdown_content = re.sub(r'\)\s*#', ')\n\n#', markdown_content)  # Add newlines between links and headings
        markdown_content = re.sub(r'\n(#{1,6})', '\n\n\\1',
                                  markdown_content)  # Ensure headings have newlines before them

        # Apply post-processing to fix formatting issues
        markdown_content = post_process_markdown(markdown_content)

    except Exception as e:
        logger.error(f"Error processing content: {e}")
        markdown_content += f"\n\n## Error Processing Content\n\nAn error occurred while processing the page content: {e}\n\n"

        # Try to extract at least some text from the page
        try:
            body_text = soup.body.get_text(separator='\n\n', strip=True)
            markdown_content += f"\n\n## Extracted Text\n\n{body_text[:2000]}...\n\n"
        except Exception:
            pass

    # If content is truly empty (< 15 words), try a fallback text extraction
    if len(markdown_content.split()) < 15:
        try:
            significant_blocks = []
            seen_texts = set()

            for tag in soup.find_all(['p', 'article', 'section']):
                if should_skip_element(tag, ['nav', 'menu', 'footer', 'header', 'sidebar', 'cookie']):
                    continue
                text = clean_text(tag.get_text(separator=' ', strip=True))
                if len(text) > 60 and text not in seen_texts:
                    seen_texts.add(text)
                    significant_blocks.append(text)

            if significant_blocks:
                markdown_content += "\n\n" + "\n\n".join(significant_blocks) + "\n\n"
        except Exception as e:
            logger.error(f"Error extracting significant blocks: {e}")

    return markdown_content


def post_process_markdown(markdown):
    """Apply final formatting fixes and boilerplate removal to the markdown content"""
    # 1. Remove Standalone/Raw URLs
    markdown = re.sub(RAW_URL_PATTERN, '', markdown)
    
    # 2. Remove Legal/Privacy Boilerplate
    for pattern in UNWANTED_CONTENT_PATTERNS:
        # Match the pattern, potentially surrounded by some common prefixes or as standalone lines
        markdown = re.sub(rf'(?i)^.*{pattern}.*$\n?', '', markdown, flags=re.MULTILINE)

    # Deduplicate consecutive identical markdown image tags
    while re.search(r'(!\[.*?\]\([^\)]+\))\s*\1', markdown):
        markdown = re.sub(r'(!\[.*?\]\([^\)]+\))\s*\1', r'\1', markdown)

    # Fix newlines
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)  # Replace 3+ newlines with 2

    # Fix headings - ensure they have newlines before them
    markdown = re.sub(r'([^\n])\n(#{1,6} )', '\\1\n\n\\2', markdown)

    # Fix links followed by headings
    markdown = re.sub(r'\)\s*(#{1,6} )', ')\n\n\\1', markdown)

    # Fix lists - ensure they have proper spacing
    markdown = re.sub(r'([^\n])\n(\* )', '\\1\n\n\\2', markdown)
    markdown = re.sub(r'([^\n])\n(\d+\. )', '\\1\n\n\\2', markdown)

    # Fix images - ensure they have proper spacing
    # markdown = re.sub(r'([^\n])\n(!\[)', '\\1\n\n\\2', markdown)

    # Fix blockquotes - ensure they have proper spacing
    markdown = re.sub(r'([^\n])\n(> )', '\\1\n\n\\2', markdown)

    return markdown


def process_children_recursively(element, base_url, driver=None, page_dir=None, processed_elements=None):
    """Process all children of an element recursively and return the combined markdown"""
    if processed_elements is None:
        processed_elements = set()

    result = ""

    for child in element.children:
        if hasattr(child, 'name'):
            # Process child elements with names (HTML tags)
            child_content = html_to_markdown(child, base_url, driver, page_dir, processed_elements)

            # Only add newlines if needed for proper spacing
            if child_content and not child_content.startswith('\n\n') and result and not result.endswith('\n\n'):
                result += '\n\n'

            result += child_content
        elif child.string and child.string.strip():
            # Process text nodes
            text = clean_text(child.string)
            if text:
                if result and not result.endswith('\n\n'):
                    result += '\n\n'
                result += text + "\n\n"

    return result


def process_table(element):
    """Process a table element and convert it to markdown format"""
    md = "\n\n"

    try:
        rows = element.find_all('tr')
        if not rows:
            return ""

        # Check if table should be skipped based on labels
        table_text = element.get_text(separator=" ").lower()
        if any(label.lower() in table_text for label in META_TABLE_LABELS):
            logger.info(f"Skipping table labeled as metadata: {META_TABLE_LABELS}")
            return ""

        # Process header row
        headers = rows[0].find_all(['th', 'td'])
        if headers:
            # Create header row
            header_cells = []
            for h in headers:
                header_text = clean_text(h.get_text(separator=" "))
                # Escape pipe characters in table cells
                header_text = header_text.replace('|', '\\|')
                header_cells.append(header_text)

            md += "| " + " | ".join(header_cells) + " |\n"
            md += "| " + " | ".join(['---'] * len(headers)) + " |\n"

        # Process data rows
        for row in rows[1:] if headers else rows:
            cells = row.find_all('td')
            if cells:
                # Create data row
                data_cells = []
                for cell in cells:
                    cell_text = clean_text(cell.get_text(separator=" "))
                    # Escape pipe characters in table cells
                    cell_text = cell_text.replace('|', '\\|')
                    data_cells.append(cell_text)

                md += "| " + " | ".join(data_cells) + " |\n"

        md += "\n"

        # Only return the table if it has content
        if md == "\n\n\n":
            return ""

        return md

    except Exception as e:
        logger.error(f"Error processing table: {e}")
        return f"\n\nError processing table: {e}\n\n"

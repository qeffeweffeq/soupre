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
                        
            parent = parent.parent
            
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_src = candidates[0][1]
        if best_src and not best_src.startswith('data:'):
            return best_src
        
    for attr in ['data-media-url', 'data-large_image', 'data-large', 'data-full-url', 'data-src', 'data-lazy-src', 'src']:
        val = element.get(attr)
        if val and not val.startswith('data:'):
            if attr == 'data-media-url' and ',' in val:
                urls = val.split(',')
                for u in urls:
                    u = u.strip()
                    if u and not u.startswith('data:'):
                        return u
            return val
            
    style = element.get('style', '')
    if 'background-image' in style or 'background' in style:
        import re
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
                import re, html
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
        if element.name in ['form', 'input', 'select', 'option', 'button', 'script', 'style', 'noscript']:
            return ""

        # Skip elements with certain classes/IDs that indicate non-content
        element_classes = get_element_attr_as_string(element, 'class')
        element_id = get_element_attr_as_string(element, 'id')

        skip_patterns = ['cookie', 'consent', 'popup', 'modal', 'banner', 'sidebar',
                         'footer', 'header', 'menu', 'nav', 'search', 'cart', 'login']

        if any(pattern in element_classes.lower() or pattern in element_id.lower() for pattern in skip_patterns):
            return ""

        # Process headings with proper spacing
        if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            heading_level = int(element.name[1])
            heading_text = clean_text(element.get_text(separator=" "))
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
            href = element.get('href', '')
            if href:
                # Skip links to javascript, mailto, tel
                if href.startswith(('javascript:', 'mailto:', 'tel:')):
                    return clean_text(element.get_text(separator=" "))

                # Convert relative URLs to absolute
                href = urljoin(base_url, href)
                link_text = clean_text(element.get_text(separator=" "))

                # If the link has no text but has an image, process the image
                if not link_text and element.find('img'):
                    img = element.find('img')
                    return html_to_markdown(img, base_url, driver, page_dir, processed_elements)

                # Return formatted link if it has text
                if link_text:
                    return f"[{link_text}]({href})\n\n"
            return clean_text(element.get_text(separator=" "))

        # Process images with better handling
        elif element.name == 'img' or (element.name in ['div', 'span', 'section', 'article'] and (element.has_attr('data-media-video') or element.has_attr('data-media-autoplayvideo') or (element.has_attr('style') and ('background-image' in element.get('style', '') or 'background:' in element.get('style', ''))))):
            src = get_best_image(element)
            alt = element.get('alt', '') if element.name == 'img' else ''
            
            if element.name == 'img':
                if element.get('width') and str(element.get('width')).isdigit() and int(element.get('width')) < 50:
                    return ""
                if element.get('height') and str(element.get('height')).isdigit() and int(element.get('height')) < 50:
                    return ""
                    
            if src:
                if should_exclude_image(src, alt):
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
            md = "\n\n"
            for li in element.find_all('li', recursive=False):
                li_text = clean_text(li.get_text(separator=" "))
                if li_text:
                    md += f"* {li_text}\n"
            return md + "\n" if md != "\n\n" else ""

        elif element.name == 'ol':
            md = "\n\n"
            for i, li in enumerate(element.find_all('li', recursive=False), 1):
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
            # Upgrade Elementor/div headings to actual headings
            element_classes = element.get('class', [])
            if isinstance(element_classes, list):
                class_str = ' '.join(element_classes).lower()
            else:
                class_str = str(element_classes).lower()
                
            if element.name in ['div', 'p', 'span'] and ('heading' in class_str or 'title' in class_str) and len(clean_text(element.get_text(separator=' '))) < 150:
                heading_text = clean_text(element.get_text(separator=' '))
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

        # Look for main content containers
        for selector in [
            'main', 'article', '.content', '#content', '.entry-content', '.post-content',
            '.main-content', '.site-content', '#main', '.main', 'div[role="main"]'
        ]:
            try:
                elements = soup.select(selector)
                for element in elements:
                    # Skip small containers
                    if len(element.get_text(strip=True)) > 300:
                        main_containers.append(element)
            except Exception:
                continue

        # If we found main containers, process them
        if main_containers:
            # Sort by content length (largest first)
            main_containers.sort(key=lambda x: len(x.get_text(strip=True)), reverse=True)

            # Process the largest container
            markdown += html_to_markdown(main_containers[0], url, driver, page_dir, processed_elements)
        else:
            # Fallback to processing key elements

            # Process headings
            for i in range(1, 7):
                for heading in soup.find_all(f'h{i}'):
                    # Skip headings in navigation, footer, etc.
                    if should_skip_element(heading, ['nav', 'menu', 'footer', 'header', 'sidebar']):
                        continue

                    markdown += html_to_markdown(heading, url, driver, page_dir, processed_elements)

            # Process paragraphs
            for p in soup.find_all('p'):
                # Skip paragraphs in navigation, footer, etc.
                if should_skip_element(p, ['nav', 'menu', 'footer', 'header', 'sidebar', 'cookie']):
                    continue

                # Skip very short paragraphs that are likely not content
                if len(p.get_text(strip=True)) < 20:
                    continue

                markdown += html_to_markdown(p, url, driver, page_dir, processed_elements)

            # Process lists
            for ul in soup.find_all('ul'):
                # Skip lists in navigation, footer, etc.
                if should_skip_element(ul, ['nav', 'menu', 'footer', 'header', 'sidebar']):
                    continue

                markdown += html_to_markdown(ul, url, driver, page_dir, processed_elements)

            for ol in soup.find_all('ol'):
                # Skip lists in navigation, footer, etc.
                if should_skip_element(ol, ['nav', 'menu', 'footer', 'header', 'sidebar']):
                    continue

                markdown += html_to_markdown(ol, url, driver, page_dir, processed_elements)

            # Process images
            # for img in soup.find_all('img'):
            #     # Skip tiny images and icons
            #     if img.get('width') and int(img.get('width', '0').replace('px', '').strip() or 0) < 100:
            #         continue
            #     if img.get('height') and int(img.get('height', '0').replace('px', '').strip() or 0) < 100:
            #         continue
            #
            #     # Skip images that should be excluded
            #     src = img.get('src', '')
            #     alt = img.get('alt', '')
            #     if should_exclude_image(src, alt):
            #         continue
            #
            #     # Skip images in navigation, footer, etc.
            #     parent_classes = ' '.join([get_element_attr_as_string(p, 'class') for p in img.parents])
            #     parent_ids = ' '.join([get_element_attr_as_string(p, 'id') for p in img.parents])
            #
            #     if any(pattern in parent_classes.lower() or pattern in parent_ids.lower()
            #            for pattern in ['nav', 'menu', 'footer', 'header', 'sidebar', 'logo']):
            #         continue
            #
            #     if src and not src.startswith('data:'):  # Skip data URIs
            #         markdown += html_to_markdown(img, url, driver, page_dir, processed_elements)

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
    # Start building markdown content
    title = soup.title.string if soup.title else "Untitled Page"
    markdown_content = ""

    # Add screenshot reference to the markdown
    # markdown_content += f"![Page Screenshot](screenshot.png)\n\n"

    # First, try to remove cookie banners, forms, and other non-content elements
    if main_content:
        # Remove cookie banners, forms, and other non-content elements
        for selector in [
            '.cookie', '.cookies', '.cookie-banner', '.cookie-consent', '.consent',
            '.gdpr', '.privacy-alert', '[id*="cookie"]', '[class*="cookie"]',
            'form', '.form', '#form', 'input', 'select', 'button',
            '.menu', '#menu', 'nav', '.nav', '#nav', '.navigation', '#navigation',
            '.footer', '#footer', '.header', '#header', '.sidebar', '#sidebar',
            '.ad', '.advertisement', '.popup', '.modal', '.overlay',
            '.elementor-hidden-mobile', '.elementor-hidden-tablet', '.elementor-hidden-phone',
            '.d-none', '.d-sm-none', '.d-md-none', '.hidden', '[style*="display: none"]', '[style*="display:none"]'
        ]:
            for element in main_content.select(selector):
                element.decompose()

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

    # If content is still minimal, try a more aggressive approach
    if len(markdown_content.split()) < 100:
        # Extract all significant text blocks
        try:
            significant_blocks = []

            # Find all text blocks with substantial content
            for tag in soup.find_all(['p', 'div', 'section', 'article']):
                text = tag.get_text(strip=True)
                if len(text) > 100:  # Only include blocks with meaningful text
                    # Skip if it's in a navigation, footer, etc.
                    if should_skip_element(tag, ['nav', 'menu', 'footer', 'header', 'sidebar', 'cookie']):
                        continue

                    significant_blocks.append(text)

            # Add the significant blocks to the content
            if significant_blocks:
                markdown_content += "\n\n## Extracted Content\n\n"
                for block in significant_blocks:
                    markdown_content += f"{block}\n\n"
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

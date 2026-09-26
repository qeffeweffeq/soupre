import os
import re
import urllib.parse
from urllib.parse import urljoin
import requests
import random
import time
import csv
import os
from modules.config import logger
from modules.parsers.html_parser import should_exclude_image
from modules.utils.text_utils import sanitize_filename

# Fallback user agents in case import fails
FALLBACK_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]

# Get user agents from config or use fallback
try:
    from modules.config import USER_AGENTS
except ImportError:
    USER_AGENTS = FALLBACK_USER_AGENTS


def ensure_directory_exists(directory):
    """Create directory if it doesn't exist"""
    if not os.path.exists(directory):
        os.makedirs(directory)


def save_html_source(html_content, directory, filename='source.html'):
    """Save HTML source to file"""
    ensure_directory_exists(directory)
    file_path = os.path.join(directory, filename)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    return file_path


def save_markdown_content(markdown_content, directory, filename='content.md', create_text=True):
    """Save markdown content to file and also create a plain text version by default"""
    ensure_directory_exists(directory)

    # Ensure markdown_content is a string
    if not isinstance(markdown_content, str):
        if isinstance(markdown_content, list):
            markdown_content = '\n\n'.join(markdown_content)
        else:
            markdown_content = str(markdown_content)

    file_path = os.path.join(directory, filename)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)

    # Also save as plain text by default
    if create_text:
        try:
            save_plain_text(markdown_content, directory, 'content.txt')
        except Exception as e:
            logger.error(f"Error creating plain text file: {e}")

    return file_path


def download_image(src, base_url, page_dir, driver=None):
    """Download an image with improved performance and error handling"""
    if not src:
        return None

    # Skip tiny images, icons, and data URIs
    if src.startswith('data:'):
        return None

    # Skip images that should be excluded
    if should_exclude_image(src):
        return None

    # Skip common icon paths
    if any(pattern in src.lower() for pattern in ['/icon', 'favicon', 'logo']):
        if any(ext in src.lower() for ext in ['.ico', '.svg', '.gif']):
            return None

    # Convert relative URLs to absolute
    src = urljoin(base_url, src)

    # Check if we've already downloaded this image
    images_dir = os.path.join(page_dir, 'images')
    img_filename = os.path.basename(urllib.parse.urlparse(src).path)

    # Make sure filename is valid
    img_filename = sanitize_filename(img_filename)

    # If filename is empty or doesn't have an extension, create a unique name
    if not img_filename or '.' not in img_filename:
        img_filename = f"image_{time.time()}.jpg"

    img_path = os.path.join(images_dir, img_filename)

    # If the image already exists, return its path
    if os.path.exists(img_path):
        return os.path.join('images', img_filename)

    try:
        # Create images directory if it doesn't exist
        ensure_directory_exists(images_dir)

        # Import USER_AGENTS here to avoid circular imports
        from modules.config import USER_AGENTS

        # Standard browser headers
        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': base_url,
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0'
        }

        # Use the driver's cookies if available
        if driver:
            try:
                cookies = driver.get_cookies()
            except AttributeError:
                cookies = driver
            s = requests.Session()
            for cookie in cookies:
                s.cookies.set(cookie['name'], cookie['value'])
            img_response = s.get(src, stream=True, headers=headers, timeout=5)
        else:
            img_response = requests.get(src, stream=True, headers=headers, timeout=5)

        if img_response.status_code == 200:
            # Check if the content is actually an image
            content_type = img_response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                return None

            # Check if the image is too small (likely an icon)
            content_length = int(img_response.headers.get('Content-Length', 0))
            if content_length > 0 and content_length < 5000:  # Skip images smaller than ~5KB
                return None

            with open(img_path, 'wb') as f:
                for chunk in img_response.iter_content(8192):  # Larger chunks for better performance
                    f.write(chunk)

            # Return relative path for markdown
            return os.path.join('images', img_filename)
    except Exception as e:
        logger.warning(f"Error downloading image {src}: {e}")

    return None


def create_index_file(scraped_pages, base_url, output_dir):
    """Create an index CSV file with links to all scraped pages"""
    # Get archive information
    archive_info = get_archive_info(output_dir)
    website_dir = archive_info["website_dir"]

    # Define the CSV file path
    index_path = os.path.join(output_dir, "index.csv")

    # Create the CSV file
    with open(index_path, 'w', encoding='utf-8', newline='') as csvfile:
        # Define the CSV writer and headers
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(['#', 'Page Title', 'URL', 'Directory'])

        # Write each page's information
        for i, page in enumerate(scraped_pages, 1):
            try:
                # Ensure the URL is correctly extracted
                page_url = page.get('url', '')
                if not isinstance(page_url, str):
                    page_url = str(page_url)

                # Check if the URL is valid
                if not page_url.startswith('http'):
                    # If not a valid URL, construct it from the base URL and the path
                    if 'path' in page:
                        page_url = urljoin(f"https://{base_url}", page['path'])
                    else:
                        page_url = f"https://{base_url}"

                # Get title, handling potential non-string values
                title = page.get('title', '')
                if not isinstance(title, str):
                    title = str(title)

                # Get directory, handling potential non-string values
                directory = page.get('directory', '')
                if not isinstance(directory, str):
                    directory = str(directory)

                # Create directory path for the CSV
                directory_path = f"./{directory}/content.md"

                # Write the row to the CSV
                csv_writer.writerow([i, title, page_url, directory_path])

            except Exception as e:
                logger.error(f"Error processing page in index: {e}")
                # Add error information to the CSV
                csv_writer.writerow([i, f"Error: {e}", page.get('url', 'Unknown URL'), "Error"])

    return index_path


def get_realistic_headers(url):
    """Generate realistic browser headers"""
    from modules.config import USER_AGENTS  # Import here to avoid circular imports

    parsed_url = urllib.parse.urlparse(url)
    domain = parsed_url.netloc

    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': f"https://www.google.com/search?q={domain}",
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'cross-site',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0'
    }


def get_archive_info(output_dir):
    """Get information about the archive directory structure"""
    # Split the output directory path to get archive directory and website directory
    path_parts = os.path.normpath(output_dir).split(os.sep)

    # The last part is the website directory name
    website_dir = path_parts[-1]

    # The second-to-last part is the archive directory (if available)
    archive_dir = path_parts[-2] if len(path_parts) > 1 else "_soups"

    return {
        "archive_dir": archive_dir,
        "website_dir": website_dir,
        "full_path": output_dir
    }


def markdown_to_plain_text(markdown_content):
    """Convert markdown content to plain text without formatting"""
    # Ensure input is a string
    if not isinstance(markdown_content, str):
        if isinstance(markdown_content, list):
            markdown_content = ' '.join(markdown_content)
        else:
            markdown_content = str(markdown_content)

    # Remove images
    text = re.sub(r'!\[.*?\]\(.*?\)', '', markdown_content)

    # Replace links with just their text
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)

    # Remove headings markers but keep the text
    text = re.sub(r'#{1,6}\s+(.*?)$', r'\1', text, flags=re.MULTILINE)

    # Remove blockquote markers
    text = re.sub(r'>\s+(.*?)$', r'\1', text, flags=re.MULTILINE)

    # Remove code blocks but keep the content
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)

    # Remove inline code but keep the content
    text = re.sub(r'`(.*?)`', r'\1', text)

    # Remove HTML tags
    text = re.sub(r'<.*?>', '', text)

    # Remove markdown tables
    text = re.sub(r'\|.*?\|', '', text)
    text = re.sub(r'\|[\s-]*\|', '', text)

    # Remove list markers but keep the text
    text = re.sub(r'^\s*[\*\-+]\s+(.*?)$', r'\1', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+(.*?)$', r'\1', text, flags=re.MULTILINE)

    # Remove horizontal rules
    text = re.sub(r'^\s*[\*\-_]{3,}\s*$', '', text, flags=re.MULTILINE)

    # Normalize whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'\s+', ' ', text)

    # Fix sentence spacing
    text = re.sub(r'(\. )([A-Z])', r'.\n\n\2', text)

    # Remove leading/trailing whitespace from each line
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)

    # Remove leading/trailing whitespace
    text = text.strip()

    return text


def save_plain_text(markdown_content, directory, filename='content.txt'):
    """Save plain text extracted from markdown content"""
    ensure_directory_exists(directory)

    try:
        plain_text = markdown_to_plain_text(markdown_content)
        file_path = os.path.join(directory, filename)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(plain_text)

        return file_path
    except Exception as e:
        logger.error(f"Error creating plain text file: {e}")
        # Create a minimal text file with error information
        file_path = os.path.join(directory, filename)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(f"Error creating plain text: {e}\n\nOriginal content type: {type(markdown_content)}")
        return file_path

from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

def extract_links(html_content, base_url):
    """Extracts all internal links from HTML content."""
    soup = BeautifulSoup(html_content, 'html.parser')
    links = set()
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href']
        full_url = urljoin(base_url, href)
        if urlparse(full_url).netloc == urlparse(base_url).netloc:
            links.add(full_url)
    return list(links)

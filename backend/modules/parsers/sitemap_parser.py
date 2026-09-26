import requests
import os
from lxml import etree
from modules.config import logger

def get_sitemap_urls(path_or_url):
    """
    Extracts URIs from a sitemap.xml file under <loc> tags.
    Supports both local file paths and remote URLs.
    
    Args:
        path_or_url (str): Local path or URL to sitemap.xml
        
    Returns:
        list: A list of extracted URIs
    """
    logger.info(f"Parsing sitemap from: {path_or_url}")
    
    try:
        content = b""
        if path_or_url.startswith(('http://', 'https://')):
            response = requests.get(path_or_url, timeout=30)
            response.raise_for_status()
            content = response.content
        else:
            if not os.path.exists(path_or_url):
                logger.error(f"Sitemap file not found: {path_or_url}")
                return []
            with open(path_or_url, 'rb') as f:
                content = f.read()
        
        # Parse XML with lxml
        root = etree.fromstring(content)
        
        # Extract <loc> tags using XPath (ignoring namespaces for simplicity or handling them)
        # Using local-name() is robust for different sitemap namespaces
        urls = root.xpath("//*[local-name()='loc']/text()")
        
        # Clean URLs (remove whitespace)
        urls = [url.strip() for url in urls if url]
                
        logger.info(f"Extracted {len(urls)} URLs from sitemap.")
        return urls
        
    except Exception as e:
        logger.error(f"Error parsing sitemap: {e}")
        return []

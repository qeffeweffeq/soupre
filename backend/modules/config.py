import logging
import os

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.dirname(__file__)), "scraper.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Default archive directory name
DEFAULT_ARCHIVE_DIR = "_soups"

# Add user agents rotation to avoid detection
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
]

# Common content selectors
CONTENT_SELECTORS = [
    'main', 'article',
    'div[role="main"]', '.main-content',
    '#main-content', '.content',
    '#content', '.post-content',
    '.entry-content', '.page-content',
    '.container', '#container',
    '.wrapper', '#wrapper'
]

# Cookie banner selectors
COOKIE_BUTTON_SELECTORS = [
    "button[id*='cookie' i]",
    "button[class*='cookie' i]",
    "a[id*='cookie' i]",
    "a[class*='cookie' i]",
    "button[id*='accept' i]",
    "button[class*='accept' i]",
    "button[id*='consent' i]",
    "button[class*='consent' i]",
    ".cookie-banner button",
    "#cookie-banner button",
    ".cookie-consent button",
    "#cookie-consent button",
    "button.accept-cookies",
    "button#accept-cookies",
    "[id*='cookie' i] button",
    "[class*='cookie' i] button",
    "[id*='consent' i] button",
    "[class*='consent' i] button",
    "[id*='gdpr' i] button",
    "[class*='gdpr' i] button",
    "button.agree",
    "button#agree",
    "button[class*='agree' i]",
    "button[id*='agree' i]"
]

# Accept button XPaths
ACCEPT_BUTTON_XPATHS = [
    "button[contains(text(), 'Accept')]",
    "button[contains(text(), 'Agree')]",
    "button[contains(text(), 'Accept All')]",
    "button[contains(text(), 'I Agree')]",
    "button[contains(text(), 'OK')]",
    "button[contains(text(), 'Continue')]"
]

# Breadcrumb selectors
BREADCRUMB_SELECTORS = [
    '.breadcrumb', '#breadcrumb',
    '[class*="breadcrumb"]', '[id*="breadcrumb"]',
    'nav[aria-label*="breadcrumb"]', '.breadcrumbs',
    '#breadcrumbs', 'ol.breadcrumb', 'ul.breadcrumb'
]

# Content filtering patterns (regex)
UNWANTED_CONTENT_PATTERNS = [
    r'P\.\s*IVA\s*/\s*VAT',
    r'Privacy\s*Policy',
    r'Cookie\s*Policy',
    r'Terms\s*of\s*Service',
    r'Legal\s*Notes',
    r'All\s*rights\s*reserved',
    r'©\s*\d{4}',
    r'GDPR',
    r'Cookie\s*Consent',
    r'Informativa\s*Privacy',
    r'Condizioni\s*di\s*vendita',
    r'Diritti\s*riservati'
]

# Table labels to skip
META_TABLE_LABELS = [
    "Meta contents",
    "Meta Content",
    "Informazioni meta"
]

# Regex for standalone URLs (not inside markdown links/images)
RAW_URL_PATTERN = r'(?<![\(\[])https?://[^\s<>"]+(?![\]\)])'

# Rate limiting configuration (seconds)
SCRAPE_DELAY_MIN = float(os.environ.get('SCRAPE_DELAY_MIN', 2.0))
SCRAPE_DELAY_MAX = float(os.environ.get('SCRAPE_DELAY_MAX', 5.0))

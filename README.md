# Soupre Web Archiver

**Soupre** is a high-stealth, full-stack web archiving, sitemap exploration, and content extraction platform. Engineered for resilience against anti-bot systems (Cloudflare Turnstile, AWS WAF, SiteGround Captcha), Soupre provides clean Markdown conversion, site-wide contact and icon extraction, isolated per-job parallel scraping, and an interactive real-time dashboard.

---

## Key Features

### 1. High-Stealth Browser Engine (`Camoufox` + `Playwright`)
- **Camoufox Primary**: Employs an anti-detect Firefox-based engine with authentic browser fingerprints, WebGL spoofing, and GeoIP routing.
- **Playwright Chromium Fallback**: Transparent fallback with stealth patches if Camoufox is unavailable or during browser failures.
- **Automated Anti-Bot Clearance**: Automatically detects and solves or waits for Cloudflare Turnstile, AWS WAF, and captcha challenges.
- **Consent & Preloader Dismissal**: Intelligently dismisses cookie banners (OneTrust, Cookiebot, Complianz, Usercentrics, Tarteaucitron) and preloader splash screens before scraping content.
- **Loop-Prevention Guards**: Distinguishes genuine WAF challenges from 404/410 errors and network timeouts, preventing costly, infinite browser launch loops.

### 2. Smart Sitemap Discovery & Multi-Domain Prioritization
- **Comprehensive Discovery**: Discovers standard XML sitemaps, sitemap indexes, HTML/XSL-rendered sitemaps (e.g., Yoast SEO), and `robots.txt` directives.
- **Multi-Domain Prioritization**: Automatically prioritizes exact-domain matches over foreign TLDs and subdomains, and seamlessly handles HTTP-to-HTTPS / redirect sitemaps.
- **Guaranteed Output**: Extracts and writes a normalized, clean `sitemap.xml` directly inside each scrape job directory.
- **Fail-Fast Protection**: Detects dead or unreachable origins and aborts early after consecutive timeouts.

### 3. Robust CMS Parsers & Clean Markdown Extraction
- **Modern CMS / Builder Compatibility**: Specially tuned for WordPress, Elementor, Divi, Webflow, and ASP.NET WebForms.
- **ASP.NET Form Unwrapping**: Automatically unwraps legacy `<form runat="server">` wrappers that swallow page content.
- **Hero & Background Image Extraction**: Parses inline CSS `background-image` styles (crucial for Elementor hero sections) alongside standard `<img>` and `<picture>` tags.
- **Deduplication & Cloaking Removal**: Drops cloned carousel slides (Swiper, Slick, Owl Carousel), strips desktop-hidden elements (`.elementor-hidden-desktop`), and removes hidden keyword-stuffing cloaks.
- **Clean Markdown Formatting**: Converts HTML to semantic, readable GitHub Flavored Markdown with preserved headings, lists, tables, and media links.

### 4. Site-Wide Metadata & Centralized Contact Extraction
- **Automated Icon Discovery**: Identifies and verifies high-res favicons, Apple touch icons, and Android manifest icons with Google Favicon API fallback.
- **Centralized `contact.csv`**: Extracts site-wide metadata (emails, phone numbers, Instagram, LinkedIn, physical location, founded year, description) into a single, clean `contact.csv` per job instead of duplicating it into every individual page's metadata.
- **Parked Domain & Blank Page Detection**: Automatically flags parked domains and empty pages.

### 5. Parallel Scraping & Resumable Architecture
- **True Concurrency**: Run multiple scraping jobs simultaneously without cross-job state or log interference.
- **Isolated Per-Job Logging**: Dedicated log files (`job_<id>.log`) per task streamed in real time to the frontend via Server-Sent Events (SSE).
- **Pause, Resume, and Cancel**: Interrupted or paused jobs can be resumed from the exact page offset without re-fetching completed pages.
- **Dynamic Job Lifecycle**: Real-time status states (`pending`, `running`, `completed`, `cancelled`, `failed`).

### 6. Modern Catppuccin Dashboard Frontend
- **Next.js 15 + React 19 + Tailwind CSS v4**: Blazing fast UI built with `@qfwfq/material-catppuccin-ui`.
- **Interactive Job Cards**: Live target favicon/icon, extracted page title, and friendly date timestamps (e.g., *Today, 14:30*, *Yesterday, 09:15*, *Tuesday, 18:00*).
- **Multi-Accordion Support**: Keep multiple job cards expanded simultaneously to inspect pages and monitor logs in parallel.
- **Dual-Pane Tabs**: Switch between paginated page lists (with quick links and Markdown previews) and real-time streaming terminal logs with one-click download.
- **Custom Minimal Scrollbars**: Sleek, theme-consistent scrollbar styling.
- **Zero-Collision Dev Runner**: `dev_all.py` dynamically scans and binds to free ports, eliminating port-in-use collisions.

---

## Architecture & Tech Stack

- **Backend**: Python 3.12, FastAPI, SQLModel (SQLite), Camoufox, Playwright, BeautifulSoup4, lxml, html2text.
- **Frontend**: Next.js 15, React 19, TypeScript, Tailwind CSS v4, `@qfwfq/material-catppuccin-ui`.
- **Database**: SQLite with automatic lightweight schema migrations (`backend/database/models.py`).
- **Dev Runner**: Custom Python multi-process manager (`dev_all.py`).

---

## Project Structure

```
soupre/
├── backend/
│   ├── database/             # SQLite connection & SQLModel schema definitions
│   ├── modules/
│   │   ├── core/             # Logging and application lifecycle
│   │   ├── extractors/       # Media, icon, contact, and metadata extractors
│   │   ├── parsers/          # HTML parsing, Elementor guards, and Markdown conversion
│   │   ├── scrapers/         # Camoufox/Playwright engine, fetcher, and sitemap fetcher
│   │   └── utils/            # Text, URL, file, and delay helpers
│   ├── tests/                # Automated pytest test suites
│   ├── main.py               # FastAPI application and SSE streaming endpoints
│   └── requirements.txt      # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── app/              # Next.js app router, dashboard, and modal dialogs
│   │   └── types/            # TypeScript interfaces
│   ├── package.json          # Frontend scripts and dependencies
│   └── tailwind.config.ts    # Tailwind CSS v4 configuration
├── dev_all.py                # Smart dual-service runner with dynamic port allocation
├── package.json              # Root scripts
├── _downloads/               # Output directory for all archived websites & media
└── README.md
```

---

## Archive Directory Structure (`_downloads/`)

Scraped websites are saved under `_downloads/` organized by job ID and domain slug (`job_<id>_<domain_slug>/`). Each archive maintains a deterministic, taxonomy-based hierarchy:

```
_downloads/
└── job_<job_id>_<domain_slug>/
    ├── sitemap.xml               # Complete discovered or generated sitemap
    ├── job.log                   # Isolated log stream for this scrape job
    ├── contact.csv               # Site-wide contact info (Email, Phone, Socials, Icon URL)
    ├── _images/                  # Central media directory for all downloaded site assets
    │   ├── hero-banner.jpg
    │   └── ...
    └── <taxonomy>/               # Language or section routing (e.g. `it/`, `en/`, `default/`)
        └── <category>/           # Path-derived taxonomy (e.g. `pages/`, `rooms/`, `blog/`)
            └── <page_slug>/      # Dedicated directory for each scraped page
                ├── <title>.md    # Semantic GitHub Flavored Markdown with clean media links
                ├── metadata.csv  # Page-level metadata (Title, URL, Description, Keywords)
                ├── media_index.csv # Mapping of downloaded filenames, alt text, and source URLs
                └── screenshot_<domain>_<timestamp>.png # Full-page browser screenshot
```

### Generated Artifacts Breakdown
- **`contact.csv`**: Centralizes site-wide company details (`Icon URL`, `Location`, `Email`, `Phone`, `Instagram`, `LinkedIn`, `Founded`) extracted across the domain to avoid duplicating them across every page's metadata.
- **`sitemap.xml`**: Normalized XML sitemap compiled from XML discovery, sitemap indexes, or robots.txt fallback.
- **`<page_slug>/<title>.md`**: Clean Markdown document with unwrapped ASP.NET wrappers, preserved Elementor hero background images, stripped cloaking, and deduplicated carousel slides.
- **`metadata.csv`**: Contains `URL`, `Title`, `Description`, and `Keywords` specific to that page.
- **`media_index.csv`**: Maps local filenames in `_images/` back to their `Alt Text` and `Original URL`.
- **`screenshot_*.png`**: High-resolution, full-page rendering captured by the stealth browser engine.
- **`job.log`**: Isolated real-time log trace captured during execution and streamed via SSE to the UI.

---

## Getting Started

### Prerequisites
- **Node.js**: v18+ (Node.js 20+ recommended)
- **Python**: 3.12+

### Quick Start

1. **Install Frontend Dependencies**:
   ```bash
   cd frontend
   npm install
   cd ..
   ```

2. **Setup Backend Environment**:
   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate    # or activate.fish / activate.csh depending on your shell
   pip install -r requirements.txt
   playwright install chromium
   camoufox fetch
   cd ..
   ```

3. **Start the Development Server**:
   Launch both backend and frontend simultaneously with dynamic port allocation and full color support:
   ```bash
   npm run dev:all
   ```
   The terminal will display the dynamic ports allocated (typically `3000` for the frontend and `8000` for the backend).

---

## Running Tests

Run the full backend test suite:

```bash
cd backend
source venv/bin/activate
pytest tests/
```

To run individual test modules:
```bash
pytest tests/test_browser_engine.py
pytest tests/test_sitemap_fetcher.py
pytest tests/test_parsers.py
pytest tests/test_shared_improvements.py
pytest tests/test_parallel_jobs_logging.py
```

---

## License

Private and proprietary. All rights reserved.

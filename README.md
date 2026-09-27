# Soupre

Soupre is a robust, modular web scraping and content extraction application designed to transform complex web pages into clean, highly readable Markdown files while seamlessly downloading all embedded media (high-resolution images, videos).

It features a modern dual-stack architecture:
- **Frontend**: A sleek Next.js (React) web application styled with Tailwind CSS for visualizing and managing scraped content.
- **Backend**: A high-performance Python (FastAPI/SQLModel) server powered by Playwright and BeautifulSoup.

## Current State & Key Integrations

Soupre has been extensively upgraded with advanced extraction capabilities to handle modern, dynamically loaded, and WAF-protected websites:

- **WAF & Captcha Bypass**: Built-in Playwright stealth configurations successfully navigate SiteGround "Checking the site connection security" screens, Cloudflare challenges, and other anti-bot measures. The scraper intelligently falls back to HTTP requests if needed, but orchestrates a specialized secondary Playwright context specifically for downloading Captcha-protected media assets.
- **Maximum Resolution Media Extraction**: Advanced DOM parsing algorithms scan the tree to locate `<picture>` tags, `<source>` elements, and custom data attributes (e.g., `data-media-url`) to extract the absolute highest-resolution image assets available (including retina `2x`/`3x` variants).
- **Embedded Video Downloading**: Integrates `yt-dlp` to detect Vimeo and YouTube embeds (including hidden Javascript-driven iframes) inside the DOM, automatically downloading the best quality `.mp4` videos directly to your local file system.
- **Perfectly Flattened Output**: Scraped data is structured beautifully in a single flat directory per job:
  - `PageTitle.md`: Clean Markdown content.
  - `images/`: High-resolution images safely fetched and referenced via relative links in the markdown.
  - `videos/`: Embedded videos downloaded via `yt-dlp`.
  - `metadata.csv` & `media_index.csv`: CSV indexes of the scraped media.
- **Real-time Server-Sent Events (SSE)**: The Next.js frontend streams live logs directly from the FastAPI backend using SSE, giving you line-by-line visibility into the scraping progress.

## Tech Stack

### Frontend
- **Framework**: Next.js 14+ (App Router)
- **Styling**: Tailwind CSS
- **Markdown Rendering**: `react-markdown` + `remark-gfm`

### Backend
- **Framework**: FastAPI
- **Database**: SQLite (managed via SQLModel)
- **Scraping Engine**: Playwright (Async) + `playwright-stealth`, BeautifulSoup4
- **Media Downloaders**: `yt-dlp` (videos), API Context fetch (images)

## Quick Start

You can run both the frontend and backend concurrently with a single command from the project root:

```bash
npm run dev
```

*(This command uses `fish` shell to activate the backend virtual environment and run the FastAPI server via Uvicorn, while also starting the Next.js frontend.)*

### Manual Execution

If you prefer to run them separately:

**Backend:**
```bash
cd backend
source venv/bin/activate  # or source venv/bin/activate.fish
uvicorn main:app --reload
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

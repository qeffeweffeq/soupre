# Soupre Web Archiver

Soupre is a sophisticated full-stack web archiving and scraping tool. 

## Features
- **FastAPI Backend**: Uses SQLModel/SQLite for job management and `crawl4ai` for robust web extraction.
- **Next.js Frontend**: A dynamic, modern dashboard built with Tailwind v4.
- **Real-time Logging**: Streams live Python extraction logs directly to a beautiful frontend terminal via Server-Sent Events (SSE).
- **Material Catppuccin UI**: Powered by the abstract `@qfwfq/material-catppuccin-ui` component library for a flawless visual experience.
- **Smart Dev Server**: Includes a custom `dev_all.py` script that automatically finds open ports, preventing `Address already in use` collisions when running alongside other projects.

## Getting Started

### Prerequisites
- Node.js (v18+)
- Python 3.12+

### Quick Start
To launch both the backend and frontend simultaneously with dynamic port allocation and full color support:

```bash
# 1. Install frontend dependencies
cd frontend && npm install

# 2. Setup backend virtual environment
cd ../backend
python3 -m venv venv
source venv/bin/activate.fish
pip install -r requirements.txt

# 3. Start the smart dev server from the root directory
cd ..
npm run dev:all
```
The terminal will display the dynamic ports chosen (usually `3000` for frontend and `8000` for backend).

## Development
If you are developing the UI components, use `npm link @qfwfq/material-catppuccin-ui` inside the `frontend` directory to link the local repository.

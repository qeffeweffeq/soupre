# modules/config.py

import os


# Default configuration for the DOM Raider
class Config:
    # List of URLs to start scraping from
    START_URLS = []

    # Base directory for saving downloaded media
    # Default is './downloads' or from environment variable
    OUTPUT_DIR = os.getenv("DOWNLOAD_BASE_DIR", "_downloads")
    
    # Whether to organize media files by type in subdirectories (e.g. /img/, /video/)
    # Default is False (flat directory structure)
    ORGANIZE_BY_MEDIA_TYPE = os.getenv("ORGANIZE_BY_MEDIA_TYPE", "false").lower() == "true"

    # Types of media to download (e.g., 'img', 'video', 'audio')
    # Can be extended based on specific needs
    MEDIA_TYPES = ["img", "video", "audio"]

    # Maximum number of concurrent requests
    CONCURRENT_REQUESTS = 5

    # User-Agent string for HTTP requests
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

    # Request timeout in seconds
    REQUEST_TIMEOUT = 10

    # Whether to follow links on the scraped pages
    FOLLOW_LINKS = False

    # Maximum depth for following links (0 for current page only)
    MAX_DEPTH = 0

    # Whether to run the Playwright browser in headless mode
    HEADLESS_BROWSER = True

    # File extensions for images to download
    IMG_EXTENSIONS = [".tiff", ".tif", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif"]

    # File extensions for videos to download
    VIDEO_EXTENSIONS = [".mp4", ".webm", ".ogg"]

    # File extensions for audio to download
    AUDIO_EXTENSIONS = [".mp3", ".wav", ".ogg"]

    # Configuration initialization
    def __init__(self, **kwargs):
        # Ensure the base directory exists
        os.makedirs(self.OUTPUT_DIR, exist_ok=True)
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

# You can create different configurations for different environments or use cases
# For example:
# class DevelopmentConfig(Config):
#     START_URLS = ["http://localhost:8000"]
#     CONCURRENT_REQUESTS = 2

# class ProductionConfig(Config):
#     START_URLS = ["http://example.com"]
#     CONCURRENT_REQUESTS = 10

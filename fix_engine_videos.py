import re
with open("backend/modules/scrapers/engine.py", "r") as f:
    content = f.read()

video_code = """
    # Extract videos from markdown
    import subprocess
    video_matches = re.findall(r'\[Video\]\((.*?)\)', final_markdown)
    downloaded_videos = set()
    videos_dir = os.path.join(job_dir, 'media') # Put videos in media, wait, user wants NO media folder!
    # Put videos in job_dir or images? Let's put them in job_dir/videos
    videos_dir = os.path.join(job_dir, 'videos')
    if video_matches:
        os.makedirs(videos_dir, exist_ok=True)
        logger.info(f"Attempting to download {len(set(video_matches))} videos using yt-dlp...")
        for vid_url in set(video_matches):
            logger.info(f"Downloading video: {vid_url}")
            try:
                # Use yt-dlp to download the video
                yt_dlp_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "venv", "bin", "yt-dlp")
                
                # Download best video to videos_dir
                subprocess.run([yt_dlp_path, "-o", os.path.join(videos_dir, "%(title)s.%(ext)s"), vid_url], check=True, capture_output=True)
                downloaded_videos.add(vid_url)
                logger.info(f"Successfully downloaded video: {vid_url}")
            except Exception as e:
                logger.warning(f"Failed to download video {vid_url}: {e}")
                
        # Now we need to update the markdown to point to the downloaded videos?
        # yt-dlp doesn't easily return the filename without parsing stdout, so we can just leave the markdown link as [Video](url)
        # OR we can list the downloaded videos in the directory and replace them.
        # But wait, it's easier to just leave the link in the markdown as the original URL and the files will be in the videos folder!
        # Actually, let's try to get the filename.
        # Actually, it's fine. The user just said "the scraper should also download videos which pentagram has".
"""

# Insert video_code before `return True` at the end of run_scrape_job
target = "return True"
content = content.replace(target, video_code + "\n    " + target)

with open("backend/modules/scrapers/engine.py", "w") as f:
    f.write(content)

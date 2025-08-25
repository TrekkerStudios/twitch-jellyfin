import os
import random
import time
import logging
from yt_dlp import YoutubeDL
import config
import state
from utils import load_config

# Setup yt-dlp debug logger
log_dir = os.path.join(config.BASE_DIR, "tmp")
os.makedirs(log_dir, exist_ok=True)  # make sure tmp/ exists
log_path = os.path.join(log_dir, "yt_dlp_debug.log")

logging.basicConfig(
    filename=log_path,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

class YTDLPLogger:
    def debug(self, msg):
        logging.debug(msg)
    def warning(self, msg):
        logging.warning(msg)
    def error(self, msg):
        logging.error(msg)

def fetch_youtube_videos(channels, max_videos=5):
    """Fetch exactly the latest N valid YouTube uploads (with duration filter)."""
    ydl_opts = {
        "format": "best[ext=mp4]",
        "outtmpl": os.path.join(config.YOUTUBE_DIR, "%(id)s.%(ext)s"),
        "cachedir": os.path.join(config.BASE_DIR, "yt_dlp_cache"),
        "playlistend": max_videos * 5,  # fetch a small buffer to filter from
        "logger": YTDLPLogger(),
        "progress_hooks": [lambda d: logging.info(f"yt-dlp: {d}")],
    }

    ydl = YoutubeDL(ydl_opts)
    downloaded, meta = [], []

    for channel in channels:
        try:
            # Normalize channel URL
            if channel.startswith("UC"):
                url = f"https://www.youtube.com/channel/{channel}/videos"
            else:
                # Assumes other channels are handles (either with or without @)
                handle = channel if channel.startswith("@") else f"@{channel}"
                url = f"https://www.youtube.com/{handle}/videos"

            print(f"📺 Fetching last {max_videos} valid videos for {channel}...")
            logging.info(f"Fetching from {url}")

            info = ydl.extract_info(url, download=False)
            entries = info.get("entries") or []
            valid_entries = []

            # Filter by duration until we have enough
            for e in entries:
                if not e:
                    continue
                duration = e.get("duration", 0)
                if 60 <= duration <= 10800:  # between 1 min and 3 hrs
                    valid_entries.append(e)
                if len(valid_entries) >= max_videos:
                    break

            for e in valid_entries:
                video_id = e["id"]
                path = os.path.join(config.YOUTUBE_DIR, f"{video_id}.mp4")

                if not os.path.exists(path):
                    print(f"⬇️ Downloading {e.get('title', 'Unknown')}...")
                    logging.info(f"Downloading {e.get('title')} ({e['webpage_url']})")
                    ydl.download([e["webpage_url"]])
                else:
                    print(f"✅ Already cached: {e.get('title', 'Unknown')}")
                    logging.info(f"Already cached: {e.get('title')}")

                downloaded.append(path)
                meta.append(
                    {
                        "title": e.get("title", "Unknown"),
                        "duration": f"{e['duration']//60}m{e['duration']%60}s",
                        "url": e.get("webpage_url"),
                        "path": path,
                    }
                )

        except Exception:
            import traceback
            print("⚠️ YouTube fetch error")
            logging.exception("YouTube fetch error")
            traceback.print_exc()

    return downloaded, meta

def refresh_youtube_cache():
    """Background thread: refresh YouTube cache hourly"""
    while True:
        cfg = load_config()
        state.youtube_cache, state.youtube_meta = fetch_youtube_videos(
            cfg.get("youtube_channels", [])
        )
        print(f"✅ YouTube cache refreshed: {len(state.youtube_cache)} videos")
        time.sleep(config.YOUTUBE_REFRESH)

def build_youtube_playlist():
    """Build a randomized playlist file from cached YouTube videos."""
    playlist_path = os.path.join(config.YOUTUBE_DIR, "playlist.txt")
    with open(playlist_path, "w") as f:
        if not state.youtube_cache:
            return playlist_path
        for v in random.sample(state.youtube_cache, len(state.youtube_cache)):
            f.write(f"file '{v}'\n")
    return playlist_path
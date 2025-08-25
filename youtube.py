# youtube.py
import os
import random
import time
import logging
from yt_dlp import YoutubeDL
import config
import state
from utils import load_config

# Setup yt-dlp debug logger
log_dir = config.BASE_DIR
os.makedirs(log_dir, exist_ok=True)
log_path = os.path.join(log_dir, "yt_dlp_debug.log")

yt_dlp_logger = logging.getLogger("yt-dlp-logger")
yt_dlp_logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler(log_path)
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
file_handler.setFormatter(formatter)
yt_dlp_logger.addHandler(file_handler)

with open(log_path, "a"):
    os.utime(log_path, None)

print(f"📝 yt-dlp log path: {log_path}")


class YTDLPLogger:
    def debug(self, msg):
        yt_dlp_logger.debug(msg)

    def warning(self, msg):
        yt_dlp_logger.warning(msg)

    def error(self, msg):
        yt_dlp_logger.error(msg)


def _channel_url(channel: str) -> str:
    """Normalize channel input to always point to the /videos tab."""
    channel = channel.strip()
    if channel.startswith("@"):
        return f"https://www.youtube.com/{channel}/videos"
    elif channel.startswith("UC"):
        return f"https://www.youtube.com/channel/{channel}/videos"
    elif channel.startswith("channel/"):
        return f"https://www.youtube.com/{channel}/videos"
    else:
        return f"https://www.youtube.com/@{channel}/videos"


def load_cached_videos(max_videos=5):
    """Return up to N most recent cached YouTube videos with metadata."""
    files = [
        os.path.join(config.YOUTUBE_DIR, f)
        for f in os.listdir(config.YOUTUBE_DIR)
        if f.endswith(".mp4")
    ]
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    files = files[:max_videos]

    meta = []
    for f in files:
        meta.append(
            {
                "title": os.path.basename(f),
                "duration": "unknown",
                "url": None,
                "path": f,
            }
        )
    return files, meta


def fetch_youtube_videos(channels, max_videos=5, rate_limit=10):
    """Fetch the latest N valid YouTube uploads, respecting cache, rate limit, and cleanup."""
    ydl_opts = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(config.YOUTUBE_DIR, "%(id)s.%(ext)s"),
        "cachedir": os.path.join(config.BASE_DIR, "yt_dlp_cache"),
        "playlistend": max_videos * 5,
        "logger": YTDLPLogger(),
        "progress_hooks": [lambda d: logging.info(f"yt-dlp: {d}")],
        "match_filter": "!is_short",
    }

    cookie_file = os.path.join(config.BASE_DIR, "cookies.txt")
    if os.path.exists(cookie_file):
        ydl_opts["cookiefile"] = cookie_file

    ydl = YoutubeDL(ydl_opts)
    downloaded, meta = [], []

    for channel in channels:
        try:
            url = _channel_url(channel)
            print(f"📺 Fetching last {max_videos} valid videos for {url}...")
            logging.info(f"Fetching from {url}")

            info = ydl.extract_info(url, download=False)
            entries = info.get("entries") or []

            # Filter by duration and take only the latest N
            valid_entries = []
            for e in entries:
                if not e:
                    continue
                duration = e.get("duration", 0)
                if 60 <= duration <= 10800:  # 1 min – 3 hrs
                    valid_entries.append(e)
                if len(valid_entries) >= max_videos:
                    break

            keep_ids = [e["id"] for e in valid_entries]

            for e in valid_entries:
                video_id = e["id"]
                path = os.path.join(config.YOUTUBE_DIR, f"{video_id}.mp4")

                if not os.path.exists(path):
                    print(f"⬇️ Downloading {e.get('title', 'Unknown')}...")
                    logging.info(f"Downloading {e.get('title')} ({e['webpage_url']})")
                    ydl.download([e["webpage_url"]])
                    time.sleep(rate_limit)  # rate limit between downloads
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

            # Cleanup: remove old cached files not in keep_ids
            for f in os.listdir(config.YOUTUBE_DIR):
                if f.endswith(".mp4"):
                    vid_id, _ = os.path.splitext(f)
                    if vid_id not in keep_ids:
                        old_path = os.path.join(config.YOUTUBE_DIR, f)
                        try:
                            os.remove(old_path)
                            print(f"🗑️ Removed old cached video: {f}")
                            logging.info(f"Removed old cached video: {f}")
                        except Exception as e:
                            logging.warning(f"Failed to remove {f}: {e}")

        except Exception:
            import traceback

            print("⚠️ YouTube fetch error")
            logging.exception("YouTube fetch error")
            traceback.print_exc()

    return downloaded, meta


def refresh_youtube_cache():
    """Background thread: refresh YouTube cache hourly (cache-first + cleanup)."""
    while True:
        cfg = load_config()

        # 1. Load cached files first
        cached, cached_meta = load_cached_videos(max_videos=5)
        if cached:
            state.youtube_cache, state.youtube_meta = cached, cached_meta
            print(f"⚡ Using {len(cached)} cached videos (no fetch needed)")
        else:
            # 2. If not enough cached, fetch from YouTube
            state.youtube_cache, state.youtube_meta = fetch_youtube_videos(
                cfg.get("youtube_channels", []), max_videos=5
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
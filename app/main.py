import subprocess
import requests
import json
import random
import time
import threading
import os
import datetime
import xml.etree.ElementTree as ET
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import functools
import glob

# Load config from environment
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_OAUTH_TOKEN = os.getenv("TWITCH_OAUTH_TOKEN")
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL")
UPDATE_INTERVAL = int(os.getenv("YOUTUBE_UPDATE_INTERVAL", "3600"))
FFMPEG_URL = os.getenv("FFMPEG_URL", "http://0.0.0.0:8080/channel")
HTTP_PORT = int(os.getenv("HTTP_PORT", "8090"))
MAX_VIDEO_DURATION = int(os.getenv("MAX_VIDEO_DURATION", "3600"))  # seconds
MAX_CACHE_FILES = int(os.getenv("MAX_CACHE_FILES", "50"))          # files

# Paths
DATA_DIR = "/data"
CACHE_DIR = os.path.join(DATA_DIR, "cache")
PLAYLIST_FILE = os.path.join(DATA_DIR, "playlist.json")
XMLTV_PATH = os.path.join(DATA_DIR, "guide.xml")
M3U_PATH = os.path.join(DATA_DIR, "channel.m3u")
LOGO_PATH = os.path.join(DATA_DIR, "logo.jpg")
CHANNELS_FILE = os.path.join(DATA_DIR, "channels.txt")

os.makedirs(CACHE_DIR, exist_ok=True)

# Track last modification time
_last_channels_mtime = 0
_cached_channels = []


def normalize_channel(line: str) -> str:
    """Normalize a channels.txt entry into a usable YouTube URL for yt-dlp."""
    line = line.strip()
    if not line:
        return None
    if line.startswith("http://") or line.startswith("https://"):
        return line
    if line.startswith("@"):
        return f"https://www.youtube.com/{line}/videos"
    if line.startswith("UC"):
        return f"https://www.youtube.com/channel/{line}/videos"
    return f"https://www.youtube.com/c/{line}/videos"


def load_channels():
    global _last_channels_mtime, _cached_channels
    try:
        mtime = os.path.getmtime(CHANNELS_FILE)
        if mtime != _last_channels_mtime:
            with open(CHANNELS_FILE) as f:
                raw_lines = [line.strip() for line in f if line.strip()]
            _cached_channels = [normalize_channel(line) for line in raw_lines]
            _last_channels_mtime = mtime
            print(f"Reloaded channels.txt: {_cached_channels}")
    except FileNotFoundError:
        _cached_channels = []
    return _cached_channels


def fetch_twitch_logo():
    """Fetch Twitch channel profile image and save to /data/logo.jpg"""
    url = f"https://api.twitch.tv/helix/users?login={TWITCH_CHANNEL}"
    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {TWITCH_OAUTH_TOKEN}",
    }
    try:
        r = requests.get(url, headers=headers).json()
        if "data" in r and r["data"]:
            logo_url = r["data"][0]["profile_image_url"]
            img = requests.get(logo_url).content
            with open(LOGO_PATH, "wb") as f:
                f.write(img)
            print(f"Downloaded Twitch logo: {logo_url}")
            return True
    except Exception as e:
        print(f"Failed to fetch Twitch logo: {e}")
    return False


def fetch_latest_videos(channel_url, count=5, max_duration=MAX_VIDEO_DURATION):
    cmd = [
        "yt-dlp",
        channel_url,
        "--flat-playlist",
        "--dump-json",
        "--playlist-end", str(count * 3),
    ]
    try:
        output = subprocess.check_output(cmd).decode().splitlines()
    except subprocess.CalledProcessError as e:
        print(f"yt-dlp failed for {channel_url}: {e}")
        return []

    videos = []
    for line in output:
        try:
            data = json.loads(line)
            if data.get("live_status") in ("is_live", "is_upcoming"):
                continue
            duration = data.get("duration")
            if duration and duration > max_duration:
                continue

            video_id = data["id"]
            title = data.get("title", f"YouTube Video {video_id}")
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            out_file_template = os.path.join(CACHE_DIR, f"{video_id}.%(ext)s")

            # Check if already cached
            matches = glob.glob(os.path.join(CACHE_DIR, f"{video_id}.*"))
            if not matches:
                print(f"Downloading {video_url} to cache...")
                try:
                    subprocess.run(
                        [
                            "yt-dlp",
                            "-f", "bestvideo+bestaudio/best",
                            "-o", out_file_template,
                            video_url,
                        ],
                        check=True,
                    )
                except subprocess.CalledProcessError as e:
                    print(f"Failed to download {video_url}: {e}")
                    continue
                matches = glob.glob(os.path.join(CACHE_DIR, f"{video_id}.*"))

            if matches:
                videos.append({"title": title, "file": matches[0]})
        except Exception as e:
            print(f"Error parsing yt-dlp output: {e}")
            continue

        if len(videos) >= count:
            break

    return videos


def cleanup_cache(max_files=MAX_CACHE_FILES):
    files = sorted(
        [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR)],
        key=os.path.getmtime,
        reverse=True,
    )
    for f in files[max_files:]:
        os.remove(f)
        print(f"Removed old cached file: {f}")


def update_channels():
    while True:
        playlist = {}
        channels = load_channels()
        for ch in channels:
            try:
                playlist[ch] = fetch_latest_videos(ch)
            except Exception as e:
                print(f"Error updating {ch}: {e}")
        with open(PLAYLIST_FILE, "w") as f:
            json.dump(playlist, f, indent=2)
        cleanup_cache()
        print("Updated playlist")
        time.sleep(UPDATE_INTERVAL)


def is_twitch_live():
    url = f"https://api.twitch.tv/helix/streams?user_login={TWITCH_CHANNEL}"
    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {TWITCH_OAUTH_TOKEN}",
    }
    try:
        r = requests.get(url, headers=headers).json()
        print("Twitch API response:", r)  # <-- debug
        return bool(r.get("data"))
    except Exception as e:
        print(f"Twitch check failed: {e}")
        return False


def run_ffmpeg(input_src, is_twitch=False):
    if is_twitch:
        twitch_url = subprocess.check_output(
            ["streamlink", "--stream-url", f"twitch.tv/{TWITCH_CHANNEL}", "best"]
        ).decode().strip()
        return subprocess.Popen(
            [
                "ffmpeg", "-re", "-i", twitch_url,
                "-c:v", "libx264", "-preset", "veryfast",
                "-c:a", "aac",
                "-f", "mpegts", "-listen", "1",
                FFMPEG_URL,
            ]
        )

    return subprocess.Popen(
        [
            "ffmpeg", "-re", "-i", input_src,
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac",
            "-f", "mpegts", "-listen", "1",
            FFMPEG_URL,
        ]
    )


def generate_m3u():
    channel_name = TWITCH_CHANNEL.capitalize() if TWITCH_CHANNEL else "Live Channel"
    logo_url = f"http://{os.environ.get('HOSTNAME', 'localhost')}:{HTTP_PORT}/logo.jpg"
    with open(M3U_PATH, "w") as f:
        f.write("#EXTM3U\n")
        f.write(
            f'#EXTINF:-1 tvg-id="{TWITCH_CHANNEL}" tvg-name="{channel_name}" '
            f'tvg-logo="{logo_url}" group-title="Twitch",{channel_name}\n'
        )
        f.write(FFMPEG_URL + "\n")
    print(f"Generated M3U for {channel_name}")


def write_xmltv(program_title, duration_minutes=60):
    channel_name = TWITCH_CHANNEL.capitalize() if TWITCH_CHANNEL else "Live Channel"
    now = datetime.datetime.utcnow()
    start = now.strftime("%Y%m%d%H%M%S +0000")
    stop = (now + datetime.timedelta(minutes=duration_minutes)).strftime(
        "%Y%m%d%H%M%S +0000"
    )

    tv = ET.Element("tv")
    channel = ET.SubElement(tv, "channel", id=TWITCH_CHANNEL)
    ET.SubElement(channel, "display-name").text = channel_name
    ET.SubElement(channel, "icon", src="logo.jpg")

    prog = ET.SubElement(tv, "programme", start=start, stop=stop, channel=TWITCH_CHANNEL)
    ET.SubElement(prog, "title", lang="en").text = program_title

    tree = ET.ElementTree(tv)
    tree.write(XMLTV_PATH, encoding="utf-8", xml_declaration=True)
    print(f"Updated XMLTV: {program_title} on {channel_name}")


def run_test_pattern():
    print("No Twitch or YouTube videos available — serving test pattern...")
    return subprocess.Popen(
        [
            "ffmpeg",
            "-f", "lavfi", "-i", "smptebars=size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100",
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac",
            "-shortest",
            "-f", "mpegts", "-listen", "1",
            FFMPEG_URL,
        ]
    )
    

def try_twitch():
    """Try to resolve Twitch stream with streamlink. Returns URL if live, else None."""
    cmd = [
        "streamlink",
        "--stream-url",
        f"twitch.tv/{TWITCH_CHANNEL}",
        "best"
    ]

    # If user OAuth token is set, add it
    user_oauth = os.getenv("TWITCH_USER_OAUTH")
    if user_oauth:
        cmd.insert(1, "--twitch-api-header")
        cmd.insert(2, f"Authorization=OAuth {user_oauth}")

    try:
        twitch_url = subprocess.check_output(cmd).decode().strip()
        return twitch_url
    except subprocess.CalledProcessError as e:
        print(f"Twitch not live or auth failed: {e}")
        return None
    

def play_loop():
    while True:
        if not os.path.exists(PLAYLIST_FILE):
            time.sleep(5)
            continue

        playlist = json.load(open(PLAYLIST_FILE))
        videos = [v for vids in playlist.values() for v in vids]
        random.shuffle(videos)

        if not videos and not try_twitch():
            write_xmltv("Test Pattern")
            proc = run_test_pattern()
            proc.wait()
            time.sleep(5)
            continue

        for video in videos:
            try:
                twitch_url = try_twitch()
                if twitch_url:
                    print("Twitch is live, switching...")
                    write_xmltv(f"Twitch Live: {TWITCH_CHANNEL}")
                    proc = run_ffmpeg(twitch_url)
                else:
                    print(f"Playing YouTube VOD: {video['title']}")
                    write_xmltv(f"YouTube: {video['title']}")
                    proc = run_ffmpeg(video["file"])

                proc.wait()
            except Exception as e:
                print(f"Error in play loop: {e}")
            time.sleep(5)


def serve_files():
    os.chdir(DATA_DIR)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=DATA_DIR)
    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), handler)
    print(f"Serving M3U/XMLTV/logo on port {HTTP_PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    fetch_twitch_logo()
    threading.Thread(target=update_channels, daemon=True).start()
    threading.Thread(target=serve_files, daemon=True).start()
    generate_m3u()
    play_loop()
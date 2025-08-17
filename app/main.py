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

# Load config from environment
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_OAUTH_TOKEN = os.getenv("TWITCH_OAUTH_TOKEN")
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL")
UPDATE_INTERVAL = int(os.getenv("YOUTUBE_UPDATE_INTERVAL", "3600"))
FFMPEG_URL = os.getenv("FFMPEG_URL", "http://0.0.0.0:8080/channel")
HTTP_PORT = int(os.getenv("HTTP_PORT", "8090"))

# Paths
PLAYLIST_FILE = "/data/playlist.json"
XMLTV_PATH = "/data/guide.xml"
M3U_PATH = "/data/channel.m3u"
LOGO_PATH = "/data/logo.jpg"
CHANNELS_FILE = "/channels.txt"

# Track last modification time
_last_channels_mtime = 0
_cached_channels = []


def load_channels():
    """Load channel IDs from channels.txt, auto-reload if file changed"""
    global _last_channels_mtime, _cached_channels
    try:
        mtime = os.path.getmtime(CHANNELS_FILE)
        if mtime != _last_channels_mtime:
            with open(CHANNELS_FILE) as f:
                _cached_channels = [line.strip() for line in f if line.strip()]
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


def fetch_latest_videos(channel_id, count=5):
    cmd = [
        "yt-dlp",
        f"https://www.youtube.com/channel/{channel_id}",
        "--flat-playlist",
        "--get-id",
        "--playlist-end",
        str(count),
    ]
    ids = subprocess.check_output(cmd).decode().splitlines()
    return [f"https://www.youtube.com/watch?v={vid}" for vid in ids]


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
        return bool(r.get("data"))
    except Exception as e:
        print(f"Twitch check failed: {e}")
        return False


def get_twitch_url():
    return (
        subprocess.check_output(
            ["streamlink", "--stream-url", f"twitch.tv/{TWITCH_CHANNEL}", "best"]
        )
        .decode()
        .strip()
    )


def run_ffmpeg(input_src):
    return subprocess.Popen(
        [
            "ffmpeg",
            "-re",
            "-i",
            input_src,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-f",
            "mpegts",
            FFMPEG_URL,
        ]
    )


def generate_m3u():
    logo_url = f"http://{os.environ.get('HOSTNAME', 'localhost')}:{HTTP_PORT}/logo.jpg"
    with open(M3U_PATH, "w") as f:
        f.write("#EXTM3U\n")
        f.write(
            f'#EXTINF:-1 tvg-id="custom1" tvg-name="My Live Channel" '
            f'tvg-logo="{logo_url}" group-title="Custom",My Live Channel\n'
        )
        f.write(FFMPEG_URL + "\n")
    print("Generated M3U")


def write_xmltv(program_title, duration_minutes=60):
    now = datetime.datetime.utcnow()
    start = now.strftime("%Y%m%d%H%M%S +0000")
    stop = (now + datetime.timedelta(minutes=duration_minutes)).strftime(
        "%Y%m%d%H%M%S +0000"
    )

    tv = ET.Element("tv")
    channel = ET.SubElement(tv, "channel", id="custom1")
    ET.SubElement(channel, "display-name").text = "My Live Channel"
    ET.SubElement(channel, "icon", src="logo.jpg")

    prog = ET.SubElement(tv, "programme", start=start, stop=stop, channel="custom1")
    ET.SubElement(prog, "title", lang="en").text = program_title

    tree = ET.ElementTree(tv)
    tree.write(XMLTV_PATH, encoding="utf-8", xml_declaration=True)
    print(f"Updated XMLTV: {program_title}")


def play_loop():
    while True:
        if not os.path.exists(PLAYLIST_FILE):
            time.sleep(5)
            continue

        playlist = json.load(open(PLAYLIST_FILE))
        videos = [v for vids in playlist.values() for v in vids]
        random.shuffle(videos)

        for video in videos:
            try:
                if is_twitch_live():
                    print("Twitch is live, switching...")
                    twitch_url = get_twitch_url()
                    write_xmltv(f"Twitch Live: {TWITCH_CHANNEL}")
                    proc = run_ffmpeg(twitch_url)
                else:
                    print(f"Playing {video}")
                    write_xmltv(f"YouTube: {video}")
                    proc = run_ffmpeg(video)

                proc.wait()
            except Exception as e:
                print(f"Error in play loop: {e}")
            time.sleep(5)


def serve_files():
    os.chdir("/data")
    handler = functools.partial(SimpleHTTPRequestHandler, directory="/data")
    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), handler)
    print(f"Serving M3U/XMLTV/logo on port {HTTP_PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    fetch_twitch_logo()
    threading.Thread(target=update_channels, daemon=True).start()
    threading.Thread(target=serve_files, daemon=True).start()
    generate_m3u()
    play_loop()
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
import tempfile
import stat

# =========
# Settings
# =========

# Load config from environment
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_OAUTH_TOKEN = os.getenv("TWITCH_OAUTH_TOKEN")
TWITCH_USER_OAUTH = os.getenv("TWITCH_USER_OAUTH")
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL")
UPDATE_INTERVAL = int(os.getenv("YOUTUBE_UPDATE_INTERVAL", "3600"))
HTTP_PORT = int(os.getenv("HTTP_PORT", "8090"))
MAX_VIDEO_DURATION = int(os.getenv("MAX_VIDEO_DURATION", "3600"))
MAX_CACHE_FILES = int(os.getenv("MAX_CACHE_FILES", "50"))
EXTERNAL_HOST_IP = os.getenv("EXTERNAL_HOST_IP", "localhost")
CHANNEL_DISPLAY_NAME = os.getenv("CHANNEL_DISPLAY_NAME")

# New/adjustable
INPUT_FIFO = os.getenv("INPUT_FIFO", "/data/input_fifo")
HLS_TIME = int(os.getenv("HLS_TIME", "5"))
HLS_LIST_SIZE = int(os.getenv("HLS_LIST_SIZE", "6"))
YTDLP_FORMAT = os.getenv(
    "YTDLP_FORMAT",
    "bv*[vcodec~='(avc1|h264)']+ba[acodec~='(mp4a|aac)']/b[ext=mp4]/best",
)

DEBUG_FFMPEG = os.getenv("DEBUG_FFMPEG", "false").lower() in ("1", "true", "yes")

# Paths
DATA_DIR = "/data"
CACHE_DIR = os.path.join(DATA_DIR, "cache")
HLS_DIR = os.path.join(DATA_DIR, "hls")
PLAYLIST_FILE = os.path.join(DATA_DIR, "playlist.json")
XMLTV_PATH = os.path.join(DATA_DIR, "guide.xml")
M3U_PATH = os.path.join(DATA_DIR, "channel.m3u")
LOGO_PATH = os.path.join(DATA_DIR, "logo.jpg")
CHANNELS_FILE = os.path.join(DATA_DIR, "channels.txt")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(HLS_DIR, exist_ok=True)

_last_channels_mtime = 0
_cached_channels = []

# ====================
# Utility / File I/O
# ====================


def atomic_write_json(path: str, obj) -> None:
    d = os.path.dirname(path)
    with tempfile.NamedTemporaryFile("w", dir=d, delete=False) as tf:
        json.dump(obj, tf, indent=2)
        tmp = tf.name
    os.replace(tmp, path)


def safe_load_json(path: str, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def ensure_fifo(path: str):
    if os.path.exists(path):
        st = os.stat(path)
        if not stat.S_ISFIFO(st.st_mode):
            os.remove(path)
            os.mkfifo(path)
    else:
        os.mkfifo(path)


def stop_process(proc: subprocess.Popen, name: str, timeout: float = 5.0):
    if not proc:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception as e:
        print(f"Failed to stop {name}: {e}")


# ====================
# FFmpeg Runner
# ====================


def run_ffmpeg(args: list[str], name: str) -> subprocess.Popen:
    """Run ffmpeg with optional debug logging."""
    if DEBUG_FFMPEG:
        full_args = ["ffmpeg", "-hide_banner", "-loglevel", "info"] + args
        print(f"[FFMPEG-DEBUG] Starting {name}: {' '.join(full_args)}")
        return subprocess.Popen(full_args)
    else:
        full_args = ["ffmpeg", "-hide_banner", "-loglevel", "error"] + args
        print(f"[FFMPEG] Starting {name}")
        return subprocess.Popen(
            full_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


# ====================
# Channels + YouTube
# ====================


def normalize_channel(line: str) -> str | None:
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
                raw_lines = [
                    line.strip()
                    for line in f
                    if line.strip() and not line.strip().startswith("#")
                ]
            _cached_channels = [normalize_channel(line) for line in raw_lines if line]
            _last_channels_mtime = mtime
            print(f"Reloaded channels.txt: {_cached_channels}")
    except FileNotFoundError:
        _cached_channels = []
    return _cached_channels


def get_channel_name():
    if CHANNEL_DISPLAY_NAME:
        return CHANNEL_DISPLAY_NAME
    if TWITCH_CHANNEL:
        return TWITCH_CHANNEL.capitalize()
    return "Live Channel"


def fetch_latest_videos(channel_url, count=5, max_duration=MAX_VIDEO_DURATION):
    cmd = [
        "yt-dlp",
        channel_url,
        "--flat-playlist",
        "--dump-json",
        "--playlist-end",
        str(count * 3),
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

            vid = data["id"]
            title = data.get("title", f"YouTube Video {vid}")
            url = f"https://www.youtube.com/watch?v={vid}"
            out_tpl = os.path.join(CACHE_DIR, f"{vid}.%(ext)s")

            matches = glob.glob(os.path.join(CACHE_DIR, f"{vid}.*"))
            if not matches:
                print(f"Downloading {url} to cache...")
                try:
                    subprocess.run(
                        [
                            "yt-dlp",
                            "-f",
                            YTDLP_FORMAT,
                            "-o",
                            out_tpl,
                            "--merge-output-format",
                            "mp4",
                            url,
                        ],
                        check=True,
                    )
                except subprocess.CalledProcessError as e:
                    print(f"Failed to download {url}: {e}")
                    continue
                matches = glob.glob(os.path.join(CACHE_DIR, f"{vid}.*"))

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
        try:
            os.remove(f)
            print(f"Removed old cached file: {f}")
        except Exception as e:
            print(f"Failed to remove cached file {f}: {e}")


def update_channels():
    while True:
        playlist = {}
        channels = load_channels()
        for ch in channels:
            try:
                playlist[ch] = fetch_latest_videos(ch)
            except Exception as e:
                print(f"Error updating {ch}: {e}")
        try:
            atomic_write_json(PLAYLIST_FILE, playlist)
        except Exception as e:
            print(f"Failed writing playlist.json: {e}")
        cleanup_cache()
        print("Updated playlist")
        time.sleep(UPDATE_INTERVAL)


# =============
# Twitch / EPG
# =============


def fetch_twitch_logo():
    if not (TWITCH_CLIENT_ID and TWITCH_OAUTH_TOKEN and TWITCH_CHANNEL):
        return False
    url = f"https://api.twitch.tv/helix/users?login={TWITCH_CHANNEL}"
    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {TWITCH_OAUTH_TOKEN}",
    }
    try:
        r = requests.get(url, headers=headers, timeout=15).json()
        if "data" in r and r["data"]:
            logo_url = r["data"][0]["profile_image_url"]
            img = requests.get(logo_url, timeout=15).content
            with open(LOGO_PATH, "wb") as f:
                f.write(img)
            print(f"Downloaded Twitch logo: {logo_url}")
            return True
    except Exception as e:
        print(f"Failed to fetch Twitch logo: {e}")
    return False


def try_twitch():
    if not TWITCH_CHANNEL:
        return None
    cmd = ["streamlink", "--stream-url", f"twitch.tv/{TWITCH_CHANNEL}", "best"]
    if TWITCH_USER_OAUTH:
        cmd[1:1] = ["--twitch-api-header", f"Authorization=OAuth {TWITCH_USER_OAUTH}"]
    try:
        twitch_url = subprocess.check_output(cmd, timeout=20).decode().strip()
        return twitch_url or None
    except subprocess.CalledProcessError:
        return None
    except Exception as e:
        print(f"try_twitch error: {e}")
        return None


def write_xmltv(program_title, duration_minutes=60):
    channel_name = get_channel_name()
    now = datetime.datetime.utcnow()
    start = now.strftime("%Y%m%d%H%M%S +0000")
    stop = (now + datetime.timedelta(minutes=duration_minutes)).strftime(
        "%Y%m%d%H%M%S +0000"
    )

    tv = ET.Element("tv")
    channel = ET.SubElement(tv, "channel", id=TWITCH_CHANNEL or "live")
    ET.SubElement(channel, "display-name").text = channel_name
    ET.SubElement(
        channel, "icon", src=f"http://{EXTERNAL_HOST_IP}:{HTTP_PORT}/logo.jpg"
    )

    prog = ET.SubElement(
        tv, "programme", start=start, stop=stop, channel=TWITCH_CHANNEL or "live"
    )
    ET.SubElement(prog, "title", lang="en").text = program_title

    tree = ET.ElementTree(tv)
    tree.write(XMLTV_PATH, encoding="utf-8", xml_declaration=True)
    print(f"Updated XMLTV: {program_title} on {channel_name}")


def generate_m3u():
    channel_name = get_channel_name()
    logo_url = f"http://{EXTERNAL_HOST_IP}:{HTTP_PORT}/logo.jpg"
    with open(M3U_PATH, "w") as f:
        f.write("#EXTM3U\n")
        f.write(
            f'#EXTINF:-1 tvg-id="{TWITCH_CHANNEL or "live"}" '
            f'tvg-name="{channel_name}" '
            f'tvg-logo="{logo_url}" '
            f'group-title="Twitch",{channel_name}\n'
        )
        f.write(f"http://{EXTERNAL_HOST_IP}:{HTTP_PORT}/hls/stream.m3u8\n")
    print(f"Generated M3U for {channel_name}")


# ============================
# HLS Segmenter + Feeders (FIFO)
# ============================


def start_hls_segmenter() -> subprocess.Popen:
    args = [
        "-fflags",
        "+genpts",
        "-i",
        INPUT_FIFO,
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        str(HLS_TIME),
        "-hls_list_size",
        str(HLS_LIST_SIZE),
        "-hls_flags",
        "delete_segments+append_list+program_date_time",
        "-hls_segment_filename",
        os.path.join(HLS_DIR, "segment_%09d.ts"),
        os.path.join(HLS_DIR, "stream.m3u8"),
    ]
    return run_ffmpeg(args, "HLS segmenter")


def start_test_feeder() -> subprocess.Popen:
    args = [
        "-re",
        "-f",
        "lavfi",
        "-i",
        "smptebars=size=1280x720:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=44100",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "baseline",
        "-g",
        "60",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-b:a",
        "128k",
        "-f",
        "mpegts",
        INPUT_FIFO,
    ]
    return run_ffmpeg(args, "Test feeder")


def start_twitch_feeder(twitch_url: str) -> subprocess.Popen:
    args = [
        "-re",
        "-i",
        twitch_url,
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-f",
        "mpegts",
        INPUT_FIFO,
    ]
    return run_ffmpeg(args, "Twitch feeder")


def start_file_feeder(path: str) -> subprocess.Popen:
    args = [
        "-re",
        "-i",
        path,
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-f",
        "mpegts",
        INPUT_FIFO,
    ]
    return run_ffmpeg(args, f"File feeder ({path})")


def serve_files():
    os.chdir(DATA_DIR)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=DATA_DIR)
    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), handler)
    print(f"Serving M3U/XMLTV/logo/HLS on port {HTTP_PORT}")
    httpd.serve_forever()


# ===============
# Playback Logic
# ===============


def flatten_videos() -> list[dict]:
    playlist = safe_load_json(PLAYLIST_FILE, {})
    videos = [v for vids in playlist.values() for v in vids]
    random.shuffle(videos)
    return videos


def play_loop():
    current_feeder = None
    last_program = None

    while True:
        try:
            twitch_url = try_twitch()
            if twitch_url:
                if last_program != f"Twitch Live: {TWITCH_CHANNEL}":
                    write_xmltv(f"Twitch Live: {TWITCH_CHANNEL}")
                    last_program = f"Twitch Live: {TWITCH_CHANNEL}"

                stop_process(current_feeder, "feeder")
                current_feeder = start_twitch_feeder(twitch_url)

                while True:
                    time.sleep(5)
                    if current_feeder.poll() is not None:
                        break
                    if not try_twitch():
                        stop_process(current_feeder, "twitch feeder")
                        break
                continue

            videos = flatten_videos()
            if not videos:
                if last_program != "Test Pattern":
                    write_xmltv("Test Pattern")
                    last_program = "Test Pattern"
                if not current_feeder or current_feeder.poll() is not None:
                    stop_process(current_feeder, "feeder")
                    current_feeder = start_test_feeder()
                time.sleep(5)
                continue

            for video in videos:
                if try_twitch():
                    break

                title = video.get("title", "YouTube VOD")
                path = video.get("file")
                if not path or not os.path.exists(path):
                    continue

                if last_program != f"YouTube: {title}":
                    write_xmltv(f"YouTube: {title}")
                    last_program = f"YouTube: {title}"

                stop_process(current_feeder, "feeder")
                current_feeder = start_file_feeder(path)

                while True:
                    time.sleep(3)
                    if try_twitch():
                        stop_process(current_feeder, "file feeder")
                        break
                    if current_feeder.poll() is not None:
                        break
        except Exception as e:
            print(f"Error in play loop: {e}")
            time.sleep(5)


# =========
# Startup
# =========

if __name__ == "__main__":
    if not os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, "w") as f:
            f.write("# Add YouTube channel IDs, @handles, or full channel URLs here\n")
            f.write("# One per line, e.g.:\n")
            f.write("# @LinusTechTips\n")
            f.write("# https://www.youtube.com/channel/UC-lHJZR3Gqxm24_Vd_D_aWg\n")
        print(f"Created default {CHANNELS_FILE}")

    fetch_twitch_logo()
    threading.Thread(target=update_channels, daemon=True).start()
    threading.Thread(target=serve_files, daemon=True).start()
    generate_m3u()

    ensure_fifo(INPUT_FIFO)
    hls_proc = start_hls_segmenter()
    seed_feeder = start_test_feeder()

    try:
        play_loop()
    finally:
        stop_process(seed_feeder, "seed feeder")
        stop_process(hls_proc, "hls segmenter")
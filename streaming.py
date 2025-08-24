import subprocess
import time
import os
from streamlink import Streamlink
import config
import state
from utils import stop_writer
from youtube import build_youtube_playlist

def start_ffmpeg():
    """Start persistent FFmpeg reading from pipe"""
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
        "-re", "-i", config.PIPE_PATH,
        "-c:v", "libx264", "-preset", "veryfast",
        "-c:a", "aac",
        "-f", "hls",
        "-hls_time", "4", "-hls_list_size", "5",
        "-hls_flags", "delete_segments",
        "-hls_segment_filename", os.path.join(config.HLS_DIR, "stream%d.ts"),
        os.path.join(config.HLS_DIR, "stream.m3u8"),
    ]
    log_file = open(config.FFMPEG_LOG, "a")
    subprocess.Popen(ffmpeg_cmd, stdout=log_file, stderr=log_file)
    print("🎬 Persistent FFmpeg started (logs → ffmpeg.log)")

def write_twitch():
    stop_writer()
    print("🔴 Writing Twitch stream...")
    session = Streamlink()
    streams = session.streams(f"https://twitch.tv/{config.CHANNEL}")
    if "best" not in streams:
        print("⚠️ Twitch channel offline.")
        return False
    hls_url = streams["best"].url
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-re", "-hide_banner", "-loglevel", "info",
        "-i", hls_url,
        "-c", "copy", "-f", "mpegts", config.PIPE_PATH
    ]
    log_file = open(config.FFMPEG_LOG, "a")
    state.current_writer_proc = subprocess.Popen(ffmpeg_cmd, stdout=log_file, stderr=log_file)
    state.current_source = "twitch"
    return True


def write_youtube():
    stop_writer()
    if not state.youtube_cache:
        print("⚠️ No YouTube videos cached.")
        return False
    playlist = build_youtube_playlist()
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-re", "-hide_banner", "-loglevel", "info",
        "-f", "concat", "-safe", "0", "-i", playlist,
        "-c", "copy", "-f", "mpegts", config.PIPE_PATH
    ]
    log_file = open(config.FFMPEG_LOG, "a")
    state.current_writer_proc = subprocess.Popen(ffmpeg_cmd, stdout=log_file, stderr=log_file)
    state.current_source = "youtube"
    return True


def write_fallback():
    stop_writer()
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-re", "-hide_banner", "-loglevel", "info",
        "-f", "lavfi", "-i", "smptebars=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100",
        "-c:v", "libx264", "-preset", "veryfast",
        "-c:a", "aac", "-f", "mpegts", config.PIPE_PATH
    ]
    log_file = open(config.FFMPEG_LOG, "a")
    state.current_writer_proc = subprocess.Popen(ffmpeg_cmd, stdout=log_file, stderr=log_file)
    state.current_source = "fallback"
    return True

def graceful_switch(new_source):
    """Insert fallback for a few seconds before switching sources"""
    print("🟨 Graceful switch: inserting fallback...")
    write_fallback()
    time.sleep(3)  # 3s of bars/tone

    if new_source == "twitch":
        print("🔄 Switching to Twitch...")
        write_twitch()
    elif new_source == "youtube":
        print("🔄 Switching to YouTube filler...")
        write_youtube()
    else:
        print("🔄 Staying on fallback...")
        write_fallback()

def orchestrator():
    while True:
        live = False
        try:
            session = Streamlink()
            streams = session.streams(f"https://twitch.tv/{config.CHANNEL}")
            live = "best" in streams
        except Exception:
            live = False

        if live and state.current_source != "twitch":
            graceful_switch("twitch")
        elif not live:
            if state.youtube_cache and state.current_source != "youtube":
                graceful_switch("youtube")
            elif not state.youtube_cache and state.current_source != "fallback":
                graceful_switch("fallback")

        time.sleep(config.CHECK_INTERVAL)

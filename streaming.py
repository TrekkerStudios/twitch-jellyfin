import subprocess
import time
import os
from streamlink import Streamlink
import config
import state
from utils import stop_writer, load_config
from youtube import build_youtube_playlist


def start_ffmpeg():
    """Start persistent FFmpeg writing MPEG-TS directly for Jellyfin"""
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "info",
        "-re",
        "-i", config.PIPE_PATH,

        # Video
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",   # good for live streaming
        "-pix_fmt", "yuv420p",

        # Audio
        "-c:a", "aac",
        "-b:a", "192k",

        # MPEG-TS container options
        "-f", "mpegts",
        "-mpegts_flags", "resend_headers+initial_discontinuity",
        "-muxrate", "8000k",      # optional: constant mux rate for IPTV
        "-muxdelay", "0.7",       # reduce latency
        "-muxpreload", "0.7",
        "-pat_period", "0.5",     # send PAT/PMT tables every 0.5s
        "-pcr_period", "20",      # PCR interval (ms)

        os.path.join(config.HLS_DIR, "stream.ts"),
    ]

    log_file = open(config.FFMPEG_LOG, "a")
    subprocess.Popen(ffmpeg_cmd, stdout=log_file, stderr=log_file)
    print("🎬 Persistent FFmpeg started (MPEG-TS output, Jellyfin-friendly)")


def write_twitch(channel):
    stop_writer()
    print(f"🔴 Writing Twitch stream for {channel}...")
    session = Streamlink()
    streams = session.streams(f"https://twitch.tv/{channel}")
    if "best" not in streams:
        print("⚠️ Twitch channel offline.")
        return False
    hls_url = streams["best"].url
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-re",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        hls_url,
        "-c",
        "copy",
        "-f",
        "mpegts",
        config.PIPE_PATH,
    ]
    log_file = open(config.FFMPEG_LOG, "a")
    state.current_writer_proc = subprocess.Popen(
        ffmpeg_cmd, stdout=log_file, stderr=log_file
    )
    state.current_source = "twitch"
    return True


def write_youtube():
    stop_writer()
    if not state.youtube_cache:
        print("⚠️ No YouTube videos cached.")
        return False

    cfg = load_config()
    playlist = build_youtube_playlist()

    if cfg.get("youtube_transcode", True):
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-re",
            "-hide_banner",
            "-loglevel",
            "info",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            playlist,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(cfg.get("youtube_crf", 20)),
            "-c:a",
            "aac",
            "-b:a",
            cfg.get("youtube_audio_bitrate", "192k"),
            "-f",
            "mpegts",
            config.PIPE_PATH,
        ]
        print(
            f"🎥 YouTube → transcoding (CRF {cfg.get('youtube_crf',20)}, {cfg.get('youtube_audio_bitrate','192k')} audio)"
        )
    else:
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-re",
            "-hide_banner",
            "-loglevel",
            "info",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            playlist,
            "-c",
            "copy",
            "-f",
            "mpegts",
            config.PIPE_PATH,
        ]
        print("⚡ YouTube → remux (no re-encode)")

    log_file = open(config.FFMPEG_LOG, "a")
    state.current_writer_proc = subprocess.Popen(
        ffmpeg_cmd, stdout=log_file, stderr=log_file
    )
    state.current_source = "youtube"
    return True


def write_fallback():
    stop_writer()
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-re",
        "-hide_banner",
        "-loglevel",
        "info",
        "-f",
        "lavfi",
        "-i",
        "smptebars=size=1280x720:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=44100",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-f",
        "mpegts",
        config.PIPE_PATH,
    ]
    log_file = open(config.FFMPEG_LOG, "a")
    state.current_writer_proc = subprocess.Popen(
        ffmpeg_cmd, stdout=log_file, stderr=log_file
    )
    state.current_source = "fallback"
    return True


def graceful_switch(new_source, channel=None):
    """Insert fallback for a few seconds before switching sources"""
    print("🟨 Graceful switch: inserting fallback...")
    write_fallback()
    time.sleep(3)  # 3s of bars/tone

    if new_source == "twitch":
        print("🔄 Switching to Twitch...")
        write_twitch(channel)
    elif new_source == "youtube":
        print("🔄 Switching to YouTube filler...")
        write_youtube()
    else:
        print("🔄 Staying on fallback...")
        write_fallback()


def orchestrator():
    while True:
        cfg = load_config()
        channel = cfg.get("twitch_channel", "ludwig")
        live = False
        try:
            session = Streamlink()
            streams = session.streams(f"https://twitch.tv/{channel}")
            live = "best" in streams
        except Exception:
            live = False

        if live and state.current_source != "twitch":
            graceful_switch("twitch", channel=channel)
        elif not live:
            if state.youtube_cache and state.current_source != "youtube":
                graceful_switch("youtube")
            elif not state.youtube_cache and state.current_source != "fallback":
                graceful_switch("fallback")

        time.sleep(config.CHECK_INTERVAL)
import os
import subprocess
import json
import time
import state
import config
import requests


def cleanup():
    """Ensure dirs exist and clear old files"""
    os.makedirs(config.HLS_DIR, exist_ok=True)
    os.makedirs(config.YOUTUBE_DIR, exist_ok=True)
    os.makedirs(os.path.join("static", "logos"), exist_ok=True)

    for f in os.listdir(config.HLS_DIR):
        os.remove(os.path.join(config.HLS_DIR, f))

    if os.path.exists(config.PIPE_PATH):
        os.remove(config.PIPE_PATH)
    os.mkfifo(config.PIPE_PATH)

    if os.path.exists(config.FFMPEG_LOG):
        os.remove(config.FFMPEG_LOG)

    print("🧹 Cleanup complete. Fresh HLS dir, pipe, and log file ready.")


def get_twitch_user_info(username):
    """Fetch Twitch user info, including profile picture."""
    try:
        # Placeholder: Twitch API requires OAuth. Simulated response.
        print(f"Simulating API call for {username}")
        return {
            "display_name": username.capitalize(),
            "profile_image_url": f"https://via.placeholder.com/150/0000FF/FFFFFF?text={username}",
        }
    except Exception as e:
        print(f"Error fetching Twitch user info: {e}")
        return None


def load_config():
    if not os.path.exists(config.CONFIG_FILE):
        info = get_twitch_user_info("ludwig")
        return {
            "youtube_channels": [],
            "twitch_channel": "ludwig",
            "channel_name": info["display_name"] if info else "ludwig",
            "channel_logo": info["profile_image_url"] if info else None,
            "custom_logo": False,
            "youtube_transcode": True,
            "youtube_crf": 20,
            "youtube_audio_bitrate": "192k",
        }
    with open(config.CONFIG_FILE) as f:
        cfg = json.load(f)
        # ensure defaults exist
        cfg.setdefault("youtube_channels", [])
        cfg.setdefault("twitch_channel", "ludwig")
        if "channel_name" not in cfg or not cfg.get("channel_name"):
            info = get_twitch_user_info(cfg["twitch_channel"])
            cfg["channel_name"] = (
                info["display_name"] if info else cfg["twitch_channel"]
            )
        if "channel_logo" not in cfg or not cfg.get("channel_logo"):
            info = get_twitch_user_info(cfg["twitch_channel"])
            cfg["channel_logo"] = info["profile_image_url"] if info else None
        cfg.setdefault("custom_logo", False)
        cfg.setdefault("youtube_transcode", True)
        cfg.setdefault("youtube_crf", 20)
        cfg.setdefault("youtube_audio_bitrate", "192k")
        return cfg


def save_config(cfg):
    with open(config.CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def stop_writer():
    """Kill current writer if running"""
    if state.current_writer_proc and state.current_writer_proc.poll() is None:
        state.current_writer_proc.terminate()
        try:
            state.current_writer_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            state.current_writer_proc.kill()
    state.current_writer_proc = None


def wait_for_playlist(timeout=30):
    """Wait until HLS playlist exists"""
    playlist = os.path.join(config.HLS_DIR, "stream.m3u8")
    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(playlist) and os.path.getsize(playlist) > 0:
            print("✅ HLS playlist ready:", playlist)
            return True
        time.sleep(0.5)
    print("⚠️ Timeout waiting for HLS playlist.")
    return False
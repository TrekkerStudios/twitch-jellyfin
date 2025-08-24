import os
import subprocess
import json
import time
import state
import config

def cleanup():
    """Ensure dirs exist and clear old files"""
    os.makedirs(config.HLS_DIR, exist_ok=True)
    os.makedirs(config.YOUTUBE_DIR, exist_ok=True)

    for f in os.listdir(config.HLS_DIR):
        os.remove(os.path.join(config.HLS_DIR, f))

    if os.path.exists(config.PIPE_PATH):
        os.remove(config.PIPE_PATH)
    os.mkfifo(config.PIPE_PATH)

    if os.path.exists(config.FFMPEG_LOG):
        os.remove(config.FFMPEG_LOG)

    print("🧹 Cleanup complete. Fresh HLS dir, pipe, and log file ready.")

def load_config():
    if not os.path.exists(config.CONFIG_FILE):
        return {"youtube_channels": []}
    with open(config.CONFIG_FILE) as f:
        return json.load(f)

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

def wait_for_playlist(timeout=10):
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

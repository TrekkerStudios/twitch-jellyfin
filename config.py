import os

BASE_DIR = os.path.join(os.getcwd(), "tmp")
HLS_DIR = os.path.join(BASE_DIR, "hls")
PIPE_PATH = os.path.join(BASE_DIR, "input.ts")
YOUTUBE_DIR = os.path.join(BASE_DIR, "youtube")
FFMPEG_LOG = os.path.join(BASE_DIR, "ffmpeg.log")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

CHECK_INTERVAL = 15
YOUTUBE_REFRESH = 3600  # refresh every hour

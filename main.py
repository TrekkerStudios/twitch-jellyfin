import threading
import config
from server import app
from utils import cleanup, wait_for_playlist
from streaming import start_ffmpeg, write_fallback, orchestrator
from youtube import refresh_youtube_cache

if __name__ == "__main__":
    cleanup()
    start_ffmpeg()
    write_fallback()
    wait_for_playlist()
    
    threading.Thread(target=orchestrator, daemon=True).start()
    threading.Thread(target=refresh_youtube_cache, daemon=True).start()

    print("🚀 Server running at http://localhost:3000")
    print("📺 Open http://localhost:3000/stream.m3u8 in VLC or Jellyfin")
    print("📝 FFmpeg logs →", config.FFMPEG_LOG)
    
    app.run(host="0.0.0.0", port=3000)

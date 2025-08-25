import os
import time
import socket
from flask import (
    Flask,
    send_from_directory,
    request,
    redirect,
    url_for,
    render_template,
)
from werkzeug.utils import secure_filename
import config
import state
from utils import load_config, save_config, get_twitch_user_info
from youtube import fetch_youtube_videos

app = Flask(__name__, template_folder="templates")
app.config["UPLOAD_FOLDER"] = "static/logos"


def get_local_ip():
    """Return LAN IP of this machine (not localhost)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # doesn't actually send
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


# --- Static + HLS Routes ---
@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


@app.route("/<path:filename>")
def hls_root(filename):
    if filename.endswith(".m3u8"):
        return send_from_directory(
            config.HLS_DIR,
            filename,
            mimetype="application/vnd.apple.mpegurl",
        )
    elif filename.endswith(".ts"):
        return send_from_directory(
            config.HLS_DIR,
            filename,
            mimetype="video/mp2t",
        )
    return send_from_directory(config.HLS_DIR, filename)


@app.route("/stream.m3u8")
def stream():
    return send_from_directory(
        config.HLS_DIR,
        "stream.m3u8",
        mimetype="application/vnd.apple.mpegurl",
    )


@app.route("/playlist.m3u")
def playlist():
    cfg = load_config()
    host_ip = get_local_ip()
    base_url = f"http://{host_ip}:3000"
    return f"""#EXTM3U
#EXTINF:-1,{cfg['channel_name']} Live
{base_url}/stream.m3u8
"""


@app.route("/guide.xml")
def guide():
    cfg = load_config()
    now = time.strftime("%Y%m%d%H%M%S")
    host_ip = get_local_ip()
    base_url = f"http://{host_ip}:3000"

    logo_url = cfg.get("channel_logo", "")
    if cfg.get("custom_logo"):
        # ensure absolute URL for Jellyfin
        logo_url = base_url + logo_url

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="twitch">
    <display-name>{cfg['channel_name']}</display-name>
    <icon src="{logo_url}"/>
  </channel>
  <programme start="{now}" channel="twitch">
    <title>{state.current_source.title() if state.current_source else "Unknown"} Content</title>
  </programme>
</tv>"""


@app.route("/revert_branding")
def revert_branding():
    cfg = load_config()
    info = get_twitch_user_info(cfg["twitch_channel"])
    if info:
        cfg["channel_name"] = info["display_name"]
        cfg["channel_logo"] = info["profile_image_url"]
        cfg["custom_logo"] = False
        save_config(cfg)
    return redirect("/")


@app.route("/status")
def status():
    cfg = load_config()
    return {"channel": cfg["twitch_channel"], "source": state.current_source}


@app.route("/", methods=["GET", "POST"])
def index():
    cfg = load_config()
    if request.method == "POST":
        # YouTube Channel Add
        new_yt_channel = request.form.get("yt_channel")
        if new_yt_channel and new_yt_channel not in cfg["youtube_channels"]:
            cfg["youtube_channels"].append(new_yt_channel)
            save_config(cfg)

        # Twitch Channel Change
        new_twitch_channel = request.form.get("twitch_channel")
        if new_twitch_channel and new_twitch_channel != cfg["twitch_channel"]:
            cfg["twitch_channel"] = new_twitch_channel
            info = get_twitch_user_info(new_twitch_channel)
            if info:
                cfg["channel_name"] = info["display_name"]
                cfg["channel_logo"] = info["profile_image_url"]
                cfg["custom_logo"] = False
            save_config(cfg)

        # Channel Branding Change
        new_channel_name = request.form.get("channel_name")
        if new_channel_name and new_channel_name != cfg["channel_name"]:
            cfg["channel_name"] = new_channel_name
            save_config(cfg)

        # Logo Upload
        if "channel_logo" in request.files:
            logo = request.files["channel_logo"]
            if logo.filename != "":
                filename = secure_filename(logo.filename)
                logo_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                logo.save(logo_path)
                cfg["channel_logo"] = url_for(
                    "static_files", filename=f"logos/{filename}"
                )
                cfg["custom_logo"] = True
                save_config(cfg)

        # Cookie file upload
        if "cookies" in request.files:
            cookie_file = request.files["cookies"]
            if cookie_file.filename != "":
                cookie_path = os.path.join(config.BASE_DIR, "cookies.txt")
                cookie_file.save(cookie_path)
                print("🍪 Cookies.txt uploaded successfully.")

        return redirect("/")

    yt_list = ""
    for ch in cfg.get("youtube_channels", []):
        yt_list += f"""
        <div class="flex items-center justify-between bg-gray-700 p-3 rounded-lg">
            <span class="font-medium text-white">{ch}</span>
            <a href='/remove_channel/{ch}' class="px-3 py-1 text-sm font-semibold text-white bg-red-600 rounded-md hover:bg-red-700 transition-colors">Remove</a>
        </div>
        """
    yt_list = (
        f"<div class='space-y-3'>{yt_list}</div>"
        if yt_list
        else "<p class='text-gray-400'>No channels added.</p>"
    )

    playlist_preview = (
        "".join(
            [
                f"<div class='flex items-center justify-between bg-gray-700 p-3 rounded-lg'><span class='font-medium text-white truncate'>{m['title']} ({m['duration']})</span> "
                f"<a href='{m['url']}' target='_blank' class='px-3 py-1 text-sm font-semibold text-white bg-blue-600 rounded-md hover:bg-blue-700 transition-colors'>Watch</a></div>"
                for m in state.youtube_meta
            ]
        )
        if state.youtube_meta
        else "<p class='text-gray-400'>No videos cached.</p>"
    )

    logo_url = cfg.get("channel_logo", "")

    return render_template(
        "index.html",
        cfg=cfg,
        source=state.current_source,
        yt_list=yt_list,
        playlist_preview=playlist_preview,
        logo_url=logo_url,
    )


@app.route("/remove_channel/<channel>")
def remove_channel(channel):
    cfg = load_config()
    if channel in cfg["youtube_channels"]:
        cfg["youtube_channels"].remove(channel)
        save_config(cfg)
        print(f"🗑️ Removed YouTube channel: {channel}")
    return redirect("/")


@app.route("/refresh_youtube", methods=["POST"])
def refresh_youtube():
    cfg = load_config()
    state.youtube_cache, state.youtube_meta = fetch_youtube_videos(
        cfg.get("youtube_channels", [])
    )
    print(f"🔄 Manual YouTube refresh: {len(state.youtube_cache)} videos")
    return redirect("/")


@app.route("/clear_cache", methods=["POST"])
def clear_cache():
    for f in os.listdir(config.YOUTUBE_DIR):
        try:
            os.remove(os.path.join(config.YOUTUBE_DIR, f))
        except Exception as e:
            print("⚠️ Error deleting file:", f, e)
    print("🗑️ YouTube cache cleared.")
    return redirect("/")

@app.route("/update_transcode", methods=["POST"])
def update_transcode():
    cfg = load_config()
    cfg["youtube_transcode"] = request.form.get("youtube_transcode") == "true"
    cfg["youtube_crf"] = int(request.form.get("youtube_crf", 20))
    cfg["youtube_audio_bitrate"] = request.form.get("youtube_audio_bitrate", "192k")
    save_config(cfg)
    print(f"⚙️ Updated transcoding: {cfg}")
    return redirect("/")


# --- CORS headers for Jellyfin/web clients ---
@app.after_request
def add_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response
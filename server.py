import os
import time
from flask import Flask, send_from_directory, request, redirect, url_for
from werkzeug.utils import secure_filename
import config
import state
from utils import load_config, save_config, get_twitch_user_info
from youtube import fetch_youtube_videos

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/logos'

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory('static', filename)

@app.route("/<path:filename>")
def hls_root(filename):
    return send_from_directory(config.HLS_DIR, filename)

@app.route("/stream.m3u8")
def stream():
    return send_from_directory(config.HLS_DIR, "stream.m3u8")

@app.route("/playlist.m3u")
def playlist():
    cfg = load_config()
    return f"#EXTM3U\n#EXTINF:-1,{cfg['channel_name']} Live\nhttp://localhost:3000/stream.m3u8\n"

@app.route("/guide.xml")
def guide():
    cfg = load_config()
    now = time.strftime("%Y%m%d%H%M%S")
    logo_url = cfg.get("channel_logo", '')
    if cfg.get("custom_logo"):
        logo_url = request.host_url.rstrip('/') + logo_url

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
        if 'channel_logo' in request.files:
            logo = request.files['channel_logo']
            if logo.filename != '':
                filename = secure_filename(logo.filename)
                logo_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                logo.save(logo_path)
                cfg["channel_logo"] = url_for('static_files', filename=f'logos/{filename}')
                cfg["custom_logo"] = True
                save_config(cfg)

        return redirect("/")

    yt_list = ""
    for ch in cfg.get("youtube_channels", []):
        yt_list += f"""
        <li class='list-item'>
            <span>{ch}</span>
            <a href='/remove_channel/{ch}' class='btn btn-danger'>Remove</a>
        </li>
        """
    yt_list = f"<ul class='list'>{yt_list}</ul>" if yt_list else "<p>No channels added.</p>"

    playlist_preview = "".join(
        [f"<li class='list-item'>{m['title']} ({m['duration']}) "
         f"<a href='{m['url']}' target='_blank' class='btn btn-link'>Watch</a></li>"
         for m in state.youtube_meta]
    ) if state.youtube_meta else "<p>No videos cached.</p>"

    logo_url = cfg.get("channel_logo", '')
    if cfg.get("custom_logo"):
        logo_url = url_for('static_files', filename=os.path.basename(logo_url))

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>twitch-jellyfin</title>
      <style>
        body {{
          font-family: Arial, sans-serif;
          background-color: #121212;
          color: #e0e0e0;
          margin: 0;
          padding: 0;
        }}
        .container {{
          max-width: 800px;
          margin: 40px auto;
          background: #1e1e1e;
          padding: 20px 30px;
          border-radius: 8px;
          box-shadow: 0 2px 6px rgba(0,0,0,0.5);
        }}
        h2, h3 {{ margin-top: 20px; color: #fff; }}
        .card {{ background: #2a2a2a; padding: 15px; border-radius: 6px; margin-bottom: 20px; border: 1px solid #333; }}
        .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 0.85em; background: #17a2b8; color: #fff; }}
        .btn {{ display: inline-block; padding: 6px 12px; margin: 2px; font-size: 0.9em; border-radius: 4px; text-decoration: none; cursor: pointer; }}
        .btn-primary {{ background: #007bff; color: #fff; }}
        .btn-secondary {{ background: #6c757d; color: #fff; }}
        .btn-success {{ background: #28a745; color: #fff; }}
        .btn-warning {{ background: #ffc107; color: #000; }}
        .btn-danger {{ background: #dc3545; color: #fff; }}
        .btn-link {{ background: none; color: #66b2ff; text-decoration: underline; }}
        .list {{ list-style: none; padding: 0; margin: 0; }}
        .list-item {{ display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; border-bottom: 1px solid #333; }}
        .list-item:last-child {{ border-bottom: none; }}
        form {{ margin-top: 10px; }}
        input[type="text"], input[name="yt_channel"], input[name="twitch_channel"], input[name="channel_name"] {{ padding: 6px; font-size: 0.9em; border: 1px solid #444; border-radius: 4px; width: 70%; background: #222; color: #eee; }}
        .logo-preview {{ max-width: 50px; max-height: 50px; border-radius: 50%; margin-right: 10px; }}
      </style>
    </head>
    <body>
      <div class="container">
        <h2>twitch-jellyfin</h2>
        <div class="card">
          <p><strong>Current source:</strong> <span class="badge">{state.current_source}</span></p>
          <a href='/stream.m3u8' class="btn btn-primary">Stream.m3u8</a>
          <a href='/playlist.m3u' class="btn btn-secondary">M3U Playlist</a>
          <a href='/guide.xml' class="btn btn-secondary">XMLTV Guide</a>
          <a href='/status' class="btn btn-secondary">Status JSON</a>
        </div>

        <h3>Settings</h3>
        <form method="POST" class="d-flex mt-2">
          <input name="twitch_channel" placeholder="Twitch Channel" value="{cfg['twitch_channel']}">
          <button type="submit" class="btn btn-success">Save</button>
        </form>

        <h3>Channel Branding</h3>
        <form method="POST" enctype="multipart/form-data">
            <img src="{logo_url}" class="logo-preview">
            <input type="text" name="channel_name" placeholder="Channel Name" value="{cfg['channel_name']}">
            <input type="file" name="channel_logo">
            <button type="submit" class="btn btn-success">Save Branding</button>
            <a href="/revert_branding" class="btn btn-warning">Revert to Default</a>
        </form>

        <h3>YouTube Channels</h3>
        {yt_list}
        <form method="POST" class="d-flex mt-2">
          <input name="yt_channel" placeholder="channel/UC... or @handle">
          <button type="submit" class="btn btn-success">Add</button>
        </form>
        <form method="POST" action="/refresh_youtube">
          <button type="submit" class="btn btn-warning">Force Refresh YouTube Cache</button>
        </form>
        <form method="POST" action="/clear_cache">
          <button type="submit" class="btn btn-danger">🗑️ Clear YouTube Cache</button>
        </form>

        <h3 class="mt-4">Current YouTube Playlist</h3>
        <ul class="list">
          {playlist_preview}
        </ul>
      </div>
    </body>
    </html>
    """

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
    state.youtube_cache, state.youtube_meta = fetch_youtube_videos(cfg.get("youtube_channels", []))
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

import os
import tempfile
import shutil
import uuid
import threading
from pathlib import Path
from flask import Flask, jsonify, render_template, request, send_from_directory
import yt_dlp
import certifi
import time
import logging

# =========================================================
# Configuration
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

FILE_EXPIRATION_HOURS = 3
CLEANUP_INTERVAL_SECONDS = 10 * 60  # 10 minutes

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
downloads = {}

# =========================================================
# Cookie Helper with Comprehensive Validation
# =========================================================
def get_cookie_file():
    """
    Return a path to a writable, validated cookie file.
    Copies from read-only secrets location if needed.
    """
    source_paths = [
        Path("/etc/secrets/cookies.txt"),  # Render secrets (read-only)
        BASE_DIR / "cookies.txt",           # Local development
    ]
    
    for source_path in source_paths:
        if not source_path.exists():
            continue
            
        try:
            # Read and validate the cookie file
            with open(source_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                
                if not lines:
                    logger.warning(f"[COOKIE] {source_path} is empty")
                    continue
                
                first_line = lines[0].strip()
                if not (first_line.startswith('# HTTP Cookie File') or 
                        first_line.startswith('# Netscape HTTP Cookie File')):
                    logger.warning(f"[COOKIE] {source_path} is not in Netscape format")
                    continue
                
                # Check for essential YouTube cookies
                content = ''.join(lines).lower()
                if 'youtube.com' not in content:
                    logger.warning(f"[COOKIE] {source_path} doesn't contain YouTube cookies")
                    continue
                
                # Check for essential session cookies
                has_visitor = any('visitor_info1_live' in line.lower() for line in lines)
                if not has_visitor:
                    logger.warning(f"[COOKIE] {source_path} missing VISITOR_INFO1_LIVE cookie")
                    # Continue anyway - might still work
                
                logger.info(f"[COOKIE] Successfully validated: {source_path}")
                logger.info(f"[COOKIE] Cookie count: {len([l for l in lines if l.strip() and not l.startswith('#')])}")
            
            # If on Render, copy to writable location
            if str(source_path).startswith('/etc/secrets/'):
                writable_path = DOWNLOAD_DIR / "cookies.txt"
                shutil.copy2(source_path, writable_path)
                writable_path.chmod(0o644)
                logger.info(f"[COOKIE] Copied to writable: {writable_path}")
                return str(writable_path)
            else:
                logger.info(f"[COOKIE] Using local file: {source_path}")
                return str(source_path)
                
        except Exception as e:
            logger.error(f"[COOKIE] Error processing {source_path}: {e}")
            continue
    
    logger.warning("[COOKIE] No valid cookie file found")
    return None

# =========================================================
# Cleanup Function
# =========================================================
def cleanup_old_files():
    while True:
        try:
            now = time.time()
            expiration_seconds = FILE_EXPIRATION_HOURS * 60 * 60
            
            for file_path in DOWNLOAD_DIR.iterdir():
                if not file_path.is_file():
                    continue

                # Handle cookie file separately (clean up after 1 hour)
                if file_path.name == "cookies.txt":
                    if (now - file_path.stat().st_mtime) > 3600:
                        try:
                            file_path.unlink()
                            logger.info(f"[CLEANUP] Deleted old cookie file")
                        except Exception as e:
                            logger.error(f"[CLEANUP] Failed to delete cookie file: {e}")
                    continue

                # Clean up old download files
                file_age = now - file_path.stat().st_mtime
                if file_age > expiration_seconds:
                    try:
                        file_path.unlink()
                        logger.info(f"[CLEANUP] Deleted: {file_path.name}")
                    except Exception as e:
                        logger.error(f"[CLEANUP] Failed to delete {file_path.name}: {e}")
                        
        except Exception as e:
            logger.error(f"[CLEANUP] Error: {e}")
            
        time.sleep(CLEANUP_INTERVAL_SECONDS)

# =========================================================
# Video Info Function
# =========================================================
def get_video_info(url: str):
    cookie_file = get_cookie_file()
    
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "cookiefile": cookie_file,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
                "skip": ["hls", "dash"],
            }
        }
    }
    
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)

def format_duration(seconds):
    if not seconds:
        return "Unknown"

    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

# =========================================================
# Progress Hook
# =========================================================
def progress_hook(download_id):
    def hook(data):
        download = downloads.get(download_id)
        if not download:
            return

        status = data.get("status")

        if status == "downloading":
            downloaded = data.get("downloaded_bytes", 0)
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            filename = data.get("filename")

            if filename:
                streams = download.setdefault("streams", {})
                streams[filename] = {
                    "downloaded": downloaded,
                    "total": total or 0,
                }

            # Calculate overall progress
            streams = download.get("streams", {})
            total_downloaded = sum(s.get("downloaded", 0) for s in streams.values())
            total_size = sum(s.get("total", 0) for s in streams.values())

            if total_size > 0:
                progress = min((total_downloaded / total_size * 100), 99)
            else:
                progress = download.get("progress", 0)

            # Never allow progress to go backwards
            previous_progress = download.get("progress", 0)
            progress = max(progress, previous_progress)

            downloads[download_id].update({
                "status": "downloading",
                "progress": round(progress, 1),
                "downloaded_bytes": total_downloaded,
                "total_bytes": total_size,
                "speed": data.get("speed"),
                "eta": data.get("eta"),
            })

        elif status == "finished":
            downloads[download_id].update({
                "status": "processing",
            })

    return hook

# =========================================================
# Main Download Function
# =========================================================
def perform_download(download_id, url, media_type, quality):
    try:
        output_template = str(DOWNLOAD_DIR / f"{download_id}.%(ext)s")
        
        downloads[download_id].update({
            "status": "starting",
            "progress": 0,
        })
        
        # Get validated cookie file
        cookie_file = get_cookie_file()
        logger.info(f"[DOWNLOAD] Cookie file: {cookie_file}")
        
        # Base options
        base_opts = {
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "ca_certs": certifi.where(),
            "cookiefile": cookie_file,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "progress_hooks": [progress_hook(download_id)],
            "sleep_interval": 5,
            "max_sleep_interval": 10,
            "extractor_args": {
                "youtube": {
                    "player_client": ["mweb"],  # Mobile web client
                    "player_skip": ["configs"],  # Skip configs
                    "skip": ["hls", "dash"],    # Skip problematic formats
                }
            },
            "headers": {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
        }
        
        if media_type == "audio":
            ydl_opts = {
                **base_opts,
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
        else:
            # Better format selection with fallbacks
            quality_map = {
                "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
                "720": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
                "480": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
                "360": "bestvideo[height<=360]+bestaudio/best[height<=360]/best",
            }
            
            format_spec = quality_map.get(quality, "bestvideo+bestaudio/best")
            
            ydl_opts = {
                **base_opts,
                "format": format_spec,
                "merge_output_format": "mp4",
            }
        
        logger.info(f"[DOWNLOAD] Using format: {ydl_opts.get('format', 'default')}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            downloads[download_id].update({
                "status": "processing",
                "progress": 99,
            })
            
            filename = ydl.prepare_filename(info)
        
        # Determine final filename
        if media_type == "audio":
            filename = str(Path(filename).with_suffix(".mp3"))
        else:
            filename = str(Path(filename).with_suffix(".mp4"))
        
        file_path = Path(filename)
        if not file_path.exists():
            raise Exception("Downloaded file was not found.")
        
        downloads[download_id].update({
            "status": "completed",
            "progress": 100,
            "filename": file_path.name,
        })
        
        logger.info(f"[DOWNLOAD] Completed: {file_path.name}")
        
    except Exception as e:
        logger.error(f"[DOWNLOAD ERROR] {str(e)}")
        downloads[download_id].update({
            "status": "error",
            "error": str(e),
        })

# =========================================================
# Routes
# =========================================================

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/api/info")
def video_info():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({
            "success": False,
            "error": "YouTube URL is required."
        }), 400

    try:
        info = get_video_info(url)
        return jsonify({
            "success": True,
            "video": {
                "id": info.get("id"),
                "title": info.get("title"),
                "description": info.get("description"),
                "thumbnail": info.get("thumbnail"),
                "channel": info.get("channel"),
                "channel_id": info.get("channel_id"),
                "duration": info.get("duration"),
                "duration_formatted": format_duration(info.get("duration")),
                "view_count": info.get("view_count"),
                "webpage_url": info.get("webpage_url"),
            }
        })
    except Exception as e:
        logger.error(f"[API ERROR] {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

@app.post("/api/download")
def download_video():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    media_type = data.get("type", "video")
    quality = data.get("quality", "720")

    if not url:
        return jsonify({
            "success": False,
            "error": "YouTube URL is required."
        }), 400

    if media_type not in ["video", "audio"]:
        return jsonify({
            "success": False,
            "error": "Invalid media type."
        }), 400

    download_id = uuid.uuid4().hex
    downloads[download_id] = {
        "status": "starting",
        "progress": 0,
        "filename": None,
        "error": None,
        "streams": {},
    }

    thread = threading.Thread(
        target=perform_download,
        args=(download_id, url, media_type, quality),
        daemon=True,
    )
    thread.start()

    return jsonify({
        "success": True,
        "download_id": download_id,
    })

@app.get("/download/<filename>")
def download_file(filename):
    return send_from_directory(
        DOWNLOAD_DIR,
        filename,
        as_attachment=True
    )

@app.get("/api/download/<download_id>/status")
def download_status(download_id):
    download = downloads.get(download_id)
    if not download:
        return jsonify({
            "success": False,
            "error": "Download not found."
        }), 404

    return jsonify({
        "success": True,
        "download": download,
    })

# =========================================================
# Debug Endpoint - Check Cookie Status
# =========================================================
@app.get("/api/debug/cookie")
def debug_cookie():
    """Debug endpoint to check cookie file status."""
    cookie_file = get_cookie_file()
    if not cookie_file:
        return jsonify({
            "success": False,
            "error": "No cookie file found"
        }), 404
    
    try:
        with open(cookie_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            return jsonify({
                "success": True,
                "file_path": cookie_file,
                "total_lines": len(lines),
                "format_valid": any(lines[0].strip().startswith('#') for lines in lines if lines),
                "has_youtube_cookies": any('youtube.com' in line.lower() for line in lines),
                "has_visitor_cookie": any('visitor_info1_live' in line.lower() for line in lines),
                "preview": lines[:5] if len(lines) >= 5 else lines
            })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# =========================================================
# Run
# =========================================================

if __name__ == "__main__":
    # Start cleanup thread
    cleanup_thread = threading.Thread(
        target=cleanup_old_files,
        daemon=True,
    )
    cleanup_thread.start()
    
    logger.info("Starting Flask application...")
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
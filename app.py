import os
import uuid
import threading

from pathlib import Path

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_from_directory,
    Response,
    stream_with_context,
)

import yt_dlp
import certifi
import time

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"

DOWNLOAD_DIR.mkdir(exist_ok=True)

# =========================================================
# Download Cleanup Configuration
# =========================================================

FILE_EXPIRATION_HOURS = 3

CLEANUP_INTERVAL_SECONDS = 10 * 60  # 10 minutes

app = Flask(__name__)

downloads = {}

# =========================================================
# Helpers
# =========================================================

def cleanup_old_files():

    while True:

        try:

            now = time.time()

            expiration_seconds = (
                FILE_EXPIRATION_HOURS * 60 * 60
            )

            for file_path in DOWNLOAD_DIR.iterdir():

                if not file_path.is_file():
                    continue

                file_age = (
                    now - file_path.stat().st_mtime
                )

                if file_age > expiration_seconds:

                    try:

                        file_path.unlink()

                        print(
                            f"[CLEANUP] Deleted: "
                            f"{file_path.name}"
                        )

                    except Exception as e:

                        print(
                            f"[CLEANUP] Failed to delete "
                            f"{file_path.name}: {e}"
                        )

        except Exception as e:

            print(
                f"[CLEANUP] Error: {e}"
            )

        time.sleep(
            CLEANUP_INTERVAL_SECONDS
        )

def get_video_info(url: str):
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
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

def progress_hook(download_id):

    def hook(data):

        download = downloads.get(download_id)

        if not download:
            return

        status = data.get("status")

        # =====================================================
        # Downloading
        # =====================================================

        if status == "downloading":

            downloaded = data.get(
                "downloaded_bytes",
                0
            )

            total = (
                data.get("total_bytes")
                or data.get("total_bytes_estimate")
            )

            filename = data.get(
                "filename"
            )

            # ---------------------------------------------
            # Each downloaded file/stream gets its own ID
            # ---------------------------------------------

            if filename:

                streams = download.setdefault(
                    "streams",
                    {}
                )

                streams[filename] = {
                    "downloaded": downloaded,
                    "total": total or 0,
                }

            # ---------------------------------------------
            # Calculate overall progress
            # ---------------------------------------------

            streams = download.get(
                "streams",
                {}
            )

            total_downloaded = 0
            total_size = 0

            for stream in streams.values():

                total_downloaded += (
                    stream.get(
                        "downloaded",
                        0
                    )
                )

                total_size += (
                    stream.get(
                        "total",
                        0
                    )
                )

            if total_size > 0:

                progress = (
                    total_downloaded
                    / total_size
                    * 100
                )

                progress = min(
                    progress,
                    99
                )

            else:

                progress = download.get(
                    "progress",
                    0
                )

            # ---------------------------------------------
            # Never allow progress to go backwards
            # ---------------------------------------------

            previous_progress = download.get(
                "progress",
                0
            )

            progress = max(
                progress,
                previous_progress
            )

            downloads[download_id].update({

                "status": "downloading",

                "progress": round(
                    progress,
                    1
                ),

                "downloaded_bytes":
                    total_downloaded,

                "total_bytes":
                    total_size,

                "speed":
                    data.get("speed"),

                "eta":
                    data.get("eta"),

            })


        # =====================================================
        # One stream/file finished
        # =====================================================

        elif status == "finished":

            # Do NOT set progress to 100 here.
            #
            # Another stream may still need to download.
            #
            # yt-dlp may call this multiple times.

            downloads[download_id].update({

                "status": "processing",

            })

    return hook

def perform_download(
    download_id,
    url,
    media_type,
    quality
):

    try:

        output_template = str(
            DOWNLOAD_DIR / f"{download_id}.%(ext)s"
        )

        # =====================================================
        # Initial state
        # =====================================================

        downloads[download_id].update({
            "status": "starting",
            "progress": 0,
        })

        if media_type == "audio":

            ydl_opts = {
                "format": "bestaudio/best",

                "outtmpl": output_template,

                "noplaylist": True,

                "quiet": True,

                "no_warnings": True,

                "ca_certs": certifi.where(),

                "progress_hooks": [
                    progress_hook(download_id)
                ],

                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }

        else:

            ydl_opts = {
                "format": (
                    f"bestvideo[height<={quality}]"
                    f"+bestaudio/"
                    f"best[height<={quality}]"
                ),

                "outtmpl": output_template,

                "merge_output_format": "mp4",

                "noplaylist": True,

                "quiet": True,

                "no_warnings": True,

                "ca_certs": certifi.where(),

                "progress_hooks": [
                    progress_hook(download_id)
                ],
            }


        with yt_dlp.YoutubeDL(ydl_opts) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            # =============================================
            # Download itself is finished.
            # FFmpeg/post-processing may still be running.
            # =============================================

            downloads[download_id].update({
                "status": "processing",
                "progress": 99,
            })

            filename = ydl.prepare_filename(info)


        # -----------------------------------------
        # Determine final filename
        # -----------------------------------------

        if media_type == "audio":

            filename = str(
                Path(filename).with_suffix(".mp3")
            )

        else:

            filename = str(
                Path(filename).with_suffix(".mp4")
            )


        file_path = Path(filename)


        if not file_path.exists():

            raise Exception(
                "Downloaded file was not found."
            )


        downloads[download_id].update({
            "status": "completed",
            "progress": 100,
            "filename": file_path.name,
        })


    except Exception as e:

        downloads[download_id].update({
            "status": "error",
            "error": str(e),
        })

# =========================================================
# Pages
# =========================================================

@app.get("/")
def index():
    return render_template("index.html")


# =========================================================
# API: Get Video Information
# =========================================================

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
                "duration_formatted": format_duration(
                    info.get("duration")
                ),
                "view_count": info.get("view_count"),
                "webpage_url": info.get("webpage_url"),
            }
        })

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e)
        }), 400


# =========================================================
# API: Download
# =========================================================

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
        args=(
            download_id,
            url,
            media_type,
            quality,
        ),
        daemon=True,
    )

    thread.start()

    return jsonify({
        "success": True,
        "download_id": download_id,
    })

# =========================================================
# Download File
# =========================================================

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
# Run
# =========================================================

cleanup_thread = threading.Thread(
    target=cleanup_old_files,
    daemon=True,
)

cleanup_thread.start()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
import os
import time
import uuid
import threading
import logging
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory
import yt_dlp
import certifi


# =========================================================
# Configuration
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Render Secret File
RENDER_COOKIE_FILE = Path("/etc/secrets/cookies.txt")

# Local development fallback
LOCAL_COOKIE_FILE = BASE_DIR / "cookies.txt"

FILE_EXPIRATION_HOURS = 3
CLEANUP_INTERVAL_SECONDS = 10 * 60  # 10 minutes


# =========================================================
# Logging
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger(__name__)


# =========================================================
# Flask
# =========================================================

app = Flask(__name__)

downloads = {}


# =========================================================
# Cookie Helper
# =========================================================

def get_cookie_file():
    """
    Find the YouTube cookies file.

    Priority:
    1. Render Secret File: /etc/secrets/cookies.txt
    2. Local development: ./cookies.txt
    """

    candidates = [
        RENDER_COOKIE_FILE,
        LOCAL_COOKIE_FILE,
    ]

    for path in candidates:

        if not path.exists():
            logger.warning(f"[COOKIE] Not found: {path}")
            continue

        if not path.is_file():
            logger.warning(f"[COOKIE] Not a file: {path}")
            continue

        try:
            size = path.stat().st_size

            if size == 0:
                logger.warning(f"[COOKIE] File is empty: {path}")
                continue

            # Read only enough to validate the file.
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            if not lines:
                logger.warning(f"[COOKIE] No lines found: {path}")
                continue

            # Netscape cookie files normally start with this.
            first_line = lines[0].strip()

            valid_header = (
                first_line.startswith("# Netscape HTTP Cookie File")
                or first_line.startswith("# HTTP Cookie File")
            )

            if not valid_header:
                logger.warning(
                    f"[COOKIE] Unexpected cookie format: {path}"
                )

            # Check YouTube domains.
            youtube_cookie_count = sum(
                1
                for line in lines
                if line.strip()
                and not line.startswith("#")
                and "youtube.com" in line.lower()
            )

            logger.info(
                f"[COOKIE] Using: {path}"
            )

            logger.info(
                f"[COOKIE] Size: {size} bytes"
            )

            logger.info(
                f"[COOKIE] YouTube cookie entries: "
                f"{youtube_cookie_count}"
            )

            return str(path)

        except Exception as e:
            logger.exception(
                f"[COOKIE] Failed reading {path}: {e}"
            )

    logger.warning(
        "[COOKIE] No usable cookie file found."
    )

    return None


# =========================================================
# yt-dlp Options
# =========================================================

def get_common_ydl_options(download_id=None):
    """
    Common yt-dlp configuration.
    """

    cookie_file = get_cookie_file()

    options = {
        "noplaylist": True,

        # Don't print huge yt-dlp output.
        "quiet": True,
        "no_warnings": False,

        # SSL certificates.
        "ca_certs": certifi.where(),

        # Modern browser user agent.
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },

        # Avoid aggressive request rate.
        "sleep_interval": 1,
        "max_sleep_interval": 3,
    }

    # Only add cookiefile when it actually exists.
    if cookie_file:
        options["cookiefile"] = cookie_file

    if download_id:
        options["progress_hooks"] = [
            progress_hook(download_id)
        ]

    return options


# =========================================================
# Video Info
# =========================================================

def get_video_info(url: str):

    options = get_common_ydl_options()

    # Info extraction doesn't need a download.
    options.update({
        "skip_download": True,
    })

    logger.info(
        f"[INFO] Extracting video information: {url}"
    )

    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(
            url,
            download=False
        )


# =========================================================
# Duration Formatter
# =========================================================

def format_duration(seconds):

    if not seconds:
        return "Unknown"

    seconds = int(seconds)

    hours, remainder = divmod(
        seconds,
        3600
    )

    minutes, seconds = divmod(
        remainder,
        60
    )

    if hours:
        return (
            f"{hours}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    return (
        f"{minutes}:"
        f"{seconds:02d}"
    )


# =========================================================
# Progress Hook
# =========================================================

def progress_hook(download_id):

    def hook(data):

        download = downloads.get(download_id)

        if not download:
            return

        status = data.get("status")

        # -------------------------------------------------
        # Downloading
        # -------------------------------------------------

        if status == "downloading":

            downloaded = data.get(
                "downloaded_bytes",
                0
            )

            total = (
                data.get("total_bytes")
                or data.get("total_bytes_estimate")
                or 0
            )

            filename = data.get("filename")

            if filename:

                streams = download.setdefault(
                    "streams",
                    {}
                )

                streams[filename] = {
                    "downloaded": downloaded,
                    "total": total,
                }

            streams = download.get(
                "streams",
                {}
            )

            total_downloaded = sum(
                stream.get("downloaded", 0)
                for stream in streams.values()
            )

            total_size = sum(
                stream.get("total", 0)
                for stream in streams.values()
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

            # Never move backwards.
            previous_progress = download.get(
                "progress",
                0
            )

            progress = max(
                progress,
                previous_progress
            )

            download.update({
                "status": "downloading",
                "progress": round(
                    progress,
                    1
                ),
                "downloaded_bytes": total_downloaded,
                "total_bytes": total_size,
                "speed": data.get("speed"),
                "eta": data.get("eta"),
            })

        # -------------------------------------------------
        # Finished individual stream
        # -------------------------------------------------

        elif status == "finished":

            download.update({
                "status": "processing",
            })

    return hook


# =========================================================
# Main Download
# =========================================================

def perform_download(
    download_id,
    url,
    media_type,
    quality
):

    try:

        output_template = str(
            DOWNLOAD_DIR
            / f"{download_id}.%(ext)s"
        )

        downloads[download_id].update({
            "status": "starting",
            "progress": 0,
        })

        cookie_file = get_cookie_file()

        logger.info(
            f"[DOWNLOAD] ID: {download_id}"
        )

        logger.info(
            f"[DOWNLOAD] URL: {url}"
        )

        logger.info(
            f"[DOWNLOAD] Type: {media_type}"
        )

        logger.info(
            f"[DOWNLOAD] Quality: {quality}"
        )

        logger.info(
            f"[DOWNLOAD] Cookie file: "
            f"{cookie_file or 'NONE'}"
        )

        # -------------------------------------------------
        # Base options
        # -------------------------------------------------

        base_opts = get_common_ydl_options(
            download_id
        )

        base_opts.update({
            "outtmpl": output_template,

            # Important for temporary downloader files.
            "noplaylist": True,

            # Continue / retry behavior.
            "retries": 3,
            "fragment_retries": 3,

            # Don't keep partial files.
            "keepvideo": False,
        })

        # -------------------------------------------------
        # Audio
        # -------------------------------------------------

        if media_type == "audio":

            ydl_opts = {
                **base_opts,

                "format": (
                    "bestaudio/best"
                ),

                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }

        # -------------------------------------------------
        # Video
        # -------------------------------------------------

        else:

            quality_map = {

                "1080": (
                    "bestvideo[height<=1080]"
                    "+bestaudio/"
                    "best[height<=1080]/"
                    "best"
                ),

                "720": (
                    "bestvideo[height<=720]"
                    "+bestaudio/"
                    "best[height<=720]/"
                    "best"
                ),

                "480": (
                    "bestvideo[height<=480]"
                    "+bestaudio/"
                    "best[height<=480]/"
                    "best"
                ),

                "360": (
                    "bestvideo[height<=360]"
                    "+bestaudio/"
                    "best[height<=360]/"
                    "best"
                ),
            }

            format_spec = quality_map.get(
                quality,
                quality_map["720"]
            )

            ydl_opts = {
                **base_opts,

                "format": format_spec,

                "merge_output_format": "mp4",
            }

        logger.info(
            "[DOWNLOAD] Format: "
            f"{ydl_opts.get('format')}"
        )

        # -------------------------------------------------
        # Download
        # -------------------------------------------------

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            downloads[download_id].update({
                "status": "processing",
                "progress": 99,
            })

            filename = ydl.prepare_filename(
                info
            )

        # -------------------------------------------------
        # Determine final extension
        # -------------------------------------------------

        if media_type == "audio":

            filename = str(
                Path(filename).with_suffix(
                    ".mp3"
                )
            )

        else:

            filename = str(
                Path(filename).with_suffix(
                    ".mp4"
                )
            )

        file_path = Path(filename)

        # -------------------------------------------------
        # Verify file
        # -------------------------------------------------

        if not file_path.exists():

            raise Exception(
                "Downloaded file was not found."
            )

        # -------------------------------------------------
        # Completed
        # -------------------------------------------------

        downloads[download_id].update({

            "status": "completed",

            "progress": 100,

            "filename": file_path.name,

        })

        logger.info(
            f"[DOWNLOAD] Completed: "
            f"{file_path.name}"
        )

    except Exception as e:

        logger.exception(
            f"[DOWNLOAD ERROR] {e}"
        )

        downloads[download_id].update({

            "status": "error",

            "error": str(e),

        })


# =========================================================
# Cleanup
# =========================================================

def cleanup_old_files():

    while True:

        try:

            now = time.time()

            expiration_seconds = (
                FILE_EXPIRATION_HOURS
                * 60
                * 60
            )

            if not DOWNLOAD_DIR.exists():
                time.sleep(
                    CLEANUP_INTERVAL_SECONDS
                )
                continue

            for file_path in DOWNLOAD_DIR.iterdir():

                if not file_path.is_file():
                    continue

                # Never delete anything that isn't
                # a generated download.
                if file_path.name == "cookies.txt":
                    continue

                file_age = (
                    now
                    - file_path.stat().st_mtime
                )

                if file_age > expiration_seconds:

                    try:

                        file_path.unlink()

                        logger.info(
                            "[CLEANUP] Deleted: "
                            f"{file_path.name}"
                        )

                    except Exception as e:

                        logger.error(
                            "[CLEANUP] Failed to delete "
                            f"{file_path.name}: {e}"
                        )

        except Exception as e:

            logger.exception(
                f"[CLEANUP] Error: {e}"
            )

        time.sleep(
            CLEANUP_INTERVAL_SECONDS
        )


# =========================================================
# Routes
# =========================================================

@app.get("/")
def index():

    return render_template(
        "index.html"
    )


# =========================================================
# Video Info API
# =========================================================

@app.post("/api/info")
def video_info():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    url = data.get(
        "url",
        ""
    ).strip()

    if not url:

        return jsonify({
            "success": False,
            "error": (
                "YouTube URL is required."
            )
        }), 400

    try:

        info = get_video_info(url)

        return jsonify({

            "success": True,

            "video": {

                "id": info.get("id"),

                "title": info.get(
                    "title"
                ),

                "description": info.get(
                    "description"
                ),

                "thumbnail": info.get(
                    "thumbnail"
                ),

                "channel": info.get(
                    "channel"
                ),

                "channel_id": info.get(
                    "channel_id"
                ),

                "duration": info.get(
                    "duration"
                ),

                "duration_formatted":
                    format_duration(
                        info.get(
                            "duration"
                        )
                    ),

                "view_count": info.get(
                    "view_count"
                ),

                "webpage_url":
                    info.get(
                        "webpage_url"
                    ),
            }
        })

    except Exception as e:

        logger.exception(
            f"[API INFO ERROR] {e}"
        )

        return jsonify({

            "success": False,

            "error": str(e)

        }), 400


# =========================================================
# Download API
# =========================================================

@app.post("/api/download")
def download_video():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    url = data.get(
        "url",
        ""
    ).strip()

    media_type = data.get(
        "type",
        "video"
    )

    quality = data.get(
        "quality",
        "720"
    )

    if not url:

        return jsonify({

            "success": False,

            "error": (
                "YouTube URL is required."
            )

        }), 400

    if media_type not in [
        "video",
        "audio"
    ]:

        return jsonify({

            "success": False,

            "error": (
                "Invalid media type."
            )

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

        as_attachment=True,

    )


# =========================================================
# Download Status
# =========================================================

@app.get(
    "/api/download/<download_id>/status"
)
def download_status(download_id):

    download = downloads.get(
        download_id
    )

    if not download:

        return jsonify({

            "success": False,

            "error": (
                "Download not found."
            )

        }), 404

    return jsonify({

        "success": True,

        "download": download,

    })


# =========================================================
# Cookie Debug
# =========================================================

@app.get("/api/debug/cookie")
def debug_cookie():

    cookie_file = get_cookie_file()

    if not cookie_file:

        return jsonify({

            "success": False,

            "error": (
                "No cookie file found."
            )

        }), 404

    try:

        path = Path(cookie_file)

        size = path.stat().st_size

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            lines = f.readlines()

        cookie_lines = [
            line
            for line in lines
            if line.strip()
            and not line.startswith("#")
        ]

        youtube_lines = [
            line
            for line in cookie_lines
            if "youtube.com" in line.lower()
        ]

        return jsonify({

            "success": True,

            "file_exists": True,

            "file_size": size,

            "total_lines": len(lines),

            "cookie_entries":
                len(cookie_lines),

            "youtube_cookie_entries":
                len(youtube_lines),

            "format_valid":
                (
                    bool(lines)
                    and (
                        lines[0].strip().startswith(
                            "# Netscape HTTP Cookie File"
                        )
                        or
                        lines[0].strip().startswith(
                            "# HTTP Cookie File"
                        )
                    )
                ),

            # DO NOT expose cookie contents.
            "cookie_preview": None,

        })

    except Exception as e:

        logger.exception(
            f"[COOKIE DEBUG ERROR] {e}"
        )

        return jsonify({

            "success": False,

            "error": str(e)

        }), 500


# =========================================================
# Application Startup
# =========================================================

if __name__ == "__main__":

    cleanup_thread = threading.Thread(

        target=cleanup_old_files,

        daemon=True,

    )

    cleanup_thread.start()

    logger.info(
        "Starting Flask application..."
    )

    logger.info(
        f"Download directory: "
        f"{DOWNLOAD_DIR}"
    )

    logger.info(
        f"Render cookie exists: "
        f"{RENDER_COOKIE_FILE.exists()}"
    )

    app.run(

        host="0.0.0.0",

        port=int(
            os.environ.get(
                "PORT",
                5000
            )
        ),

        debug=False,

    )
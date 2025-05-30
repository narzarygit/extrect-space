from flask import Flask, request, send_file, jsonify, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp
import os
import uuid
import logging
import shutil
import time
import json

app = Flask(__name__)

# Logging setup
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Rate limiting setup
LIMITER_STORAGE_FOLDER = "limiter_storage"
if not os.path.exists(LIMITER_STORAGE_FOLDER):
    os.makedirs(LIMITER_STORAGE_FOLDER)

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    storage_uri="memory://",
    storage_options={"path": LIMITER_STORAGE_FOLDER}
)

DOWNLOAD_FOLDER = "downloads"
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

download_progress = {}

def cleanup_file(filepath):
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception as e:
        logger.error(f"Error deleting file: {e}")

def cleanup_downloads_folder():
    while True:
        try:
            folder = DOWNLOAD_FOLDER
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                if os.path.isfile(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            logger.debug("Downloads folder cleaned")
        except Exception as e:
            logger.error(f"Failed to clean downloads: {str(e)}")
        time.sleep(600)

import threading
threading.Thread(target=cleanup_downloads_folder, daemon=True).start()

@app.route('/')
def serve_index():
    logger.debug("Serving index.html")
    index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
    logger.debug(f"Looking for index.html at: {index_path}")
    if not os.path.exists(index_path):
        logger.error(f"index.html not found at: {index_path}")
        return "File not found", 404
    return send_file(index_path)

@app.route("/api/video-details")
@limiter.limit("5 per minute")
def video_details():
    url = request.args.get("url")
    logger.debug(f"Received video details request with URL: {url}")
    if not url:
        logger.error("No URL provided")
        return jsonify({"error": "No URL provided"}), 400

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'format': 'bestvideo+bestaudio/best',
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])

            sizes = {
                'best_size': None,
                '1080p_size': None,
                '720p_size': None,
                '480p_size': None,
                '360p_size': None,
                'mp3_size': None,
            }

            best_height = 0
            for fmt in formats:
                height = fmt.get('height')
                filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                if filesize:
                    filesize_mb = round(filesize / (1024 * 1024), 2)
                    if height and isinstance(height, int):
                        if height <= 360 and not sizes['360p_size']:
                            sizes['360p_size'] = filesize_mb
                        elif height <= 480 and not sizes['480p_size']:
                            sizes['480p_size'] = filesize_mb
                        elif height <= 720 and not sizes['720p_size']:
                            sizes['720p_size'] = filesize_mb
                        elif height <= 1080 and not sizes['1080p_size']:
                            sizes['1080p_size'] = filesize_mb
                        if height > best_height:
                            sizes['best_size'] = filesize_mb
                            best_height = height
                if fmt.get('format_id') == 'bestaudio' or fmt.get('abr'):
                    audio_filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                    if audio_filesize and not sizes['mp3_size']:
                        sizes['mp3_size'] = round(audio_filesize / (1024 * 1024), 2)

            if not sizes['mp3_size'] and info.get('duration'):
                duration = info['duration']
                bitrate = 192
                mp3_size_bits = duration * bitrate * 1000
                sizes['mp3_size'] = round(mp3_size_bits / (8 * 1024 * 1024), 2)

        return jsonify(sizes)

    except Exception as e:
        logger.error(f"Failed to fetch video details: {str(e)}")
        return jsonify({"error": f"Failed to fetch video details: {str(e)}"}), 500

@app.route("/api/start-download")
@limiter.limit("3 per minute")
def start_download():
    url = request.args.get("url")
    format = request.args.get("format", "bestvideo+bestaudio/best")
    type = request.args.get("type", "video")
    download_id = str(uuid.uuid4())
    
    logger.debug(f"Starting download with ID: {download_id}, Type: {type}, URL: {url}, Format: {format}")
    
    download_progress[download_id] = {"progress": 0, "status": "starting"}

    def download_task():
        output_template = os.path.join(DOWNLOAD_FOLDER, f"{download_id}.%(ext)s")
        ydl_opts = {
            'format': format,
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [lambda d: update_progress(d, download_id)],
        }

        if type == "audio":
            ydl_opts['merge_output_format'] = None
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        else:
            ydl_opts['merge_output_format'] = 'mp4'

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # Get the actual downloaded file path after postprocessing
                downloaded_file = ydl.prepare_filename(info)
                if type == "audio":
                    # After FFmpegExtractAudio, the extension changes to .mp3
                    downloaded_file = downloaded_file.rsplit('.', 1)[0] + '.mp3'
                else:
                    downloaded_file = downloaded_file.rsplit('.', 1)[0] + '.mp4'
                logger.debug(f"Downloaded file path: {downloaded_file}")
            download_progress[download_id]["status"] = "completed"
            download_progress[download_id]["file_path"] = downloaded_file
        except Exception as e:
            logger.error(f"Download failed: {str(e)}")
            download_progress[download_id]["status"] = "failed"
            download_progress[download_id]["error"] = str(e)

    threading.Thread(target=download_task, daemon=True).start()
    return jsonify({"download_id": download_id})

def update_progress(d, download_id):
    if d['status'] == 'downloading':
        if 'total_bytes' in d and 'downloaded_bytes' in d:
            percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
            download_progress[download_id]["progress"] = round(percent, 2)
    elif d['status'] == 'finished':
        download_progress[download_id]["progress"] = 100

@app.route("/api/download-progress/<download_id>")
def download_progress_stream(download_id):
    if download_id not in download_progress:
        return jsonify({"error": "Invalid download ID"}), 404

    def generate():
        while True:
            progress_data = download_progress.get(download_id, {})
            yield f"data: {json.dumps(progress_data)}\n\n"
            if progress_data.get("status") in ["completed", "failed"]:
                break
            time.sleep(1)
    return Response(generate(), mimetype='text/event-stream')

@app.route("/api/get-file/<download_id>")
def get_file(download_id):
    progress_data = download_progress.get(download_id, {})
    if progress_data.get("status") != "completed":
        return jsonify({"error": "Download not completed"}), 400

    file_path = progress_data.get("file_path")
    if not file_path or not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return jsonify({"error": "File not found"}), 404

    response = send_file(file_path, as_attachment=True)
    @response.call_on_close
    def remove_file():
        cleanup_file(file_path)
        download_progress.pop(download_id, None)
    return response

from yt_dlp import YoutubeDL

ydl_opts = {
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'cookiefile': 'www.youtube.com_cookies.txt',
    'format': 'bestvideo+bestaudio/best',
    'noplaylist': True,
}

def get_video_details(url):
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'formats': info.get('formats', []),
            }
    except Exception as e:
        return f"Failed to fetch video details: {str(e)}"
if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
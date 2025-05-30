from flask import Flask, request, send_file, jsonify, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import uuid
import logging
import shutil
import time
import json
import requests
from urllib.parse import urlparse
import isodate  # For parsing YouTube API duration
from pytube import YouTube

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

# YouTube API Key (replace with your own API key)
YOUTUBE_API_KEY = "AIzaSyBkoOHQaQ_HgC07Xfl15bLlxNLF4PdQz5A"  # Replace with your API key from Google Cloud Console

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

    # Check if the URL is from YouTube
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()
    if not (domain in ['www.youtube.com', 'youtu.be', 'youtube.com']):
        logger.warning(f"Unsupported platform for URL: {url}")
        return jsonify({"error": "Only YouTube URLs are supported at the moment. Support for Instagram, Facebook, and Twitter coming soon!"}), 400

    # Extract video ID from URL
    if domain == 'youtu.be':
        video_id = parsed_url.path.lstrip('/')
    else:
        query = parsed_url.query
        video_id = None
        for param in query.split('&'):
            if param.startswith('v='):
                video_id = param.split('=')[1]
                break
        if not video_id:
            logger.error("Could not extract video ID from URL")
            return jsonify({"error": "Invalid YouTube URL"}), 400

    # Fetch video details using YouTube API
    api_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,contentDetails&id={video_id}&key={YOUTUBE_API_KEY}"
    try:
        response = requests.get(api_url)
        data = response.json()
        if "items" not in data or not data["items"]:
            return jsonify({"error": "Video not found"}), 404

        video_info = data["items"][0]
        title = video_info["snippet"]["title"]
        thumbnail = video_info["snippet"]["thumbnails"]["high"]["url"]
        duration = isodate.parse_duration(video_info["contentDetails"]["duration"]).total_seconds()

        # Estimate video sizes based on duration
        bitrate_1080p = 8000000  # 8 Mbps for 1080p
        bitrate_720p = 4000000   # 4 Mbps for 720p
        bitrate_480p = 2000000   # 2 Mbps for 480p
        bitrate_360p = 1000000   # 1 Mbps for 360p
        bitrate_mp3 = 192000     # 192 kbps for MP3

        # Calculate sizes in MB
        sizes = {
            'best_size': round((bitrate_1080p * duration) / (8 * 1024 * 1024), 2),  # 1080p as best
            '1080p_size': round((bitrate_1080p * duration) / (8 * 1024 * 1024), 2),
            '720p_size': round((bitrate_720p * duration) / (8 * 1024 * 1024), 2),
            '480p_size': round((bitrate_480p * duration) / (8 * 1024 * 1024), 2),
            '360p_size': round((bitrate_360p * duration) / (8 * 1024 * 1024), 2),
            'mp3_size': round((bitrate_mp3 * duration) / (8 * 1024 * 1024), 2),
        }

        response = {
            "title": title,
            "thumbnail": thumbnail,
            "sizes": sizes
        }
        return jsonify(response)

    except Exception as e:
        logger.error(f"Failed to fetch video details with YouTube API: {str(e)}")
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
        try:
            yt = YouTube(url, on_progress_callback=lambda stream, chunk, bytes_remaining: update_progress_pytube(download_id, stream, bytes_remaining))
            
            if type == "audio":
                # Download audio stream
                stream = yt.streams.filter(only_audio=True).first()
                if not stream:
                    raise Exception("No audio stream available")
                output_file = stream.download(output_path=DOWNLOAD_FOLDER, filename=f"{download_id}.mp3")
            else:
                # Map the format to pytube resolution
                resolution_map = {
                    'bestvideo[height<=1080]+bestaudio/best[height<=1080]': '1080p',
                    'bestvideo[height<=720]+bestaudio/best[height<=720]': '720p',
                    'bestvideo[height<=480]+bestaudio/best[height<=480]': '480p',
                    'bestvideo[height<=360]+bestaudio/best[height<=360]': '360p',
                    'bestvideo+bestaudio/best': None,  # Best available
                }
                resolution = resolution_map.get(format)
                
                if resolution:
                    stream = yt.streams.filter(progressive=True, resolution=resolution).first()
                else:
                    stream = yt.streams.filter(progressive=True).order_by('resolution').desc().first()
                
                if not stream:
                    raise Exception(f"No video stream available for resolution: {resolution}")
                output_file = stream.download(output_path=DOWNLOAD_FOLDER, filename=f"{download_id}.mp4")

            logger.debug(f"Downloaded file path: {output_file}")
            download_progress[download_id]["status"] = "completed"
            download_progress[download_id]["file_path"] = output_file
        except Exception as e:
            logger.error(f"Download failed: {str(e)}")
            download_progress[download_id]["status"] = "failed"
            download_progress[download_id]["error"] = str(e)

    threading.Thread(target=download_task, daemon=True).start()
    return jsonify({"download_id": download_id})

def update_progress_pytube(download_id, stream, bytes_remaining):
    total_size = stream.filesize
    bytes_downloaded = total_size - bytes_remaining
    percent = (bytes_downloaded / total_size) * 100
    download_progress[download_id]["progress"] = round(percent, 2)
    if percent >= 100:
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

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
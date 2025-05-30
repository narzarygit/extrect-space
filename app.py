from flask import Flask, request, send_file, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import logging
import requests
from urllib.parse import urlparse
import isodate  # For parsing YouTube API duration

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

# YouTube API Key (replace with your own API key)
YOUTUBE_API_KEY = "AIzaSyBkoOHQaQ_HgC07Xfl15bLlxNLF4PdQz5A"  # Replace with your API key from Google Cloud Console

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

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
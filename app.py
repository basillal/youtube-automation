from flask import Flask, request, render_template_string
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
import yt_dlp
import os
from datetime import datetime
import pytz

app = Flask(__name__)

# YouTube API scopes
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

# HTML template for the web form
HTML_TEMPLATE = """
<!doctype html>
<title>YouTube Uploader</title>
<h1>YouTube Uploader</h1>
<form method="post">
    YouTube URL: <input name="url" required><br><br>
    Schedule upload? (YYYY-MM-DD HH:MM, leave blank for immediate): <input name="schedule"><br><br>
    <input type="submit" value="Upload">
</form>
{% if message %}
<hr>
<p>{{ message }}</p>
{% endif %}
"""

# Authenticate with YouTube API
def get_authenticated_service():
    credentials = Credentials.from_authorized_user_file('token.json', SCOPES)
    return build('youtube', 'v3', credentials=credentials)

# Convert YouTube Shorts URLs to normal URLs
def convert_shorts_url(url):
    if "shorts" in url:
        return url.replace("shorts/", "watch?v=")
    return url

# Download video using yt_dlp
def download_video(url, filename="video.mp4"):
    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        'outtmpl': filename,
        'merge_output_format': 'mp4',
        'noprogress': True,
        'quiet': True,
        'cookiefile': 'cookies.txt'  # <-- your exported YouTube cookies
    }
    if os.path.exists(filename):
        os.remove(filename)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return filename, info['title']

# Upload video to YouTube
def upload_video(filename, title, privacy_status="private", schedule_time=None):
    youtube = get_authenticated_service()
    status = {'privacyStatus': privacy_status, 'selfDeclaredMadeForKids': False}
    if schedule_time:
        status['publishAt'] = schedule_time.isoformat()
    body = {
        'snippet': {'title': title, 'description': "Uploaded via Flask app", 'categoryId': "22"},
        'status': status
    }
    media = MediaFileUpload(filename, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response['id']

@app.route("/", methods=["GET", "POST"])
def home():
    message = None
    if request.method == "POST":
        url = convert_shorts_url(request.form["url"])
        schedule_time_str = request.form.get("schedule", "").strip()

        # Wrap in try/except to handle download/upload errors
        try:
            filename, title = download_video(url)
        except Exception as e:
            message = f"❌ Failed to download video: {str(e)}"
            return render_template_string(HTML_TEMPLATE, message=message)

        try:
            if schedule_time_str:
                local_tz = pytz.timezone("Asia/Kolkata")  # Change if needed
                scheduled_datetime = datetime.strptime(schedule_time_str, "%Y-%m-%d %H:%M")
                utc_time = local_tz.localize(scheduled_datetime).astimezone(pytz.utc)
                video_id = upload_video(filename, title, privacy_status="private", schedule_time=utc_time)
                message = f"✅ Video scheduled successfully! Video ID: {video_id}"
            else:
                video_id = upload_video(filename, title, privacy_status="public")
                message = f"✅ Video uploaded successfully! Video ID: {video_id}"
        except Exception as e:
            message = f"❌ Failed to upload video: {str(e)}"

    return render_template_string(HTML_TEMPLATE, message=message)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

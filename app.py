from flask import Flask, request, render_template_string
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
import yt_dlp
import os
from datetime import datetime
import pytz

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

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

def get_authenticated_service():
    credentials = Credentials.from_authorized_user_file('token.json', SCOPES)
    return build('youtube', 'v3', credentials=credentials)

def convert_shorts_url(url):
    if "shorts" in url:
        return url.replace("shorts/", "watch?v=")
    return url

def download_video(url, filename="video.mp4"):
    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        'outtmpl': filename,
        'merge_output_format': 'mp4',
        'noprogress': True,
        'quiet': True
    }
    if os.path.exists(filename):
        os.remove(filename)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return filename, info['title']

def upload_video(filename, title, privacy_status="private", schedule_time=None):
    youtube = get_authenticated_service()
    status = {'privacyStatus': privacy_status, 'selfDeclaredMadeForKids': False}
    if schedule_time:
        status['publishAt'] = schedule_time.isoformat()
    body = {'snippet': {'title': title, 'description': "Uploaded via Flask app", 'categoryId': "22"},
            'status': status}
    media = MediaFileUpload(filename, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
    return response['id']

@app.route("/", methods=["GET", "POST"])
def home():
    message = None
    if request.method == "POST":
        url = convert_shorts_url(request.form["url"])
        schedule_time_str = request.form.get("schedule", "").strip()
        filename, title = download_video(url)

        if schedule_time_str:
            local_tz = pytz.timezone("Asia/Kolkata")
            scheduled_datetime = datetime.strptime(schedule_time_str, "%Y-%m-%d %H:%M")
            utc_time = local_tz.localize(scheduled_datetime).astimezone(pytz.utc)
            video_id = upload_video(filename, title, privacy_status="private", schedule_time=utc_time)
            message = f"✅ Video scheduled successfully! Video ID: {video_id}"
        else:
            video_id = upload_video(filename, title, privacy_status="public")
            message = f"✅ Video uploaded successfully! Video ID: {video_id}"

    return render_template_string(HTML_TEMPLATE, message=message)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

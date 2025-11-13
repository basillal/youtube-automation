from flask import Flask, request, render_template_string
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import yt_dlp
import os
import re
import unicodedata
from datetime import datetime
import pytz

app = Flask(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.force-ssl'
]

# Simple HTML template for the web form
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

def slugify(value, allow_unicode=False):
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize('NFKC', value)
    else:
        value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value.lower())
    return re.sub(r'[-\s]+', '-', value).strip('-_')

def get_authenticated_service():
    credentials = None
    token_file = 'token.json'
    
    if os.path.exists(token_file):
        credentials = Credentials.from_authorized_user_file(token_file, SCOPES)
    
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
            # Use console flow instead of local server for server hosting
            credentials = flow.run_console()
        
        with open(token_file, 'w') as token:
            token.write(credentials.to_json())
    
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

def upload_video(filename, title, description="", tags=None, category_id="22", 
                 privacy_status="private", for_kids=False, schedule_time=None):
    if tags is None:
        tags = []

    youtube = get_authenticated_service()

    status = {
        'privacyStatus': privacy_status,
        'selfDeclaredMadeForKids': for_kids
    }
    if schedule_time:
        status['publishAt'] = schedule_time.isoformat()

    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': category_id
        },
        'status': status
    }

    media = MediaFileUpload(filename, chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
    return response['id']

@app.route("/", methods=["GET", "POST"])
def home():
    message = None
    if request.method == "POST":
        url = request.form["url"]
        schedule_time = request.form.get("schedule", "").strip()

        url = convert_shorts_url(url)
        filename, title = download_video(url)

        if schedule_time:
            local_tz = pytz.timezone("Asia/Kolkata")  # Change if needed
            scheduled_datetime = datetime.strptime(schedule_time, "%Y-%m-%d %H:%M")
            local_time = local_tz.localize(scheduled_datetime)
            utc_time = local_time.astimezone(pytz.utc)
            video_id = upload_video(
                filename=filename,
                title=title,
                description="Uploaded via Flask app",
                privacy_status="private",
                schedule_time=utc_time
            )
            message = f"✅ Video scheduled successfully! Video ID: {video_id}"
        else:
            video_id = upload_video(
                filename=filename,
                title=title,
                description="Uploaded via Flask app",
                privacy_status="public"
            )
            message = f"✅ Video uploaded successfully! Video ID: {video_id}"

    return render_template_string(HTML_TEMPLATE, message=message)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

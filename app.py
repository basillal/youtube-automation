from flask import Flask, request, render_template_string
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
import yt_dlp
import os
import json
from datetime import datetime
import pytz

app = Flask(__name__)

# YouTube API scopes
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

# HTML template
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

# ---------------------------
# 🔥 JSON → Netscape Cookie Converter
# ---------------------------
def convert_json_to_netscape(json_path="cookies.json", txt_path="cookies.txt"):
    if not os.path.exists(json_path):
        return False

    try:
        with open(json_path, "r") as f:
            cookies = json.load(f)

        with open(txt_path, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")

            for c in cookies:
                domain = c["domain"]
                include_subdomains = "TRUE" if not c.get("hostOnly", False) else "FALSE"
                path = c.get("path", "/")
                secure = "TRUE" if c.get("secure", False) else "FALSE"
                expiry = int(c["expirationDate"]) if "expirationDate" in c else 0
                name = c["name"]
                value = c["value"]

                f.write(
                    f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n"
                )
        return True
    except Exception as e:
        print("Cookie conversion failed:", e)
        return False


# ---------------------------
# YouTube API Auth
# ---------------------------
def get_authenticated_service():
    credentials = Credentials.from_authorized_user_file('token.json', SCOPES)
    return build('youtube', 'v3', credentials=credentials)


# ---------------------------
# Convert Shorts URL
# ---------------------------
def convert_shorts_url(url):
    return url.replace("shorts/", "watch?v=") if "shorts" in url else url


# ---------------------------
# Download Video via yt-dlp
# ---------------------------
def download_video(url, filename="video.mp4"):

    # Automatically convert cookies.json → cookies.txt
    if not os.path.exists("cookies.txt"):
        convert_json_to_netscape("cookies.json", "cookies.txt")

    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        'outtmpl': filename,
        'merge_output_format': 'mp4',
        'quiet': True,
        'cookiefile': 'cookies.txt'
    }

    if os.path.exists(filename):
        os.remove(filename)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    return filename, info.get('title', 'Uploaded Video')


# ---------------------------
# Upload Video to YouTube
# ---------------------------
def upload_video(filename, title, privacy_status="private", schedule_time=None):
    youtube = get_authenticated_service()

    status = {
        'privacyStatus': privacy_status,
        'selfDeclaredMadeForKids': False
    }

    if schedule_time:
        status['publishAt'] = schedule_time.isoformat()

    body = {
        'snippet': {
            'title': title,
            'description': "Uploaded via Flask app",
            'categoryId': "22"
        },
        'status': status
    }

    media = MediaFileUpload(filename, chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        _, response = request.next_chunk()

    return response['id']


# ---------------------------
# Flask Route
# ---------------------------
@app.route("/", methods=["GET", "POST"])
def home():
    message = None

    if request.method == "POST":
        url = convert_shorts_url(request.form["url"])
        schedule_time_str = request.form.get("schedule", "").strip()

        # Step 1: Download video
        try:
            filename, title = download_video(url)
        except Exception as e:
            message = f"❌ Failed to download video: {str(e)}"
            return render_template_string(HTML_TEMPLATE, message=message)

        # Step 2: Upload video
        try:
            if schedule_time_str:
                local_tz = pytz.timezone("Asia/Kolkata")
                scheduled_datetime = datetime.strptime(schedule_time_str, "%Y-%m-%d %H:%M")
                utc_time = local_tz.localize(scheduled_datetime).astimezone(pytz.utc)

                video_id = upload_video(filename, title, "private", schedule_time=utc_time)
                message = f"✅ Video scheduled successfully! Video ID: {video_id}"

            else:
                video_id = upload_video(filename, title, "public")
                message = f"✅ Video uploaded successfully! Video ID: {video_id}"

        except Exception as e:
            message = f"❌ Failed to upload video: {str(e)}"

    return render_template_string(HTML_TEMPLATE, message=message)


# ---------------------------
# Run App
# ---------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

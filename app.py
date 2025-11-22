from flask import Flask, request, redirect, render_template_string, session, url_for
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import yt_dlp
import os
import json
from datetime import datetime
import pytz
import traceback

# -------- app setup --------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET") or os.urandom(32)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

HTML_TEMPLATE = """
<!doctype html>
<title>YouTube Uploader</title>
<h1>YouTube Uploader</h1>

{% if not token_exists %}
  <p style='color:red;'>❌ token.json not found — authentication required.</p>
  <a href="{{ url_for('authorize') }}"><button>Authenticate YouTube</button></a>
{% else %}
  <form method="post">
      YouTube URL: <input name="url" required><br><br>
      Schedule upload? (YYYY-MM-DD HH:MM): <input name="schedule"><br><br>
      <input type="submit" value="Upload">
  </form>
{% endif %}

{% if message %}
<hr>
<p>{{ message|safe }}</p>
{% endif %}
"""

# -------- utilities --------
def token_exists():
    return os.path.exists("token.json")

def create_flow():
    """Flow configured for **Render HTTPS domain**."""
    redirect_uri = "https://youtube-automation-3w2v.onrender.com/oauth2callback"

    return Flow.from_client_secrets_file(
        "client_secret.json",
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )

@app.route("/authorize")
def authorize():
    flow = create_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )
    session["oauth_state"] = state
    return redirect(auth_url)

@app.route("/oauth2callback")
def oauth2callback():
    try:
        flow = create_flow()
        flow.state = session.get("oauth_state")
        flow.fetch_token(authorization_response=request.url)

        creds = flow.credentials

        # Save token.json
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        return redirect("/")
    except Exception:
        tb = traceback.format_exc()
        return render_template_string(HTML_TEMPLATE, message=f"<pre>{tb}</pre>", token_exists=token_exists())

# -------- YouTube helpers --------
def get_authenticated_service():
    if not token_exists():
        raise FileNotFoundError("token.json missing")
    credentials = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("youtube", "v3", credentials=credentials)

def convert_shorts_url(url):
    return url.replace("shorts/", "watch?v=") if "shorts/" in url else url

def download_video(url, filename="video.mp4"):
    if os.path.exists(filename):
        os.remove(filename)

    ydl_opts = {
        "format": "bestvideo[height<=720]+bestaudio/best",
        "outtmpl": filename,
        "merge_output_format": "mp4",
        "quiet": True
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    return filename, info.get("title", "Uploaded Video")

def upload_video(filename, title, privacy="private", schedule_utc=None):
    youtube = get_authenticated_service()

    status = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": False
    }

    if schedule_utc:
        status["publishAt"] = schedule_utc.isoformat()

    body = {
        "snippet": {
            "title": title,
            "description": "Uploaded via Flask uploader",
            "categoryId": "22"
        },
        "status": status
    }

    media = MediaFileUpload(filename, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()

    return response.get("id")

# -------- main page --------
@app.route("/", methods=["GET", "POST"])
def home():
    msg = None

    if request.method == "POST":
        if not token_exists():
            return redirect("/authorize")

        url = convert_shorts_url(request.form["url"])
        schedule_str = request.form.get("schedule", "").strip()

        try:
            filename, title = download_video(url)
        except Exception as e:
            msg = f"❌ Download failed: {e}"
            return render_template_string(HTML_TEMPLATE, message=msg, token_exists=token_exists())

        try:
            if schedule_str:
                dt = datetime.strptime(schedule_str, "%Y-%m-%d %H:%M")
                local_tz = pytz.timezone("Asia/Kolkata")
                schedule_utc = local_tz.localize(dt).astimezone(pytz.utc)

                video_id = upload_video(filename, title, "private", schedule_utc)
                msg = f"✅ Scheduled! Video ID: {video_id}"
            else:
                video_id = upload_video(filename, title, "public")
                msg = f"✅ Uploaded! Video ID: {video_id}"
        except Exception as e:
            msg = f"❌ Upload failed: {e}"

    return render_template_string(HTML_TEMPLATE, message=msg, token_exists=token_exists())

# -------- server run --------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

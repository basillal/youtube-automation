import os
import base64
import json
import traceback
from datetime import datetime

from flask import Flask, request, redirect, render_template_string, session, url_for
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import yt_dlp
import pytz

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # allow http for localhost

# -------- app setup --------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET") or os.urandom(32)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# -------- Decode client_secret.json and cookies.txt from base64 --------
if "CLIENT_SECRET_JSON_B64" in os.environ:
    with open("client_secret.json", "wb") as f:
        f.write(base64.b64decode(os.environ["CLIENT_SECRET_JSON_B64"]))

# Decode cookies and check format
def write_cookies_from_env():
    if "YT_COOKIES_B64" not in os.environ:
        return False

    decoded = base64.b64decode(os.environ["YT_COOKIES_B64"]).decode("utf-8", errors="ignore")
    # basic check for Netscape format (first line starts with # Netscape)
    if not decoded.startswith("# Netscape"):
        raise ValueError("Cookies Base64 does not contain valid Netscape-format cookies. Export cookies in Netscape format, not JSON.")

    with open("cookies.txt", "w", encoding="utf-8") as f:
        f.write(decoded)
    return True

write_cookies_from_env()

# -------- HTML template --------
HTML_TEMPLATE = """
<!doctype html>
<title>YouTube Uploader</title>
<h1>YouTube Uploader</h1>

{% if not token_exists %}
  <p style='color:red;'>❌ token.json not found — first-time setup required.</p>
  <p>Click below to authenticate with Google:</p>
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

# -------- OAuth flow helpers --------
def create_flow(redirect_uri):
    return Flow.from_client_secrets_file(
        "client_secret.json",
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )

@app.route("/authorize")
def authorize():
    redirect_uri = url_for("oauth2callback", _external=True)
    flow = create_flow(redirect_uri)
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
        redirect_uri = url_for("oauth2callback", _external=True)
        flow = create_flow(redirect_uri)
        flow.state = session.get("oauth_state")
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        return redirect(url_for("home"))
    except Exception as e:
        tb = traceback.format_exc()
        return render_template_string(HTML_TEMPLATE, message=f"<pre>OAuth error:\n{tb}</pre>", token_exists=token_exists())

# -------- YouTube helpers --------
def get_authenticated_service():
    if not token_exists():
        raise FileNotFoundError("token.json not found. Authenticate first.")
    credentials = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("youtube", "v3", credentials=credentials)

def convert_shorts_url(url):
    return url.replace("shorts/", "watch?v=") if "shorts/" in url else url

def download_video(url, filename="video.mp4"):
    if os.path.exists(filename):
        os.remove(filename)

    # Ensure cookies.txt exists
    if not os.path.exists("cookies.txt"):
        raise FileNotFoundError("cookies.txt not found. Make sure YT_COOKIES_B64 contains valid Netscape cookies.")

    ydl_opts = {
        "format": "bestvideo[height<=720]+bestaudio/best",
        "outtmpl": filename,
        "merge_output_format": "mp4",
        "quiet": True,
        "cookiefile": "cookies.txt"  # use cookies from Base64 env
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        # yt-dlp provides a clear error message if cookies are invalid
        raise RuntimeError(f"Download failed: {e}")

    return filename, info.get("title", "Uploaded Video")

def upload_video(filename, title, privacy="private", schedule_utc=None):
    youtube = get_authenticated_service()
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    if schedule_utc:
        status["publishAt"] = schedule_utc.isoformat()
    body = {
        "snippet": {"title": title, "description": "Uploaded via Flask uploader", "categoryId": "22"},
        "status": status
    }
    media = MediaFileUpload(filename, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response.get("id")

# -------- routes --------
@app.route("/", methods=["GET", "POST"])
def home():
    msg = None

    if request.method == "POST" and not token_exists():
        session["pending_url"] = request.form.get("url")
        session["pending_schedule"] = request.form.get("schedule", "")
        return redirect(url_for("authorize"))

    if request.method == "POST" and token_exists():
        url = convert_shorts_url(request.form.get("url", "").strip())
        schedule_str = request.form.get("schedule", "").strip()

        # Download
        try:
            filename, title = download_video(url)
        except Exception as e:
            tb = traceback.format_exc()
            msg = f"❌ Download failed: {e}\n\n<pre>{tb}</pre>"
            return render_template_string(HTML_TEMPLATE, message=msg, token_exists=token_exists())

        # Upload / Schedule
        try:
            if schedule_str:
                local_tz = pytz.timezone("Asia/Kolkata")
                dt = datetime.strptime(schedule_str, "%Y-%m-%d %H:%M")
                schedule_utc = local_tz.localize(dt).astimezone(pytz.utc)
                video_id = upload_video(filename, title, privacy="private", schedule_utc=schedule_utc)
                msg = f"✅ Scheduled upload! Video ID: {video_id}"
            else:
                video_id = upload_video(filename, title, privacy="public", schedule_utc=None)
                msg = f"✅ Uploaded successfully! Video ID: {video_id}"
        except Exception as e:
            tb = traceback.format_exc()
            msg = f"❌ Upload failed: {e}\n\n<pre>{tb}</pre>"

    if token_exists() and session.get("pending_url"):
        pending_url = session.pop("pending_url")
        pending_schedule = session.pop("pending_schedule", "")
        return render_template_string(
            HTML_TEMPLATE,
            message=f"Authenticated — re-submit to upload (previous URL: {pending_url})",
            token_exists=token_exists()
        )

    return render_template_string(HTML_TEMPLATE, message=msg, token_exists=token_exists())

# -------- run server --------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)

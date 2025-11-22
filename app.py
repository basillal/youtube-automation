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
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'   # allow http for localhost


# -------- app setup --------
app = Flask(__name__)
# Use environment-provided secret in production; fallback to random for local dev
app.secret_key = os.environ.get("FLASK_SECRET") or os.urandom(32)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

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
def convert_json_to_netscape(json_path="cookies.json", txt_path="cookies.txt"):
    """Convert Chrome cookie-export JSON to Netscape cookies.txt (optional)."""
    if not os.path.exists(json_path):
        return False
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for c in cookies:
                f.write(
                    f"{c.get('domain','')}\t"
                    f"{'TRUE' if not c.get('hostOnly', False) else 'FALSE'}\t"
                    f"{c.get('path','/')}\t"
                    f"{'TRUE' if c.get('secure', False) else 'FALSE'}\t"
                    f"{int(c['expirationDate']) if 'expirationDate' in c else 0}\t"
                    f"{c.get('name','')}\t{c.get('value','')}\n"
                )
        return True
    except Exception:
        return False

def token_exists():
    return os.path.exists("token.json")

# -------- OAuth flow helpers --------
def create_flow(redirect_uri):
    """Create a Flow object using client_secret.json and provided redirect URI."""
    return Flow.from_client_secrets_file(
        "client_secret.json",
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )

@app.route("/authorize")
def authorize():
    # Build a flow with redirect set to our oauth2callback route
    redirect_uri = url_for("oauth2callback", _external=True)
    flow = create_flow(redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline",       # request refresh token
        include_granted_scopes="true",
        prompt="consent"            # force refresh_token on first auth
    )

    # Persist state in session to verify in callback
    session["oauth_state"] = state
    return redirect(auth_url)

@app.route("/oauth2callback")
def oauth2callback():
    try:
        redirect_uri = url_for("oauth2callback", _external=True)
        flow = create_flow(redirect_uri)

        # restore state and fetch token using the full redirect URL
        flow.state = session.get("oauth_state")
        flow.fetch_token(authorization_response=request.url)

        creds = flow.credentials

        # Save token.json for later use by the uploader
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        return redirect(url_for("home", _external=False))
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
    # Optional cookies conversion if user provided cookies.json
    if not os.path.exists("cookies.txt"):
        convert_json_to_netscape()

    if os.path.exists(filename):
        os.remove(filename)

    ydl_opts = {
        "format": "bestvideo[height<=720]+bestaudio/best",
        "outtmpl": filename,
        "merge_output_format": "mp4",
        "quiet": True,
        # 'cookiefile': 'cookies.txt'  # uncomment if you exported cookies
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

# -------- routes --------
@app.route("/", methods=["GET", "POST"])
def home():
    msg = None

    # If user posted but token missing -> redirect to authorize
    if request.method == "POST" and not token_exists():
        # Save form info in session so we can resume after auth (optional)
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

    # If we returned from OAuth and there is pending form data, optionally resume:
    if token_exists() and session.get("pending_url"):
        # resume the pending upload automatically (optional)
        pending_url = session.pop("pending_url")
        pending_schedule = session.pop("pending_schedule", "")
        # redirect to POST the pending data so UI remains simple:
        return render_template_string(HTML_TEMPLATE, message=f"Authenticated — re-submit to upload (previous URL: {pending_url})", token_exists=token_exists())

    return render_template_string(HTML_TEMPLATE, message=msg, token_exists=token_exists())

# -------- run server --------
if __name__ == "__main__":
    # For local debugging set debug=True; in prod use a proper WSGI server
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)

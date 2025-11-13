# app.py
from flask import Flask, request, redirect, session, url_for, jsonify
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
import yt_dlp
import os
import re
import unicodedata
from datetime import datetime
import pytz
from tqdm import tqdm

# Flask setup
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_this_secret")

# YouTube OAuth scopes
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# Environment variables for Google OAuth
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI")  # e.g., https://yourapp.onrender.com/oauth2callback

# Google client config
CLIENT_CONFIG = {
    "web": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT_URI],
    }
}

# ---------------- Helper Functions ---------------- #

def slugify(value, allow_unicode=False):
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize('NFKC', value)
    else:
        value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value.lower())
    return re.sub(r'[-\s]+', '-', value).strip('-_')

def download_video(url, filename="video.mp4"):
    ydl_opts = {
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "outtmpl": filename,
        "merge_output_format": "mp4",
        "noprogress": True,
        "quiet": True,
    }
    if os.path.exists(filename):
        os.remove(filename)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return filename, info["title"]

def convert_shorts_url(url):
    if "shorts" in url:
        return url.replace("shorts/", "watch?v=")
    return url

def get_authenticated_service():
    if "credentials" not in session:
        return None
    credentials = Credentials(**session["credentials"])
    if not credentials.valid and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    session["credentials"] = credentials_to_dict(credentials)
    return build("youtube", "v3", credentials=credentials)

def credentials_to_dict(credentials):
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }

def upload_video(filename, title, description="", tags=None, category_id="22",
                 privacy_status="private", for_kids=False, schedule_time=None):
    if tags is None:
        tags = []

    youtube = get_authenticated_service()
    if not youtube:
        return {"error": "You are not authenticated."}

    status = {
        "privacyStatus": privacy_status,
        "selfDeclaredMadeForKids": for_kids
    }

    if schedule_time:
        status["publishAt"] = schedule_time.isoformat()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id
        },
        "status": status
    }

    media = MediaFileUpload(filename, chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media
    )

    response = None
    with tqdm(total=100, desc="Uploading", unit="%") as pbar:
        while response is None:
            status, response = request.next_chunk()
            if status:
                pbar.update(int(status.progress() * 100) - pbar.n)
    return response

# ---------------- Flask Routes ---------------- #

@app.route("/")
def index():
    return "✅ YouTube Uploader is running!"

@app.route("/authorize")
def authorize():
    flow = Flow.from_client_config(CLIENT_CONFIG, SCOPES)
    flow.redirect_uri = REDIRECT_URI
    authorization_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    session["state"] = state
    return redirect(authorization_url)

@app.route("/oauth2callback")
def oauth2callback():
    state = session["state"]
    flow = Flow.from_client_config(CLIENT_CONFIG, SCOPES, state=state)
    flow.redirect_uri = REDIRECT_URI
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials
    session["credentials"] = credentials_to_dict(credentials)
    return "✅ Authorization successful! You can now use /upload_video endpoint."

@app.route("/upload_video", methods=["POST"])
def upload_endpoint():
    if "credentials" not in session:
        return redirect(url_for("authorize"))

    data = request.json
    url = convert_shorts_url(data.get("url"))
    filename, title = download_video(url)
    description = data.get("description", "")
    tags = data.get("tags", [])
    privacy_status = data.get("privacy_status", "private")
    for_kids = data.get("for_kids", False)
    schedule_time = data.get("schedule_time")
    if schedule_time:
        local_tz = pytz.timezone("Asia/Kolkata")
        local_dt = local_tz.localize(datetime.strptime(schedule_time, "%Y-%m-%d %H:%M"))
        schedule_time = local_dt.astimezone(pytz.utc)

    response = upload_video(filename, title, description, tags, "22",
                            privacy_status, for_kids, schedule_time)
    return jsonify(response)

# ---------------- Run App ---------------- #
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

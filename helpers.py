import os
import shutil
import subprocess

from flask import current_app
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {
    "doc",
    "docx",
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "3gp",
    "avi",
    "m4v",
    "mkv",
    "mov",
    "mp4",
    "mpeg",
    "mpg",
    "webm",
    "ogg",
    "mp3",
    "wav",
    "m4a",
}
REACTION_TYPES = ["support", "understand", "keep-going", "inspire"]
REACTION_LABELS = {
    "support": "❤️ Support",
    "understand": "🤝 I Understand",
    "keep-going": "🔥 Keep Going",
    "inspire": "💪 You Inspire Me",
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_media_type(filename):
    extension = filename.rsplit(".", 1)[1].lower()
    if extension in {"3gp", "avi", "m4v", "mkv", "mov", "mp4", "mpeg", "mpg", "webm", "ogg"}:
        return "video"
    if extension in {"mp3", "wav", "m4a"}:
        return "audio"
    if extension in {"png", "jpg", "jpeg", "gif"}:
        return "image"
    return "file"


def video_needs_browser_conversion(path):
    try:
        with open(path, "rb") as file:
            data = file.read()
    except OSError:
        return False
    return b"hvc1" in data or b"hev1" in data


def is_hevc_upload(filename):
    if not filename:
        return False
    safe_name = os.path.basename(filename)
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], safe_name)
    return video_needs_browser_conversion(path)


def get_ffmpeg_executable():
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    return imageio_ffmpeg.get_ffmpeg_exe()


def convert_video_for_browser(path, filename):
    ffmpeg = get_ffmpeg_executable()
    extension = filename.rsplit(".", 1)[1].lower()
    if not ffmpeg:
        return filename
    if extension == "mp4" and not video_needs_browser_conversion(path):
        return filename

    base, _original_extension = os.path.splitext(filename)
    output_name = f"{base}_browser.mp4"
    output_path = os.path.join(current_app.config["UPLOAD_FOLDER"], output_name)
    counter = 1
    while os.path.exists(output_path):
        output_name = f"{base}_browser_{counter}.mp4"
        output_path = os.path.join(current_app.config["UPLOAD_FOLDER"], output_name)
        counter += 1

    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                path,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                output_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        if os.path.exists(output_path):
            os.remove(output_path)
        return filename
    return output_name


def get_ice_servers():
    servers = [
        {
            "urls": [
                "stun:stun.l.google.com:19302",
                "stun:stun1.l.google.com:19302",
            ]
        }
    ]
    turn_url = os.getenv("WEBRTC_TURN_URL", "").strip()
    if turn_url:
        turn_server = {"urls": turn_url}
        username = os.getenv("WEBRTC_TURN_USERNAME", "").strip()
        credential = os.getenv("WEBRTC_TURN_CREDENTIAL", "").strip()
        if username and credential:
            turn_server["username"] = username
            turn_server["credential"] = credential
        servers.append(turn_server)
    return servers


def save_media(file):
    if not file or not allowed_file(file.filename):
        return None
    filename = secure_filename(file.filename)
    destination = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    base, extension = os.path.splitext(filename)
    counter = 1
    while os.path.exists(destination):
        filename = f"{base}_{counter}{extension}"
        destination = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
        counter += 1
    file.save(destination)
    if get_media_type(filename) == "video":
        filename = convert_video_for_browser(destination, filename)
    return filename

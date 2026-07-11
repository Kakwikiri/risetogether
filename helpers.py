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
    if not filename or "." not in filename:
        return "file"
    extension = filename.rsplit(".", 1)[1].lower()
    if extension in {"3gp", "avi", "m4v", "mkv", "mov", "mp4", "mpeg", "mpg", "webm", "ogg"}:
        return "video"
    if extension in {"mp3", "wav", "m4a"}:
        return "audio"
    if extension in {"png", "jpg", "jpeg", "gif"}:
        return "image"
    return "file"


def get_upload_limit(media_type):
    if media_type == "image":
        return current_app.config["IMAGE_UPLOAD_LIMIT"]
    if media_type == "video":
        return current_app.config["VIDEO_UPLOAD_LIMIT"]
    return current_app.config["FILE_UPLOAD_LIMIT"]


def file_size(file):
    position = file.stream.tell()
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(position)
    return size


def validate_upload(file):
    if not file or not allowed_file(file.filename):
        return False, "Unsupported file type."
    media_type = get_media_type(file.filename)
    size = file_size(file)
    limit = get_upload_limit(media_type)
    if size > limit:
        limit_mb = limit // (1024 * 1024)
        return False, f"{media_type.capitalize()} uploads must be {limit_mb} MB or smaller."
    return True, ""


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


def optimize_image_for_storage(path, filename):
    extension = filename.rsplit(".", 1)[1].lower()
    if extension == "gif":
        return
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1600, 1600))
            save_options = {"optimize": True}
            if extension in {"jpg", "jpeg"}:
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(path, quality=82, progressive=True, **save_options)
            elif extension == "png":
                image.save(path, **save_options)
    except OSError:
        return


def persist_media_asset(filename):
    from extensions import db
    from models import MediaAsset

    path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    if not os.path.exists(path):
        return
    with open(path, "rb") as file:
        data = file.read()
    asset = MediaAsset.query.filter_by(filename=filename).first() or MediaAsset(filename=filename)
    asset.content_type = mimetype_for_filename(filename)
    asset.media_type = get_media_type(filename)
    asset.data = data
    asset.size = len(data)
    db.session.add(asset)


def mimetype_for_filename(filename):
    if not filename or "." not in filename:
        return "application/octet-stream"
    extension = filename.rsplit(".", 1)[1].lower()
    if extension in {"jpg", "jpeg"}:
        return "image/jpeg"
    if extension == "png":
        return "image/png"
    if extension == "gif":
        return "image/gif"
    if extension == "webm":
        return "video/webm"
    if extension in {"mp4", "m4v"}:
        return "video/mp4"
    if extension == "mov":
        return "video/quicktime"
    if extension == "mp3":
        return "audio/mpeg"
    if extension == "wav":
        return "audio/wav"
    if extension == "m4a":
        return "audio/mp4"
    if extension == "pdf":
        return "application/pdf"
    return "application/octet-stream"


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
    is_valid, _message = validate_upload(file)
    if not is_valid:
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
    media_type = get_media_type(filename)
    if media_type == "image":
        optimize_image_for_storage(destination, filename)
    elif media_type == "video":
        converted_filename = convert_video_for_browser(destination, filename)
        if converted_filename != filename:
            converted_path = os.path.join(current_app.config["UPLOAD_FOLDER"], converted_filename)
            if os.path.exists(converted_path):
                try:
                    os.remove(destination)
                except OSError:
                    pass
            filename = converted_filename
    final_path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    if os.path.exists(final_path) and os.path.getsize(final_path) > get_upload_limit(get_media_type(filename)):
        try:
            os.remove(final_path)
        except OSError:
            pass
        return None
    persist_media_asset(filename)
    return filename

import json
import os
import shutil
import subprocess
import hashlib
import re
from datetime import datetime
from html import escape
from urllib.parse import quote

from flask import current_app, url_for
from werkzeug.utils import secure_filename

try:
    from pywebpush import WebPushException, webpush
except ImportError:
    WebPushException = None
    webpush = None

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
MAX_VIDEO_DURATION_SECONDS = 180
REACTION_TYPES = ["support", "understand", "keep-going", "inspire"]
REACTION_LABELS = {
    "support": "❤️ Support",
    "understand": "🤝 I Understand",
    "keep-going": "🔥 Keep Going",
    "inspire": "💪 You Inspire Me",
}

AVATAR_PALETTES = (
    ("#115e59", "#ffffff"), ("#166534", "#ffffff"),
    ("#1e40af", "#ffffff"), ("#6b21a8", "#ffffff"),
    ("#9f1239", "#ffffff"), ("#9a3412", "#ffffff"),
    ("#334155", "#ffffff"), ("#0e7490", "#ffffff"),
)


def stable_avatar_palette(identity):
    digest = hashlib.sha256(str(identity or "risetogether").encode("utf-8")).digest()
    return AVATAR_PALETTES[digest[0] % len(AVATAR_PALETTES)]


def display_initial(value, fallback="R"):
    value = (value or "").strip()
    return (value[0] if value else fallback).upper()


def initials(value, limit=2, fallback="RT"):
    words = [word for word in (value or "").strip().split() if word]
    return "".join(word[0] for word in words[:limit]).upper() if words else fallback


def svg_data_url(svg):
    return f"data:image/svg+xml,{quote(svg, safe='')}"


def user_avatar_url(user):
    profile = getattr(user, "profile", None)
    if profile and profile.avatar:
        return url_for("api.serve_upload", filename=profile.avatar)
    name = getattr(profile, "display_name", "") or getattr(user, "username", "") or "RiseTogether member"
    identity = getattr(user, "id", None) or getattr(user, "username", name)
    background, foreground = stable_avatar_palette(f"user:{identity}")
    initial = escape(display_initial(name))
    label = escape(name)
    return svg_data_url(
        '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128" role="img" '
        f'aria-label="{label}"><rect width="128" height="128" rx="64" fill="{background}"/>'
        '<circle cx="98" cy="28" r="18" fill="rgba(255,255,255,.10)"/>'
        f'<text x="64" y="69" text-anchor="middle" dominant-baseline="middle" fill="{foreground}" '
        f'font-family="system-ui,-apple-system,sans-serif" font-size="58" font-weight="750">{initial}</text></svg>'
    )


def family_avatar_url(family):
    if getattr(family, "profile_image", ""):
        return url_for("api.serve_upload", filename=family.profile_image)
    name = getattr(family, "name", "") or "RiseTogether Family"
    identity = getattr(family, "id", None) or name
    background, foreground = stable_avatar_palette(f"family:{identity}")
    family_initials = escape(initials(name))
    label = escape(f"{name} Family")
    return svg_data_url(
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160" role="img" '
        f'aria-label="{label}"><rect width="160" height="160" rx="36" fill="{background}"/>'
        '<g fill="none" stroke="rgba(255,255,255,.58)" stroke-width="5" stroke-linecap="round">'
        '<circle cx="67" cy="43" r="13"/><circle cx="101" cy="48" r="10"/>'
        '<path d="M43 80c4-18 44-18 48 0M84 80c3-13 30-13 33 0"/></g>'
        f'<text x="80" y="126" text-anchor="middle" fill="{foreground}" font-family="system-ui,-apple-system,sans-serif" '
        f'font-size="38" font-weight="800" letter-spacing="2">{family_initials}</text></svg>'
    )


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
    from premium import upload_limit_for

    return upload_limit_for(media_type)


def file_size(file):
    position = file.stream.tell()
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(position)
    return size


def upload_signature_matches(file, extension):
    position = file.stream.tell()
    file.stream.seek(0)
    header = file.stream.read(16)
    file.stream.seek(position)
    if extension in {"jpg", "jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == "png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == "gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if extension == "pdf":
        return header.startswith(b"%PDF-")
    if extension == "doc":
        return header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if extension == "docx":
        return header.startswith(b"PK\x03\x04")
    if extension in {"mp4", "m4v", "mov", "3gp", "m4a"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if extension in {"webm", "mkv"}:
        return header.startswith(b"\x1aE\xdf\xa3")
    if extension == "avi":
        return header.startswith(b"RIFF") and header[8:12] == b"AVI "
    if extension == "wav":
        return header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    if extension == "ogg":
        return header.startswith(b"OggS")
    if extension == "mp3":
        return header.startswith(b"ID3") or (
            len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
        )
    if extension in {"mpeg", "mpg"}:
        return header.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"))
    return False


def validate_upload(file):
    if not file or not allowed_file(file.filename):
        return False, "Unsupported file type."
    extension = file.filename.rsplit(".", 1)[1].lower()
    if not upload_signature_matches(file, extension):
        return False, "The file contents do not match the selected file type."
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


def video_duration_seconds(path):
    ffmpeg = get_ffmpeg_executable()
    if not ffmpeg:
        return None
    try:
        result = subprocess.run(
            [ffmpeg, "-i", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def convert_video_for_browser(path, filename):
    ffmpeg = get_ffmpeg_executable()
    if not ffmpeg:
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
                "28",
                "-vf",
                "scale='min(1280,iw)':-2",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
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
    # Re-encoding can occasionally enlarge an already efficient clip. Keep the
    # smaller file so database persistence never grows because of optimization.
    try:
        if os.path.getsize(output_path) >= os.path.getsize(path):
            os.remove(output_path)
            return filename
    except OSError:
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


def delete_media_if_unreferenced(filename):
    """Remove stored bytes only after every supported content reference is gone."""
    if not filename:
        return False
    from extensions import db
    from models import (
        ChallengeCompletion, Family, FamilyGalleryItem, GoalProgress,
        MediaAsset, Message, MessageAttachment, Post, PostMedia, Profile,
    )

    references = (
        (Profile, Profile.avatar),
        (Post, Post.media_url),
        (PostMedia, PostMedia.media_url),
        (Message, Message.media_url),
        (MessageAttachment, MessageAttachment.media_url),
        (Family, Family.profile_image),
        (Family, Family.banner_image),
        (FamilyGalleryItem, FamilyGalleryItem.media_url),
        (ChallengeCompletion, ChallengeCompletion.evidence_media_url),
        (GoalProgress, GoalProgress.evidence_url),
    )
    if any(model.query.filter(column == filename).first() for model, column in references):
        return False
    asset = MediaAsset.query.filter_by(filename=filename).first()
    if asset:
        db.session.delete(asset)
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.basename(filename))
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        current_app.logger.warning("orphan_media_file_delete_failed filename=%s", filename)
    return bool(asset)


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


def device_push_body(notification):
    private_categories = {"message", "family_chat"}
    media_categories = {"voice_note", "video_note"}
    profile = getattr(notification.recipient, "profile", None)
    previews_enabled = getattr(profile, "notification_previews_enabled", True)
    if notification.category in private_categories:
        if previews_enabled:
            return notification.message
        return "You have a new message."
    if notification.category in media_categories:
        if previews_enabled:
            return notification.message
        return f"You received a new {notification.category.replace('_', ' ')}."
    return notification.message


def send_device_push(notification, title="RiseTogether"):
    from feature_flags import is_feature_enabled

    if not is_feature_enabled("enhanced_notifications"):
        current_app.logger.info(
            "push_skipped enhanced_notifications_disabled notification_id=%s",
            notification.id,
        )
        return
    if not webpush:
        current_app.logger.info("push_skipped pywebpush_not_installed notification_id=%s", notification.id)
        return
    public_key = current_app.config.get("VAPID_PUBLIC_KEY", "")
    private_key = current_app.config.get("VAPID_PRIVATE_KEY", "")
    subject = current_app.config.get("VAPID_SUBJECT", "")
    if not public_key or not private_key or not subject:
        current_app.logger.info("push_skipped vapid_not_configured notification_id=%s", notification.id)
        return
    if not getattr(notification.recipient.profile, "notifications_enabled", True):
        return

    from extensions import db
    from models import PushSubscription
    from notifications_service import important_unread_count, unread_private_message_count

    unread_messages = unread_private_message_count(notification.user_id)
    badge_count = unread_messages + important_unread_count(notification.user_id)

    payload = json.dumps(
        {
            "title": title,
            "body": device_push_body(notification),
            # Route push clicks through the existing notification opener so the
            # same record is marked read before its exact destination opens.
            "url": f"/notification/open/{notification.id}",
            "tag": f"notification-{notification.id}",
            "notification_id": notification.id,
            "category": notification.category,
            "badge_count": badge_count,
        }
    )
    subscriptions = PushSubscription.query.filter_by(
        user_id=notification.user_id, active=True
    ).all()
    for subscription in subscriptions:
        info = {
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
        }
        try:
            webpush(
                subscription_info=info,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
            )
            subscription.last_used_at = datetime.utcnow()
        except Exception as error:
            response = getattr(error, "response", None)
            status_code = getattr(response, "status_code", None)
            if WebPushException and isinstance(error, WebPushException) and status_code in {404, 410}:
                subscription.active = False
            current_app.logger.warning(
                "push_delivery_failed notification_id=%s subscription_id=%s status=%s error=%s",
                notification.id,
                subscription.id,
                status_code,
                error,
            )
    db.session.flush()


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
        duration = video_duration_seconds(destination)
        from premium import recording_limit_seconds
        if duration is None or duration > recording_limit_seconds("video"):
            try:
                os.remove(destination)
            except OSError:
                pass
            return None
        # Browser-ready MP4/H.264 uploads should not be synchronously re-encoded.
        # Conversion can take minutes and made the request look frozen. Only
        # convert formats that are known to need a compatibility copy.
        needs_conversion = video_needs_browser_conversion(destination)
        if needs_conversion:
            converted_filename = convert_video_for_browser(destination, filename)
            if converted_filename != filename:
                converted_path = os.path.join(current_app.config["UPLOAD_FOLDER"], converted_filename)
                if os.path.exists(converted_path):
                    try:
                        os.remove(destination)
                    except OSError:
                        pass
                filename = converted_filename
    elif media_type == "audio":
        from premium import recording_limit_seconds
        duration = video_duration_seconds(destination)
        if duration is None or duration > recording_limit_seconds("audio"):
            try:
                os.remove(destination)
            except OSError:
                pass
            return None
    final_path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    if os.path.exists(final_path) and os.path.getsize(final_path) > get_upload_limit(get_media_type(filename)):
        try:
            os.remove(final_path)
        except OSError:
            pass
        return None
    persist_media_asset(filename)
    return filename

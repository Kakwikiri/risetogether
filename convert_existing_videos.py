import os

from app import app
from extensions import db
from helpers import convert_video_for_browser, get_ffmpeg_executable, get_media_type
from models import Message, Post


def convert_record(record, field_name):
    filename = getattr(record, field_name)
    if not filename or get_media_type(filename) != "video":
        return False
    upload_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    converted = convert_video_for_browser(upload_path, filename)
    if converted == filename:
        return False
    setattr(record, field_name, converted)
    return True


with app.app_context():
    if not get_ffmpeg_executable():
        raise SystemExit("ffmpeg is not available. Install ffmpeg or imageio-ffmpeg first.")

    changed = 0
    for post in Post.query.filter_by(media_type="video").all():
        if convert_record(post, "media_url"):
            changed += 1
    for message in Message.query.filter_by(media_type="video").all():
        if convert_record(message, "media_url"):
            changed += 1

    db.session.commit()
    print(f"Converted {changed} existing videos.")

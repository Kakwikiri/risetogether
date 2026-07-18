from datetime import datetime, timedelta
from threading import Lock
from urllib.parse import parse_qs

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_socketio import emit, join_room, leave_room

from extensions import db, socketio
from family_upgrades import pinned_announcement_limit
from feature_flags import is_feature_enabled
from helpers import delete_media_if_unreferenced, get_ice_servers, get_media_type, save_media, user_avatar_url, validate_upload
from notifications_service import queue_device_push, smart_notify
from models import Block, Family, FamilyMember, FamilyMemberRestriction, FriendRequest, LiveSession, Message, MessageAttachment, MessageDeletion, MessageReaction, Notification, PushSubscription, User

chat_bp = Blueprint("chat", __name__)
connected_users = {}
open_chat_rooms = {}
live_broadcasters = {}
live_viewers = {}
active_calls = {}
active_calls_lock = Lock()
family_voice_participants = {}
CALL_TIMEOUT_SECONDS = 45
FAMILY_VOICE_ROOM_LIMIT = 8


def parse_user_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def active_message_filter(query):
    return query.filter((Message.expires_at == None) | (Message.expires_at > datetime.utcnow()))


def visible_message_filter(query):
    return active_message_filter(query).filter(
        ~Message.deletions.any(MessageDeletion.user_id == current_user.id)
    )


def paged_chat_messages(query, per_page=150):
    """Load one bounded history page so large chats stay responsive without losing access."""
    before_id = request.args.get("before", type=int)
    if before_id:
        query = query.filter(Message.id < before_id)
    rows = query.order_by(Message.created_at.desc(), Message.id.desc()).limit(per_page + 1).all()
    has_older = len(rows) > per_page
    rows = rows[:per_page]
    rows.reverse()
    return rows, (rows[0].id if has_older and rows else None), bool(before_id)


def clear_expired_message_pins(messages):
    """Keep the message, but remove a pin as soon as its 24-hour window ends."""
    now = datetime.utcnow()
    expired = [message for message in messages if message.pinned_until and message.pinned_until <= now]
    for message in expired:
        message.pinned_until = None
    if expired:
        db.session.commit()


def can_access_message(message):
    if message.sender_id == current_user.id or message.recipient_id == current_user.id:
        return True
    if message.family_id:
        return (
            FamilyMember.query.filter_by(
                family_id=message.family_id, user_id=current_user.id
            ).first()
            is not None
        )
    return False


def has_active_family_restriction(family_id, user_id, *restriction_types):
    query = FamilyMemberRestriction.query.filter_by(
        family_id=family_id,
        user_id=user_id,
        active=True,
    ).filter(
        (FamilyMemberRestriction.ends_at == None)
        | (FamilyMemberRestriction.ends_at > datetime.utcnow())
    )
    if restriction_types:
        query = query.filter(FamilyMemberRestriction.restriction_type.in_(restriction_types))
    return query.first() is not None


def room_for_private_chat(user_id, other_id):
    return f"private-{min(user_id, other_id)}-{max(user_id, other_id)}"


def user_is_online(user_id):
    return bool(connected_users.get(user_id))


def user_room(user_id):
    return f"user-{user_id}"


def user_has_open_chat(user_id, room):
    return any(open_chat_rooms.get(sid) == room for sid in connected_users.get(user_id, set()))


def users_can_message(sender_id, recipient_id):
    if sender_id == recipient_id:
        return False
    recipient = User.query.get(recipient_id)
    sender = User.query.get(sender_id)
    if not sender or not recipient or sender.is_banned or recipient.is_banned:
        return False
    return (
        Block.query.filter_by(blocker_id=sender_id, blocked_id=recipient_id).first()
        is None
        and Block.query.filter_by(blocker_id=recipient_id, blocked_id=sender_id).first()
        is None
    )


def call_id_for_users(user_id, other_id):
    return f"call-{min(user_id, other_id)}-{max(user_id, other_id)}"


def call_room_for_id(call_id):
    return f"call-room-{call_id}"


def log_call_signal(event, call_id, user_id=None, room_id=None, target_id=None, **extra):
    call = get_call_state(call_id) if call_id else None
    details = {
        "event": event,
        "call_id": call_id,
        "caller_id": extra.pop("caller_id", None) or (call or {}).get("caller_id"),
        "receiver_id": extra.pop("receiver_id", None) or (call or {}).get("receiver_id"),
        "user_id": user_id or (current_user.id if current_user.is_authenticated else None),
        "target_id": target_id,
        "socket_id": request.sid,
        "room_id": room_id,
        **extra,
    }
    current_app.logger.info("call_signaling %s", details)


def set_call_state(call_id, state, caller_id=None, receiver_id=None, room_id=None, mode=None):
    with active_calls_lock:
        call = active_calls.setdefault(call_id, {})
        if caller_id is not None:
            call["caller_id"] = caller_id
        if receiver_id is not None:
            call["receiver_id"] = receiver_id
        if room_id is not None:
            call["room_id"] = room_id
        if mode is not None:
            call["mode"] = mode
        call["state"] = state
        call["updated_at"] = datetime.utcnow()
        return dict(call)


def get_call_state(call_id):
    with active_calls_lock:
        call = active_calls.get(call_id)
        return dict(call) if call else None


def clear_call_state(call_id):
    with active_calls_lock:
        active_calls.pop(call_id, None)


def timeout_call_if_unanswered(app_obj, call_id):
    socketio.sleep(CALL_TIMEOUT_SECONDS)
    with app_obj.app_context():
        call = get_call_state(call_id)
        if not call or call.get("state") != "ringing":
            return
        caller_id = call.get("caller_id")
        receiver_id = call.get("receiver_id")
        room_id = call.get("room_id") or call_room_for_id(call_id)
        mode = call.get("mode", "video")
        if caller_id and receiver_id:
            create_call_history(caller_id, receiver_id, mode, "missed")
        socketio.emit(
            "call_timeout",
            {"call_id": call_id, "room_id": room_id, "state": "missed"},
            room=room_id,
        )
        current_app.logger.info(
            "call_signaling %s",
            {
                "event": "server_call_timeout",
                "call_id": call_id,
                "caller_id": caller_id,
                "receiver_id": receiver_id,
                "room_id": room_id,
            },
        )
        clear_call_state(call_id)


def socket_transport():
    query = parse_qs(request.environ.get("QUERY_STRING", ""))
    return (query.get("transport") or ["unknown"])[0]


def log_socket_event(event, **extra):
    current_app.logger.info(
        "socket_event %s",
        {
            "event": event,
            "user_id": current_user.id if current_user.is_authenticated else None,
            "socket_id": request.sid,
            "namespace": getattr(request, "namespace", "/"),
            "transport": socket_transport(),
            **extra,
        },
    )


def log_live_signal(event, session_id=None, broadcaster_id=None, viewer_id=None, room_id=None, **extra):
    current_app.logger.info(
        "live_signaling %s",
        {
            "event": event,
            "stream_id": session_id,
            "broadcaster_id": broadcaster_id,
            "viewer_id": viewer_id,
            "room_id": room_id,
            "socket_id": request.sid,
            **extra,
        },
    )


def serialize_message(message):
    media_items = []
    if message.media_url:
        media_items.append({
            "url": url_for("api.serve_upload", filename=message.media_url),
            "type": message.media_type,
        })
    media_items.extend({
        "url": url_for("api.serve_upload", filename=item.media_url),
        "type": item.media_type,
    } for item in message.attachments)
    return {
        "sender_id": message.sender_id,
        "sender_name": message.sender.username,
        "sender_avatar_url": user_avatar_url(message.sender),
        "recipient_id": message.recipient_id,
        "family_id": message.family_id,
        "content": message.content,
        "media_url": (
            url_for("api.serve_upload", filename=message.media_url)
            if message.media_url
            else ""
        ),
        "media_type": message.media_type,
        "media_items": media_items,
        "message_id": message.id,
        "reply_to_id": message.reply_to_id,
        "view_once": message.view_once,
        "delivered": message.delivered,
        "read_at": message.read_at.isoformat() if message.read_at else None,
        "created_at": message.created_at.strftime("%Y-%m-%d %H:%M"),
    }


def emit_chat_message(message, room, event_name=None, recipient_ids=None):
    event = event_name or ("new_family_message" if message.family_id else "new_private_message")
    payload = serialize_message(message)
    delivered_rooms = set()
    if room:
        socketio.emit(event, payload, room=room)
        delivered_rooms.add(room)
    user_ids = set(recipient_ids or [])
    if message.sender_id:
        user_ids.add(message.sender_id)
    if message.recipient_id:
        user_ids.add(message.recipient_id)
    for user_id in user_ids:
        personal_room = user_room(user_id)
        if personal_room in delivered_rooms:
            continue
        socketio.emit(event, payload, room=personal_room)
        delivered_rooms.add(personal_room)
    current_app.logger.info(
        "chat_realtime_emit %s",
        {
            "event": event,
            "message_id": message.id,
            "sender_id": message.sender_id,
            "recipient_id": message.recipient_id,
            "family_id": message.family_id,
            "room": room,
            "user_rooms": sorted(user_ids),
        },
    )
    return payload


def mark_private_messages_delivered(sender_id, recipient_id):
    messages = Message.query.filter_by(
        sender_id=sender_id,
        recipient_id=recipient_id,
        delivered=False,
    ).all()
    if not messages:
        return []
    message_ids = [message.id for message in messages]
    for message in messages:
        message.delivered = True
    db.session.commit()
    socketio.emit(
        "messages_delivered",
        {"message_ids": message_ids, "reader_id": recipient_id},
        room=user_room(sender_id),
    )
    socketio.emit(
        "messages_delivered",
        {"message_ids": message_ids, "reader_id": recipient_id},
        room=room_for_private_chat(sender_id, recipient_id),
    )
    return message_ids


def mark_private_messages_read(sender_id, recipient_id):
    now = datetime.utcnow()
    messages = Message.query.filter_by(sender_id=sender_id, recipient_id=recipient_id, read_at=None).all()
    for message in messages:
        message.read_at = now
        message.delivered = True
    if messages:
        db.session.commit()
        socketio.emit(
            "messages_read",
            {"message_ids": [message.id for message in messages], "reader_id": recipient_id},
            room=user_room(sender_id),
        )
    return [message.id for message in messages]


def create_call_history(sender_id, recipient_id, mode, status):
    label = "audio" if mode == "audio" else "video"
    messages = {
        "started": f"{label.title()} call started",
        "missed": f"Missed {label} call",
        "ended": f"{label.title()} call ended",
    }
    message = Message(
        sender_id=sender_id,
        recipient_id=recipient_id,
        content=messages.get(status, f"{label.title()} call"),
        media_type="call",
    )
    db.session.add(message)
    db.session.commit()
    room = room_for_private_chat(sender_id, recipient_id)
    emit_chat_message(message, room, "new_private_message", [sender_id, recipient_id])
    return message


def emit_notification(user_id, notification):
    socketio.emit(
        "notification_received",
        {
            "id": notification.id,
            "category": notification.category,
            "message": notification.message,
            "action_url": notification.action_url,
            "created_at": notification.created_at.strftime("%Y-%m-%d %H:%M"),
        },
        room=user_room(user_id),
    )


@chat_bp.route("/messages")
@login_required
def inbox():
    if not PushSubscription.query.filter_by(user_id=current_user.id, active=True).first():
        _notification, created = smart_notify(
            user_id=current_user.id,
            category="reminders",
            message="Turn on device notifications so important messages can reach you while RiseTogether is closed.",
            action_url=url_for("main.settings") + "#device-notifications",
            dedupe_key=f"device-notifications:{current_user.id}",
            reminder=True,
            push=False,
            important=False,
        )
        if created:
            db.session.commit()
    direct_messages = visible_message_filter(Message.query.filter(
        (Message.sender_id == current_user.id) | (Message.recipient_id == current_user.id)
    )).order_by(Message.created_at.desc()).all()
    conversations = []
    seen_user_ids = set()
    for message in direct_messages:
        other_id = message.recipient_id if message.sender_id == current_user.id else message.sender_id
        if not other_id or other_id in seen_user_ids:
            continue
        seen_user_ids.add(other_id)
        other = User.query.get(other_id)
        if other:
            unread = Message.query.filter_by(
                sender_id=other_id, recipient_id=current_user.id, read_at=None
            ).filter(
                (Message.expires_at == None) | (Message.expires_at > datetime.utcnow()),
                ~Message.deletions.any(MessageDeletion.user_id == current_user.id),
            ).count()
            conversations.append({"user": other, "message": message, "unread": unread})
    families = [
        membership.family
        for membership in current_user.family_memberships
    ]
    friendships = FriendRequest.query.filter(
        FriendRequest.status == "accepted",
        (FriendRequest.sender_id == current_user.id) | (FriendRequest.receiver_id == current_user.id),
    ).all()
    friend_ids = {
        row.receiver_id if row.sender_id == current_user.id else row.sender_id
        for row in friendships
    }
    friends = User.query.filter(User.id.in_(friend_ids)).order_by(User.username).limit(8).all() if friend_ids else []
    excluded_ids = set(friend_ids) | {current_user.id}
    pending = FriendRequest.query.filter(
        (FriendRequest.sender_id == current_user.id) | (FriendRequest.receiver_id == current_user.id)
    ).all()
    excluded_ids.update(row.receiver_id if row.sender_id == current_user.id else row.sender_id for row in pending)
    suggested_friends = User.query.filter(
        ~User.id.in_(excluded_ids), User.is_banned == False, User.is_hidden_from_directory == False
    ).order_by(User.created_at.desc()).limit(6).all()
    return render_template(
        "messages.html", conversations=conversations, families=families,
        friends=friends, suggested_friends=suggested_friends,
    )


@chat_bp.route("/chat/<int:user_id>")
@login_required
def direct_chat(user_id):
    other = User.query.get_or_404(user_id)
    if other.id == current_user.id:
        return redirect(url_for("main.home"))
    if not users_can_message(current_user.id, other.id):
        flash("Messaging is not available with this account.", "warning")
        return redirect(url_for("main.profile", username=other.username))
    message_query = visible_message_filter(
            Message.query.filter(
            (
                (Message.sender_id == current_user.id)
                & (Message.recipient_id == other.id)
            )
            | (
                (Message.sender_id == other.id)
                & (Message.recipient_id == current_user.id)
            )
        ))
    messages, older_before_id, viewing_older = paged_chat_messages(message_query)
    clear_expired_message_pins(messages)
    mark_private_messages_delivered(other.id, current_user.id)
    mark_private_messages_read(other.id, current_user.id)
    Notification.query.filter(
        Notification.user_id == current_user.id,
        Notification.category.in_(["message", "call"]),
        Notification.action_url.startswith(url_for("chat.direct_chat", user_id=other.id)),
        Notification.seen == False,
    ).update({"seen": True})
    db.session.commit()
    return render_template(
        "chat.html",
        other=other,
        messages=messages,
        family=None,
        is_other_online=user_is_online(other.id),
        older_before_id=older_before_id,
        viewing_older=viewing_older,
    )


@chat_bp.route("/family/<int:family_id>/chat")
@login_required
def family_chat(family_id):
    family = Family.query.get_or_404(family_id)
    membership = FamilyMember.query.filter_by(
        family_id=family.id, user_id=current_user.id
    ).first()
    if not membership:
        if family.privacy == "private":
            flash("This family chat is private. Ask an admin for an invite.", "warning")
        else:
            flash("Join the family before opening the group chat.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    messages, older_before_id, viewing_older = paged_chat_messages(
        visible_message_filter(Message.query.filter_by(family_id=family.id))
    )
    clear_expired_message_pins(messages)
    Notification.query.filter(
        Notification.user_id == current_user.id,
        Notification.category.in_(["message", "family_chat"]),
        Notification.action_url.startswith(url_for("chat.family_chat", family_id=family.id)),
        Notification.seen == False,
    ).update({"seen": True})
    db.session.commit()
    return render_template(
        "chat.html", other=None, messages=messages, family=family,
        older_before_id=older_before_id, viewing_older=viewing_older,
    )


@chat_bp.route("/family/<int:family_id>/voice")
@login_required
def family_voice_room(family_id):
    family = Family.query.get_or_404(family_id)
    membership = FamilyMember.query.filter_by(
        family_id=family.id, user_id=current_user.id
    ).first()
    if not membership:
        flash("Join this family before opening its voice room.", "warning")
        return redirect(url_for("family.family_detail", family_id=family.id))
    return render_template(
        "family_voice_room.html",
        family=family,
        ice_servers=get_ice_servers(),
        room_limit=FAMILY_VOICE_ROOM_LIMIT,
    )


@login_required
def calls(user_id):
    if not current_app.config.get("REALTIME_MEDIA_ENABLED"):
        flash("Audio and video calls are coming soon.", "info")
        return redirect(url_for("chat.direct_chat", user_id=user_id))
    target = User.query.get_or_404(user_id)
    call_id = call_id_for_users(current_user.id, target.id)
    return render_template(
        "call.html",
        other=target,
        ice_servers=get_ice_servers(),
        call_id=call_id,
        call_room=call_room_for_id(call_id),
    )


@chat_bp.route("/chat/upload", methods=["POST"])
@login_required
def upload_message_file():
    media_files = [item for item in request.files.getlist("file") if item and item.filename]
    media_file = media_files[0] if media_files else None
    content = request.form.get("content", "").strip()
    reply_to_id = parse_user_id(request.form.get("reply_to_id"))
    view_once = request.form.get("view_once") == "1"
    expires_in = request.form.get("expires_in")
    media_kind = request.form.get("media_kind", "").strip()
    recipient_id = request.form.get("recipient_id")
    family_id = request.form.get("family_id")
    if len(media_files) > 6:
        return jsonify({"error": "Choose up to 6 photos at once."}), 400
    if len(media_files) > 1 and any(get_media_type(item.filename) != "image" for item in media_files):
        return jsonify({"error": "Multiple chat attachments must all be photos."}), 400
    if media_kind == "audio":
        extension = media_file.filename.rsplit(".", 1)[1].lower() if media_file and "." in media_file.filename else ""
        if extension not in {"webm", "ogg", "mp3", "wav", "m4a"}:
            return jsonify({"error": "Voice notes must be uploaded as an audio recording."}), 400
    if media_kind == "video":
        extension = media_file.filename.rsplit(".", 1)[1].lower() if media_file and "." in media_file.filename else ""
        if extension not in {"webm", "mp4", "mov", "m4v"}:
            return jsonify({"error": "Video notes must be uploaded as a video recording."}), 400
    for item in media_files:
        is_valid, upload_message = validate_upload(item)
        if not is_valid:
            return jsonify({"error": upload_message}), 400
    saved_media = []
    for item in media_files:
        filename = save_media(item)
        if not filename:
            for saved_filename in saved_media:
                delete_media_if_unreferenced(saved_filename)
            from premium import recording_limit_seconds
            limit_minutes = max(1, recording_limit_seconds(media_kind or get_media_type(item.filename)) // 60)
            return jsonify({"error": f"Upload failed. Recordings must be valid and {limit_minutes} minutes or shorter."}), 400
        saved_media.append(filename)
    if not saved_media:
        return jsonify({"error": "Choose a file to send."}), 400
    filename = saved_media[0]
    media_type = media_kind if media_kind in {"audio", "video"} else get_media_type(filename)
    message = Message(
        sender_id=current_user.id,
        content=content,
        media_url=filename,
        media_type=media_type,
        reply_to_id=reply_to_id,
        view_once=view_once,
        expires_at=(
            datetime.utcnow() + timedelta(minutes=1) if expires_in == "60" else None
        ),
    )
    room = None
    recipients = []
    if family_id:
        try:
            family_id = int(family_id)
        except (TypeError, ValueError):
            for saved_filename in saved_media:
                delete_media_if_unreferenced(saved_filename)
            return jsonify({"error": "Invalid family."}), 400
        family = Family.query.get(family_id)
        membership = (
            FamilyMember.query.filter_by(family_id=family.id, user_id=current_user.id).first()
            if family
            else None
        )
        if not family or not membership:
            for saved_filename in saved_media:
                delete_media_if_unreferenced(saved_filename)
            return jsonify({"error": "Join this family before sending files."}), 403
        if has_active_family_restriction(family.id, current_user.id, "mute", "suspend"):
            for saved_filename in saved_media:
                delete_media_if_unreferenced(saved_filename)
            return jsonify({"error": "You are temporarily restricted from sending Family messages."}), 403
        message.family_id = family.id
        room = f"family-{family.id}"
        recipients = [member.user_id for member in family.members if member.user_id != current_user.id]
    elif recipient_id:
        try:
            recipient_id = int(recipient_id)
        except (TypeError, ValueError):
            for saved_filename in saved_media:
                delete_media_if_unreferenced(saved_filename)
            return jsonify({"error": "Invalid recipient."}), 400
        recipient = User.query.get(recipient_id)
        if not recipient:
            for saved_filename in saved_media:
                delete_media_if_unreferenced(saved_filename)
            return jsonify({"error": "Recipient not found."}), 404
        message.recipient_id = recipient.id
        room = room_for_private_chat(current_user.id, recipient.id)
        recipients = [recipient.id]
    else:
        for saved_filename in saved_media:
            delete_media_if_unreferenced(saved_filename)
        return jsonify({"error": "Choose a chat first."}), 400
    if not message.family_id and recipients and user_has_open_chat(recipients[0], room):
        message.delivered = True
        message.read_at = datetime.utcnow()
    db.session.add(message)
    db.session.flush()
    for position, attachment_filename in enumerate(saved_media[1:], start=2):
        db.session.add(MessageAttachment(
            message_id=message.id,
            media_url=attachment_filename,
            media_type=get_media_type(attachment_filename),
            position=position,
        ))
    db.session.commit()
    payload = emit_chat_message(
        message,
        room,
        "new_family_message" if message.family_id else "new_private_message",
        [current_user.id, *recipients],
    )
    if len(saved_media) > 1:
        notification_message = f"{current_user.username} sent {len(saved_media)} photos."
    elif media_type == "audio":
        notification_message = f"{current_user.username} sent a voice note."
    elif media_type == "video":
        notification_message = f"{current_user.username} sent a video note."
    else:
        notification_message = f"{current_user.username} shared a file."
    notification_ids = []
    for user_id in recipients:
        if user_has_open_chat(user_id, room):
            continue
        notification, _ = smart_notify(
            user_id=user_id, category="family_chat" if message.family_id else "message", message=notification_message,
            action_url=(
                url_for("chat.family_chat", family_id=message.family_id)
                if message.family_id
                else url_for("chat.direct_chat", user_id=current_user.id)
            ) + f"#message-{message.id}",
            group_key=f"message:{current_user.id}:{message.family_id or 'direct'}",
            dedupe_key=f"message-media:{message.id}:{user_id}",
            push=False,
        )
        if notification:
            notification_ids.append(notification.id)
    db.session.commit()
    for notification_id in notification_ids:
        queue_device_push(notification_id)
    return jsonify(payload)


@chat_bp.route("/chat/message/<int:message_id>/delete", methods=["POST"])
@login_required
def delete_message(message_id):
    message = Message.query.get_or_404(message_id)
    if not can_access_message(message):
        return jsonify({"error": "Not allowed."}), 403
    scope = request.form.get("scope", "me")
    room = (
        f"family-{message.family_id}"
        if message.family_id
        else f"private-{min(message.sender_id, message.recipient_id)}-{max(message.sender_id, message.recipient_id)}"
    )
    if scope == "everyone":
        if message.sender_id != current_user.id and not current_user.is_admin:
            return jsonify({"error": "Only the sender can delete this message for everyone."}), 403
        media_urls = [message.media_url, *[item.media_url for item in message.attachments]]
        db.session.delete(message)
        db.session.flush()
        for media_url in media_urls:
            delete_media_if_unreferenced(media_url)
        db.session.commit()
        socketio.emit("message_deleted", {"message_id": message_id}, room=room)
        return jsonify({"ok": True, "scope": "everyone"})
    existing = MessageDeletion.query.filter_by(
        message_id=message.id, user_id=current_user.id
    ).first()
    if not existing:
        db.session.add(MessageDeletion(message_id=message.id, user_id=current_user.id))
        db.session.commit()
    return jsonify({"ok": True, "scope": "me"})


@chat_bp.route("/chat/message/<int:message_id>/react", methods=["POST"])
@login_required
def react_to_message(message_id):
    message = Message.query.get_or_404(message_id)
    if not can_access_message(message):
        return jsonify({"error": "Not allowed."}), 403
    reaction = request.form.get("reaction", "").strip()
    if reaction not in {"heart", "support", "understand"}:
        return jsonify({"error": "Invalid reaction."}), 400
    existing = MessageReaction.query.filter_by(message_id=message.id, user_id=current_user.id).first()
    if existing and existing.reaction == reaction:
        db.session.delete(existing)
    elif existing:
        existing.reaction = reaction
    else:
        db.session.add(MessageReaction(message_id=message.id, user_id=current_user.id, reaction=reaction))
    db.session.flush()
    counts = {
        key: MessageReaction.query.filter_by(message_id=message.id, reaction=key).count()
        for key in ("heart", "support", "understand")
    }
    selected = MessageReaction.query.filter_by(message_id=message.id, user_id=current_user.id).first()
    recipient_id = message.sender_id if message.sender_id != current_user.id else message.recipient_id
    room = f"family-{message.family_id}" if message.family_id else room_for_private_chat(message.sender_id, message.recipient_id)
    notification = None
    if selected and recipient_id and recipient_id != current_user.id and not user_has_open_chat(recipient_id, room):
        notification, _ = smart_notify(
            user_id=recipient_id,
            category="message_reaction",
            message=f"{current_user.username} reacted to your message.",
            action_url=(
                url_for("chat.family_chat", family_id=message.family_id)
                if message.family_id
                else url_for("chat.direct_chat", user_id=current_user.id)
            ) + f"#message-{message.id}",
            group_key=f"message-reaction:{message.id}",
            dedupe_key=f"message-reaction:{message.id}:{current_user.id}:{reaction}",
            push=False,
        )
    db.session.commit()
    socketio.emit(
        "message_reaction_updated",
        {"message_id": message.id, "counts": counts},
        room=room,
    )
    if notification:
        queue_device_push(notification.id)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "ok": True,
            "message_id": message.id,
            "counts": counts,
            "selected_reaction": selected.reaction if selected else None,
        })
    return redirect((request.referrer or url_for("chat.inbox")) + f"#message-{message.id}")


@chat_bp.route("/chat/message/<int:message_id>/viewed", methods=["POST"])
@login_required
def viewed_once_message(message_id):
    message = Message.query.get_or_404(message_id)
    if not can_access_message(message):
        return jsonify({"error": "Not allowed."}), 403
    if message.view_once and message.sender_id != current_user.id:
        viewed = MessageDeletion.query.filter_by(
            message_id=message.id, user_id=current_user.id
        ).first()
        if not viewed:
            db.session.add(MessageDeletion(message_id=message.id, user_id=current_user.id))
            db.session.commit()
    return jsonify({"ok": True})


@chat_bp.route("/chat/message/<int:message_id>/forward", methods=["POST"])
@login_required
def forward_message(message_id):
    original = Message.query.get_or_404(message_id)
    if not can_access_message(original):
        return jsonify({"error": "Not allowed."}), 403
    recipient_id = parse_user_id(request.form.get("recipient_id"))
    family_id = parse_user_id(request.form.get("family_id"))
    forwarded = Message(
        sender_id=current_user.id,
        content=original.content,
        media_url=original.media_url,
        media_type=original.media_type,
    )
    if family_id:
        membership = FamilyMember.query.filter_by(
            family_id=family_id, user_id=current_user.id
        ).first()
        if not membership:
            return jsonify({"error": "Join the family before forwarding there."}), 403
        forwarded.family_id = family_id
        room = f"family-{family_id}"
        realtime_recipients = [
            member.user_id
            for member in FamilyMember.query.filter_by(family_id=family_id).all()
        ]
    elif recipient_id:
        forwarded.recipient_id = recipient_id
        room = f"private-{min(current_user.id, recipient_id)}-{max(current_user.id, recipient_id)}"
        realtime_recipients = [current_user.id, recipient_id]
    else:
        return jsonify({"error": "Choose where to forward."}), 400
    db.session.add(forwarded)
    db.session.flush()
    for attachment in original.attachments:
        db.session.add(MessageAttachment(
            message_id=forwarded.id,
            media_url=attachment.media_url,
            media_type=attachment.media_type,
            position=attachment.position,
        ))
    db.session.commit()
    payload = serialize_message(forwarded)
    emit_chat_message(
        forwarded,
        room,
        "new_family_message" if forwarded.family_id else "new_private_message",
        realtime_recipients,
    )
    return jsonify(payload)


@chat_bp.route("/chat/message/<int:message_id>/pin", methods=["POST"])
@login_required
def pin_message(message_id):
    message = Message.query.get_or_404(message_id)
    if not can_access_message(message):
        return jsonify({"error": "Not allowed."}), 403
    if message.pinned_until and message.pinned_until > datetime.utcnow():
        message.pinned_until = None
        db.session.commit()
        return jsonify({"ok": True, "pinned": False})
    if message.family_id:
        active_pins = Message.query.filter(
            Message.family_id == message.family_id,
            Message.id != message.id,
            Message.pinned_until > datetime.utcnow(),
        ).order_by(Message.pinned_until.asc()).all()
        limit = pinned_announcement_limit(message.family_id) if is_feature_enabled("family_upgrades") else 1
        if len(active_pins) >= limit:
            active_pins[0].pinned_until = None
    else:
        Message.query.filter(
            Message.id != message.id,
            (
                ((Message.sender_id == message.sender_id) & (Message.recipient_id == message.recipient_id))
                | ((Message.sender_id == message.recipient_id) & (Message.recipient_id == message.sender_id))
            ),
        ).update({"pinned_until": None}, synchronize_session=False)
    message.pinned_until = datetime.utcnow() + timedelta(hours=24)
    db.session.commit()
    return jsonify({"ok": True, "pinned": True, "pinned_until": message.pinned_until.isoformat()})


@socketio.on("connect")
def on_connect():
    if not current_user.is_authenticated:
        current_app.logger.warning(
            "socket_event %s",
            {
                "event": "connect_rejected",
                "socket_id": request.sid,
                "namespace": getattr(request, "namespace", "/"),
                "transport": socket_transport(),
                "reason": "unauthenticated",
            },
        )
        return False
    was_offline = not user_is_online(current_user.id)
    connected_users.setdefault(current_user.id, set()).add(request.sid)
    join_room(user_room(current_user.id))
    log_socket_event(
        "connect",
        user_room=user_room(current_user.id),
        active_sockets=len(connected_users.get(current_user.id, set())),
    )
    emit(
        "socket_connected",
        {
            "user_id": current_user.id,
            "socket_id": request.sid,
            "namespace": getattr(request, "namespace", "/"),
            "transport": socket_transport(),
            "async_mode": socketio.async_mode,
        },
        room=request.sid,
    )
    if was_offline:
        emit(
            "user_status", {"user_id": current_user.id, "status": "online"}, broadcast=True
        )


@socketio.on("disconnect")
def on_disconnect(reason=None):
    if current_user.is_authenticated:
        open_chat_rooms.pop(request.sid, None)
        leave_family_voice_room(request.sid)
        log_socket_event("disconnect", reason=reason)
        stale_live_sessions = [
            session_id
            for session_id, sid in live_broadcasters.items()
            if sid == request.sid
        ]
        for session_id in stale_live_sessions:
            live_broadcasters.pop(session_id, None)
            session = LiveSession.query.get(session_id)
            if session and session.status == "live":
                session.status = "ended"
                session.ended_at = datetime.utcnow()
                db.session.commit()
            log_live_signal(
                "server_live_host_disconnected",
                session_id=session_id,
                broadcaster_id=current_user.id,
                room_id=f"live-{session_id}",
            )
            socketio.emit(
                "live_host_left",
                {"session_id": session_id},
                room=f"live-{session_id}",
            )
        for session_id, viewer_sids in list(live_viewers.items()):
            if request.sid in viewer_sids:
                viewer_sids.discard(request.sid)
                socketio.emit(
                    "live_viewer_count",
                    {"session_id": session_id, "count": len(viewer_sids)},
                    room=f"live-{session_id}",
                )
            if not viewer_sids:
                live_viewers.pop(session_id, None)
        user_sids = connected_users.get(current_user.id)
        if user_sids:
            user_sids.discard(request.sid)
            if not user_sids:
                connected_users.pop(current_user.id, None)
                emit(
                    "user_status",
                    {"user_id": current_user.id, "status": "offline"},
                    broadcast=True,
                )


@socketio.on("join_room")
def on_join_room(data):
    room = (data or {}).get("room", "")
    allowed = False
    if room.startswith("private-"):
        parts = room.split("-")
        if len(parts) == 3:
            first_id = parse_user_id(parts[1])
            second_id = parse_user_id(parts[2])
            allowed = bool(
                first_id
                and second_id
                and current_user.id in {first_id, second_id}
                and room == room_for_private_chat(first_id, second_id)
                and users_can_message(first_id, second_id)
            )
    elif room.startswith("family-"):
        family_id = parse_user_id(room.removeprefix("family-"))
        allowed = bool(
            family_id
            and FamilyMember.query.filter_by(
                family_id=family_id, user_id=current_user.id
            ).first()
        )
    if not allowed:
        current_app.logger.warning(
            "socket_room_join_denied user_id=%s room=%s", current_user.id, room
        )
        emit("room_join_denied", {"room": room}, room=request.sid)
        return
    join_room(room)
    open_chat_rooms[request.sid] = room
    log_socket_event("join_room", room=room)
    emit("room_joined", {"room": room}, room=request.sid)


def leave_family_voice_room(sid):
    for family_id, participants in list(family_voice_participants.items()):
        participant = participants.pop(sid, None)
        if not participant:
            continue
        socketio.emit(
            "family_voice_participant_left",
            {"socket_id": sid, "user_id": participant["user_id"]},
            room=f"family-voice-{family_id}",
        )
        socketio.emit(
            "family_voice_presence",
            {"participants": [
                {"socket_id": socket_id, **row}
                for socket_id, row in participants.items()
            ]},
            room=f"family-voice-{family_id}",
        )
        if not participants:
            family_voice_participants.pop(family_id, None)
        break


@socketio.on("join_family_voice")
def join_family_voice(data):
    family_id = parse_user_id((data or {}).get("family_id"))
    membership = (
        FamilyMember.query.filter_by(family_id=family_id, user_id=current_user.id).first()
        if family_id
        else None
    )
    if not membership:
        emit("family_voice_error", {"message": "Only Family members can join this voice room."}, room=request.sid)
        return
    participants = family_voice_participants.setdefault(family_id, {})
    if request.sid not in participants and len(participants) >= FAMILY_VOICE_ROOM_LIMIT:
        emit(
            "family_voice_error",
            {"message": f"This voice room is full ({FAMILY_VOICE_ROOM_LIMIT} devices)."},
            room=request.sid,
        )
        return
    existing = [
        {"socket_id": sid, **participant}
        for sid, participant in participants.items()
        if sid != request.sid
    ]
    participants[request.sid] = {
        "user_id": current_user.id,
        "username": current_user.username,
    }
    room = f"family-voice-{family_id}"
    join_room(room)
    emit(
        "family_voice_joined",
        {"family_id": family_id, "participants": existing, "socket_id": request.sid},
        room=request.sid,
    )
    emit(
        "family_voice_participant_joined",
        {"socket_id": request.sid, "user_id": current_user.id, "username": current_user.username},
        room=room,
        include_self=False,
    )
    socketio.emit(
        "family_voice_presence",
        {"participants": [
            {"socket_id": socket_id, **participant}
            for socket_id, participant in participants.items()
        ]},
        room=room,
    )


@socketio.on("family_voice_signal")
def family_voice_signal(data):
    payload = data or {}
    family_id = parse_user_id(payload.get("family_id"))
    target_sid = str(payload.get("target_socket_id") or "")
    signal = payload.get("signal")
    participants = family_voice_participants.get(family_id, {}) if family_id else {}
    if request.sid not in participants or target_sid not in participants or not isinstance(signal, dict):
        return
    emit(
        "family_voice_signal",
        {
            "source_socket_id": request.sid,
            "user_id": current_user.id,
            "username": current_user.username,
            "signal": signal,
        },
        room=target_sid,
    )


@socketio.on("leave_family_voice")
def leave_family_voice(data=None):
    family_id = parse_user_id((data or {}).get("family_id"))
    room = f"family-voice-{family_id}" if family_id else None
    leave_family_voice_room(request.sid)
    if room:
        leave_room(room)


@socketio.on("private_message")
def private_message(data):
    recipient_id = data.get("recipient_id")
    content = data.get("content", "").strip()
    reply_to_id = parse_user_id(data.get("reply_to_id"))
    if not recipient_id or not content:
        return
    try:
        recipient_id = int(recipient_id)
    except (TypeError, ValueError):
        return
    recipient = User.query.get(recipient_id)
    if not recipient:
        return
    if not users_can_message(current_user.id, recipient_id):
        return
    room = room_for_private_chat(current_user.id, recipient_id)
    message = Message(
        sender_id=current_user.id,
        recipient_id=recipient_id,
        content=content,
        reply_to_id=reply_to_id,
    )
    recipient_is_reading = user_has_open_chat(recipient_id, room)
    if recipient_is_reading:
        message.delivered = True
        message.read_at = datetime.utcnow()
    db.session.add(message)
    db.session.commit()
    emit_chat_message(message, room, "new_private_message", [current_user.id, recipient_id])
    if not recipient_is_reading:
        preview = " ".join(content.split())[:100]
        notification, _ = smart_notify(
            user_id=recipient_id, category="message",
            message=f"{current_user.username}: ‘{preview}{'…' if len(content) > 100 else ''}’",
            action_url=url_for("chat.direct_chat", user_id=current_user.id) + f"#message-{message.id}",
            group_key=f"message:{current_user.id}:direct",
            dedupe_key=f"message:{message.id}:{recipient_id}",
            push=False,
        )
        db.session.commit()
        if notification:
            queue_device_push(notification.id)


@socketio.on("mark_messages_delivered")
def mark_messages_delivered(data):
    sender_id = parse_user_id(data.get("sender_id"))
    if not sender_id:
        return
    mark_private_messages_delivered(sender_id, current_user.id)


@socketio.on("mark_messages_read")
def mark_messages_read(data):
    sender_id = parse_user_id(data.get("sender_id"))
    if not sender_id:
        return
    mark_private_messages_read(sender_id, current_user.id)


@socketio.on("chat_typing")
def chat_typing(data):
    payload = data or {}
    family_id = parse_user_id(payload.get("family_id"))
    recipient_id = parse_user_id(payload.get("recipient_id"))
    if family_id:
        if not FamilyMember.query.filter_by(family_id=family_id, user_id=current_user.id).first():
            return
        room = f"family-{family_id}"
    elif recipient_id and users_can_message(current_user.id, recipient_id):
        room = room_for_private_chat(current_user.id, recipient_id)
    else:
        return
    emit(
        "chat_typing",
        {"user_id": current_user.id, "username": current_user.username, "is_typing": bool(payload.get("is_typing")), "family_id": family_id},
        room=room,
        include_self=False,
    )


@socketio.on("family_message")
def family_message(data):
    family_id = data.get("family_id")
    content = data.get("content", "").strip()
    reply_to_id = parse_user_id(data.get("reply_to_id"))
    if not family_id or not content:
        return
    family = Family.query.get(family_id)
    if not family:
        return
    membership = FamilyMember.query.filter_by(
        family_id=family.id, user_id=current_user.id
    ).first()
    if not membership:
        return
    if has_active_family_restriction(family.id, current_user.id, "mute", "suspend"):
        return
    message = Message(
        sender_id=current_user.id,
        family_id=family.id,
        content=content,
        reply_to_id=reply_to_id,
    )
    db.session.add(message)
    db.session.commit()
    room = f"family-{family.id}"
    emit_chat_message(
        message,
        room,
        "new_family_message",
        [member.user_id for member in family.members],
    )
    notification_ids = []
    for member in family.members:
        if member.user_id != current_user.id and not user_has_open_chat(member.user_id, room):
            preview = " ".join(content.split())[:100]
            notification, _ = smart_notify(
                user_id=member.user_id, category="family_chat",
                message=f"{current_user.username} in {family.name}: ‘{preview}{'…' if len(content) > 100 else ''}’",
                action_url=url_for("chat.family_chat", family_id=family.id) + f"#message-{message.id}",
                group_key=f"message:{current_user.id}:family:{family.id}",
                dedupe_key=f"family-message:{message.id}:{member.user_id}",
                push=False,
            )
            if notification:
                notification_ids.append(notification.id)
    db.session.commit()
    for notification_id in notification_ids:
        queue_device_push(notification_id)


def webrtc_offer(data):
    target_id = parse_user_id(data.get("target_id"))
    offer = data.get("offer")
    if not target_id or not offer:
        return
    call_id = data.get("call_id") or call_id_for_users(current_user.id, target_id)
    room_id = data.get("room_id") or call_room_for_id(call_id)
    call = get_call_state(call_id) or {}
    set_call_state(
        call_id,
        "connecting",
        caller_id=call.get("caller_id") or current_user.id,
        receiver_id=call.get("receiver_id") or target_id,
        room_id=room_id,
    )
    log_call_signal(
        "server_received_webrtc_offer",
        call_id,
        room_id=room_id,
        target_id=target_id,
        sdp_type=offer.get("type") if isinstance(offer, dict) else None,
    )
    emit(
        "webrtc_offer",
        {
            "sender_id": current_user.id,
            "offer": offer,
            "call_id": call_id,
            "room_id": room_id,
        },
        room=room_id,
        include_self=False,
    )
    log_call_signal("server_forwarded_webrtc_offer", call_id, room_id=room_id, target_id=target_id)


def webrtc_answer(data):
    target_id = parse_user_id(data.get("target_id"))
    answer = data.get("answer")
    if not target_id or not answer:
        return
    call_id = data.get("call_id") or call_id_for_users(current_user.id, target_id)
    room_id = data.get("room_id") or call_room_for_id(call_id)
    call = get_call_state(call_id) or {}
    set_call_state(
        call_id,
        "connecting",
        caller_id=call.get("caller_id") or target_id,
        receiver_id=call.get("receiver_id") or current_user.id,
        room_id=room_id,
    )
    log_call_signal(
        "server_received_webrtc_answer",
        call_id,
        room_id=room_id,
        target_id=target_id,
        sdp_type=answer.get("type") if isinstance(answer, dict) else None,
    )
    emit(
        "webrtc_answer",
        {
            "sender_id": current_user.id,
            "answer": answer,
            "call_id": call_id,
            "room_id": room_id,
        },
        room=room_id,
        include_self=False,
    )
    log_call_signal("server_forwarded_webrtc_answer", call_id, room_id=room_id, target_id=target_id)


def ice_candidate(data):
    target_id = parse_user_id(data.get("target_id"))
    candidate = data.get("candidate")
    if not target_id or not candidate:
        return
    call_id = data.get("call_id") or call_id_for_users(current_user.id, target_id)
    room_id = data.get("room_id") or call_room_for_id(call_id)
    log_call_signal(
        "server_received_ice_candidate",
        call_id,
        room_id=room_id,
        target_id=target_id,
        candidate_type=candidate.get("type") if isinstance(candidate, dict) else None,
        candidate_mid=candidate.get("sdpMid") if isinstance(candidate, dict) else None,
    )
    emit(
        "ice_candidate",
        {
            "sender_id": current_user.id,
            "candidate": candidate,
            "call_id": call_id,
            "room_id": room_id,
        },
        room=room_id,
        include_self=False,
    )
    log_call_signal("server_forwarded_ice_candidate", call_id, room_id=room_id, target_id=target_id)


def call_invite(data):
    target_id = parse_user_id(data.get("target_id"))
    mode = data.get("mode", "video")
    if not target_id:
        return
    call_id = data.get("call_id") or call_id_for_users(current_user.id, target_id)
    room_id = data.get("room_id") or call_room_for_id(call_id)
    target = User.query.get(target_id)
    if not target:
        return
    join_room(room_id)
    set_call_state(
        call_id,
        "ringing",
        caller_id=current_user.id,
        receiver_id=target_id,
        room_id=room_id,
        mode=mode,
    )
    emit("call_room_joined", {"call_id": call_id, "room_id": room_id}, room=request.sid)
    log_call_signal("caller_joined_call_room", call_id, room_id=room_id, target_id=target_id)
    if not user_is_online(target_id):
        create_call_history(current_user.id, target_id, mode, "missed")
        smart_notify(
            user_id=target_id, category="message",
            message=f"Missed {mode} call from {current_user.username}.",
            action_url=url_for("chat.direct_chat", user_id=current_user.id),
            group_key=f"message:{current_user.id}:direct",
            dedupe_key=f"missed-call:{call_id}:{target_id}",
        )
        db.session.commit()
        emit("call_unavailable", {"target_id": target_id}, room=request.sid)
        log_call_signal("server_call_unavailable", call_id, room_id=room_id, target_id=target_id)
        clear_call_state(call_id)
        return
    create_call_history(current_user.id, target_id, mode, "started")
    emit(
        "incoming_call",
        {
            "sender_id": current_user.id,
            "sender_name": current_user.profile.display_name,
            "recipient_id": target_id,
            "mode": mode,
            "call_id": call_id,
            "room_id": room_id,
        },
        room=user_room(target_id),
    )
    emit(
        "call_invite_sent",
        {
            "target_id": target_id,
            "call_id": call_id,
            "room_id": room_id,
            "state": "ringing",
        },
        room=request.sid,
    )
    socketio.start_background_task(
        timeout_call_if_unanswered,
        current_app._get_current_object(),
        call_id,
    )
    log_call_signal("server_sent_incoming_call", call_id, room_id=room_id, target_id=target_id)


def call_accepted(data):
    target_id = parse_user_id(data.get("target_id"))
    if not target_id:
        return
    call_id = data.get("call_id") or call_id_for_users(current_user.id, target_id)
    room_id = data.get("room_id") or call_room_for_id(call_id)
    join_room(room_id)
    call = get_call_state(call_id) or {}
    set_call_state(
        call_id,
        "accepted",
        caller_id=call.get("caller_id") or target_id,
        receiver_id=call.get("receiver_id") or current_user.id,
        room_id=room_id,
        mode=call.get("mode") or data.get("mode", "video"),
    )
    emit("call_room_joined", {"call_id": call_id, "room_id": room_id}, room=request.sid)
    log_call_signal("receiver_joined_call_room", call_id, room_id=room_id, target_id=target_id)
    log_call_signal("server_received_call_acceptance", call_id, room_id=room_id, target_id=target_id)
    if user_is_online(target_id):
        emit(
            "peer_ready",
            {"sender_id": current_user.id, "call_id": call_id, "room_id": room_id},
            room=room_id,
            include_self=False,
        )
        log_call_signal("server_sent_call_acceptance_to_caller", call_id, room_id=room_id, target_id=target_id)


def ready_for_call(data):
    call_accepted(data)


def call_ended(data):
    target_id = parse_user_id(data.get("target_id"))
    call_id = data.get("call_id") or (call_id_for_users(current_user.id, target_id) if target_id else None)
    room_id = data.get("room_id") or (call_room_for_id(call_id) if call_id else None)
    if target_id and User.query.get(target_id):
        create_call_history(current_user.id, target_id, data.get("mode", "video"), "ended")
    if call_id and room_id:
        set_call_state(call_id, "ended", room_id=room_id)
        log_call_signal("server_call_ended", call_id, room_id=room_id, target_id=target_id)
        payload = {
            "sender_id": current_user.id,
            "call_id": call_id,
            "room_id": room_id,
            "state": "ended",
        }
        emit("call_ended", payload, room=room_id, include_self=False)
        if user_is_online(target_id):
            emit("call_ended", payload, room=user_room(target_id))
        leave_room(room_id)
        clear_call_state(call_id)
    elif user_is_online(target_id):
        emit("call_ended", {"sender_id": current_user.id}, room=user_room(target_id))


def call_declined(data):
    target_id = parse_user_id(data.get("target_id"))
    call_id = data.get("call_id") or (call_id_for_users(current_user.id, target_id) if target_id else None)
    room_id = data.get("room_id") or (call_room_for_id(call_id) if call_id else None)
    if call_id and room_id:
        set_call_state(call_id, "rejected", room_id=room_id)
        log_call_signal("server_call_declined", call_id, room_id=room_id, target_id=target_id)
        payload = {
            "sender_id": current_user.id,
            "call_id": call_id,
            "room_id": room_id,
            "state": "rejected",
        }
        emit("call_rejected", payload, room=room_id, include_self=False)
        if user_is_online(target_id):
            emit("call_rejected", payload, room=user_room(target_id))
        clear_call_state(call_id)
    elif user_is_online(target_id):
        emit(
            "call_ended",
            {"sender_id": current_user.id, "declined": True},
            room=user_room(target_id),
        )


def leave_call(data):
    call_id = data.get("call_id")
    room_id = data.get("room_id") or (call_room_for_id(call_id) if call_id else None)
    target_id = parse_user_id(data.get("target_id"))
    if room_id:
        leave_room(room_id)
        log_call_signal("server_leave_call_room", call_id, room_id=room_id, target_id=target_id)


def join_live(data):
    session_id = parse_user_id(data.get("session_id"))
    role = data.get("role", "viewer")
    session = LiveSession.query.get(session_id) if session_id else None
    if not session or session.status != "live":
        emit("live_unavailable", {"session_id": session_id}, room=request.sid)
        log_live_signal("server_live_unavailable", session_id=session_id)
        return
    room = f"live-{session.id}"
    join_room(room)
    if role == "host" and session.user_id == current_user.id:
        live_broadcasters[session.id] = request.sid
        log_live_signal(
            "server_live_host_joined",
            session_id=session.id,
            broadcaster_id=current_user.id,
            room_id=room,
        )
        emit("live_status", {"status": "broadcasting"}, room=request.sid)
        emit(
            "live_viewer_count",
            {"session_id": session.id, "count": len(live_viewers.get(session.id, set()))},
            room=f"live-{session.id}",
        )
        emit("live_host_ready", {"session_id": session.id}, room=room, include_self=False)
        return
    live_viewers.setdefault(session.id, set()).add(request.sid)
    log_live_signal(
        "server_live_viewer_joined",
        session_id=session.id,
        broadcaster_id=session.user_id,
        viewer_id=current_user.id,
        room_id=room,
        viewer_count=len(live_viewers.get(session.id, set())),
    )
    emit(
        "live_viewer_count",
        {"session_id": session.id, "count": len(live_viewers.get(session.id, set()))},
        room=room,
    )
    host_sid = live_broadcasters.get(session.id)
    if host_sid:
        emit(
            "live_viewer_joined",
            {"session_id": session.id, "viewer_id": current_user.id, "viewer_sid": request.sid},
            room=host_sid,
        )
    else:
        emit("live_waiting_for_host", {"session_id": session.id}, room=request.sid)


def live_offer(data):
    viewer_sid = data.get("viewer_sid")
    offer = data.get("offer")
    session_id = parse_user_id(data.get("session_id"))
    session = LiveSession.query.get(session_id) if session_id else None
    if viewer_sid and offer and session and session.user_id == current_user.id:
        log_live_signal(
            "server_live_offer",
            session_id=session.id,
            broadcaster_id=current_user.id,
            room_id=f"live-{session.id}",
            target_sid=viewer_sid,
        )
        emit(
            "live_offer",
            {
                "session_id": session.id,
                "offer": offer,
                "host_name": current_user.profile.display_name,
                "sender_sid": request.sid,
            },
            room=viewer_sid,
        )


def live_answer(data):
    session_id = parse_user_id(data.get("session_id"))
    answer = data.get("answer")
    session = LiveSession.query.get(session_id) if session_id else None
    host_sid = live_broadcasters.get(session_id)
    if answer and session and host_sid:
        log_live_signal(
            "server_live_answer",
            session_id=session.id,
            broadcaster_id=session.user_id,
            viewer_id=current_user.id,
            room_id=f"live-{session.id}",
        )
        emit(
            "live_answer",
            {"session_id": session.id, "answer": answer, "viewer_sid": request.sid},
            room=host_sid,
        )


def live_ice_candidate(data):
    session_id = parse_user_id(data.get("session_id"))
    candidate = data.get("candidate")
    target_sid = data.get("target_sid")
    session = LiveSession.query.get(session_id) if session_id else None
    if candidate and target_sid and session:
        log_live_signal(
            "server_live_ice_candidate",
            session_id=session.id,
            broadcaster_id=session.user_id,
            viewer_id=current_user.id if session.user_id != current_user.id else None,
            room_id=f"live-{session.id}",
            target_sid=target_sid,
        )
        emit(
            "live_ice_candidate",
            {"session_id": session.id, "candidate": candidate, "sender_sid": request.sid},
            room=target_sid,
        )


def live_comment(data):
    session_id = parse_user_id(data.get("session_id"))
    content = (data.get("content") or "").strip()
    session = LiveSession.query.get(session_id) if session_id else None
    if not session or session.status != "live" or not content:
        return
    content = content[:500]
    room = f"live-{session.id}"
    payload = {
        "session_id": session.id,
        "sender_id": current_user.id,
        "sender_name": current_user.profile.display_name,
        "content": content,
        "created_at": datetime.utcnow().strftime("%H:%M"),
    }
    log_live_signal(
        "server_live_comment",
        session_id=session.id,
        broadcaster_id=session.user_id,
        viewer_id=current_user.id if current_user.id != session.user_id else None,
        room_id=room,
    )
    emit("live_comment", payload, room=room)

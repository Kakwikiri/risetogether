from datetime import datetime, timedelta
from threading import Lock
from urllib.parse import parse_qs

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_socketio import emit, join_room, leave_room

from extensions import db, socketio
from helpers import get_ice_servers, get_media_type, save_media, send_device_push, validate_upload
from models import Family, FamilyMember, FamilyMemberRestriction, LiveSession, Message, MessageDeletion, Notification, User

chat_bp = Blueprint("chat", __name__)
connected_users = {}
live_broadcasters = {}
live_viewers = {}
active_calls = {}
active_calls_lock = Lock()
CALL_TIMEOUT_SECONDS = 45


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
    return {
        "sender_id": message.sender_id,
        "sender_name": message.sender.username,
        "recipient_id": message.recipient_id,
        "family_id": message.family_id,
        "content": message.content,
        "media_url": (
            url_for("api.serve_upload", filename=message.media_url)
            if message.media_url
            else ""
        ),
        "media_type": message.media_type,
        "message_id": message.id,
        "reply_to_id": message.reply_to_id,
        "view_once": message.view_once,
        "delivered": message.delivered,
        "created_at": message.created_at.strftime("%Y-%m-%d %H:%M"),
    }


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
    socketio.emit("new_private_message", serialize_message(message), room=room)
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
            conversations.append({"user": other, "message": message})
    families = [
        membership.family
        for membership in current_user.family_memberships
    ]
    return render_template(
        "messages.html", conversations=conversations, families=families
    )


@chat_bp.route("/chat/<int:user_id>")
@login_required
def direct_chat(user_id):
    other = User.query.get_or_404(user_id)
    if other.id == current_user.id:
        return redirect(url_for("main.home"))
    messages = (
        visible_message_filter(
            Message.query.filter(
            (
                (Message.sender_id == current_user.id)
                & (Message.recipient_id == other.id)
            )
            | (
                (Message.sender_id == other.id)
                & (Message.recipient_id == current_user.id)
            )
        )
        )
        .order_by(Message.created_at.asc())
        .all()
    )
    Message.query.filter_by(
        sender_id=other.id, recipient_id=current_user.id, delivered=False
    ).update({"delivered": True})
    Notification.query.filter(
        Notification.user_id == current_user.id,
        Notification.category.in_(["message", "call"]),
        Notification.action_url == url_for("chat.direct_chat", user_id=other.id),
        Notification.seen == False,
    ).update({"seen": True})
    db.session.commit()
    return render_template(
        "chat.html",
        other=other,
        messages=messages,
        family=None,
        is_other_online=user_is_online(other.id),
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
    messages = (
        visible_message_filter(Message.query.filter_by(family_id=family.id))
        .order_by(Message.created_at.asc())
        .all()
    )
    Notification.query.filter_by(
        user_id=current_user.id,
        category="family_chat",
        action_url=url_for("chat.family_chat", family_id=family.id),
        seen=False,
    ).update({"seen": True})
    db.session.commit()
    return render_template("chat.html", other=None, messages=messages, family=family)


@chat_bp.route("/calls/<int:user_id>")
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
    media_file = request.files.get("file")
    content = request.form.get("content", "").strip()
    reply_to_id = parse_user_id(request.form.get("reply_to_id"))
    view_once = request.form.get("view_once") == "1"
    expires_in = request.form.get("expires_in")
    media_kind = request.form.get("media_kind", "").strip()
    recipient_id = request.form.get("recipient_id")
    family_id = request.form.get("family_id")
    if media_kind == "audio":
        extension = media_file.filename.rsplit(".", 1)[1].lower() if media_file and "." in media_file.filename else ""
        if extension not in {"webm", "ogg", "mp3", "wav", "m4a"}:
            return jsonify({"error": "Voice notes must be uploaded as an audio recording."}), 400
    is_valid, upload_message = validate_upload(media_file)
    if not is_valid:
        return jsonify({"error": upload_message}), 400
    filename = save_media(media_file)
    if not filename:
        return jsonify({"error": "Upload failed."}), 400
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
            return jsonify({"error": "Invalid family."}), 400
        family = Family.query.get(family_id)
        membership = (
            FamilyMember.query.filter_by(family_id=family.id, user_id=current_user.id).first()
            if family
            else None
        )
        if not family or not membership:
            return jsonify({"error": "Join this family before sending files."}), 403
        if has_active_family_restriction(family.id, current_user.id, "mute", "suspend"):
            return jsonify({"error": "You are temporarily restricted from sending Family messages."}), 403
        message.family_id = family.id
        room = f"family-{family.id}"
        recipients = [member.user_id for member in family.members if member.user_id != current_user.id]
    elif recipient_id:
        try:
            recipient_id = int(recipient_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid recipient."}), 400
        recipient = User.query.get(recipient_id)
        if not recipient:
            return jsonify({"error": "Recipient not found."}), 404
        message.recipient_id = recipient.id
        room = room_for_private_chat(current_user.id, recipient.id)
        recipients = [recipient.id]
    else:
        return jsonify({"error": "Choose a chat first."}), 400
    db.session.add(message)
    db.session.commit()
    for user_id in recipients:
        notification = Notification(
            user_id=user_id,
            category="video_note" if media_type == "video" else "voice_note" if media_type == "audio" else "message",
            message=f"New file from {current_user.username}",
            action_url=(
                url_for("chat.family_chat", family_id=message.family_id)
                if message.family_id
                else url_for("chat.direct_chat", user_id=current_user.id)
            ),
        )
        db.session.add(notification)
        db.session.flush()
        send_device_push(notification)
    db.session.commit()
    payload = serialize_message(message)
    socketio.emit(
        "new_family_message" if message.family_id else "new_private_message",
        payload,
        room=room,
    )
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
        db.session.delete(message)
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


@chat_bp.route("/chat/message/<int:message_id>/viewed", methods=["POST"])
@login_required
def viewed_once_message(message_id):
    message = Message.query.get_or_404(message_id)
    if not can_access_message(message):
        return jsonify({"error": "Not allowed."}), 403
    if message.view_once and message.sender_id != current_user.id:
        room = (
            f"family-{message.family_id}"
            if message.family_id
            else f"private-{min(message.sender_id, message.recipient_id)}-{max(message.sender_id, message.recipient_id)}"
        )
        db.session.delete(message)
        db.session.commit()
        socketio.emit("message_deleted", {"message_id": message_id}, room=room)
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
    elif recipient_id:
        forwarded.recipient_id = recipient_id
        room = f"private-{min(current_user.id, recipient_id)}-{max(current_user.id, recipient_id)}"
    else:
        return jsonify({"error": "Choose where to forward."}), 400
    db.session.add(forwarded)
    db.session.commit()
    payload = {
        "message_id": forwarded.id,
        "sender_id": current_user.id,
        "sender_name": current_user.username,
        "recipient_id": forwarded.recipient_id,
        "family_id": forwarded.family_id,
        "content": forwarded.content,
        "media_url": url_for("api.serve_upload", filename=forwarded.media_url) if forwarded.media_url else "",
        "media_type": forwarded.media_type,
        "created_at": forwarded.created_at.strftime("%Y-%m-%d %H:%M"),
    }
    socketio.emit(
        "new_family_message" if forwarded.family_id else "new_private_message",
        payload,
        room=room,
    )
    return jsonify(payload)


@chat_bp.route("/chat/message/<int:message_id>/pin", methods=["POST"])
@login_required
def pin_message(message_id):
    message = Message.query.get_or_404(message_id)
    if not can_access_message(message):
        return jsonify({"error": "Not allowed."}), 403
    message.pinned_until = datetime.utcnow() + timedelta(hours=24)
    db.session.commit()
    return jsonify({"ok": True, "pinned_until": message.pinned_until.isoformat()})


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
    room = data.get("room")
    if room:
        join_room(room)
        log_socket_event("join_room", room=room)
        emit("room_joined", {"room": room}, room=request.sid)


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
    room = room_for_private_chat(current_user.id, recipient_id)
    message = Message(
        sender_id=current_user.id,
        recipient_id=recipient_id,
        content=content,
        reply_to_id=reply_to_id,
    )
    db.session.add(message)
    db.session.commit()
    notification = Notification(
        user_id=recipient_id,
        category="message",
        message=f"New message from {current_user.username}",
        action_url=url_for("chat.direct_chat", user_id=current_user.id),
    )
    db.session.add(notification)
    db.session.flush()
    send_device_push(notification)
    db.session.commit()
    emit_notification(recipient_id, notification)
    emit(
        "new_private_message",
        {
            **serialize_message(message),
        },
        room=room,
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
    for member in family.members:
        if member.user_id != current_user.id:
            notification = Notification(
                user_id=member.user_id,
                category="family_chat",
                message=f"New family chat message in {family.name}",
                action_url=url_for("chat.family_chat", family_id=family.id),
            )
            db.session.add(notification)
            db.session.flush()
            emit_notification(member.user_id, notification)
            send_device_push(notification)
    db.session.commit()
    room = f"family-{family.id}"
    emit(
        "new_family_message",
        {
            **serialize_message(message),
        },
        room=room,
    )


@socketio.on("webrtc_offer")
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


@socketio.on("webrtc_answer")
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


@socketio.on("ice_candidate")
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


@socketio.on("call_invite")
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
        db.session.add(
            Notification(
                user_id=target_id,
                category="call",
                message=f"Missed {mode} call from {current_user.username}",
                action_url=url_for("chat.direct_chat", user_id=current_user.id),
            )
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


@socketio.on("call_accepted")
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


@socketio.on("ready_for_call")
def ready_for_call(data):
    call_accepted(data)


@socketio.on("call_ended")
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


@socketio.on("call_declined")
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


@socketio.on("leave_call")
def leave_call(data):
    call_id = data.get("call_id")
    room_id = data.get("room_id") or (call_room_for_id(call_id) if call_id else None)
    target_id = parse_user_id(data.get("target_id"))
    if room_id:
        leave_room(room_id)
        log_call_signal("server_leave_call_room", call_id, room_id=room_id, target_id=target_id)


@socketio.on("join_live")
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


@socketio.on("live_offer")
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


@socketio.on("live_answer")
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


@socketio.on("live_ice_candidate")
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


@socketio.on("live_comment")
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

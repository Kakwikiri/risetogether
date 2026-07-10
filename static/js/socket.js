const socket = io();

const appendChatMessage = (chatLog, data, isOwn) => {
  if (!chatLog) return;
  if (data.message_id && chatLog.querySelector(`[data-message-id="${data.message_id}"]`)) {
    return;
  }
  const message = document.createElement("div");
  message.className = `chat-message${isOwn ? " own" : ""}`;
  if (data.message_id) {
    message.dataset.messageId = data.message_id;
  }
  message.dataset.messageText = data.content || "";

  const user = document.createElement("span");
  user.className = "chat-user";
  user.textContent = isOwn ? "You" : data.sender_name || `User ${data.sender_id}`;

  const body = document.createElement("p");
  body.textContent = data.media_type === "call" ? "" : data.content || "";

  let media = null;
  let downloadLink = null;
  if (data.media_url) {
    if (data.media_type === "image") {
      media = document.createElement("div");
      media.className = `media-frame${data.view_once ? " view-once-media" : ""}`;
      media.dataset.messageId = data.message_id || "";
      media.dataset.viewOnce = data.view_once ? "1" : "0";
      const img = document.createElement("img");
      img.alt = "Shared image";
      img.src = data.media_url;
      media.appendChild(img);
    } else if (data.media_type === "video") {
      media = document.createElement("div");
      media.className = `media-frame${data.view_once ? " view-once-media" : ""}`;
      media.dataset.messageId = data.message_id || "";
      media.dataset.viewOnce = data.view_once ? "1" : "0";
      const video = document.createElement("video");
      video.controls = true;
      if (data.view_once) {
        video.setAttribute("controlsList", "nodownload");
        video.disablePictureInPicture = true;
      }
      video.src = data.media_url;
      media.appendChild(video);
    } else if (data.media_type === "audio") {
      media = document.createElement("audio");
      media.controls = true;
      media.src = data.media_url;
    } else {
      media = document.createElement("a");
      media.className = "chat-file";
      media.href = data.media_url;
      media.target = "_blank";
      media.rel = "noopener";
      media.download = "";
      media.textContent = "Download file";
    }
    if (data.media_type !== "file" && !data.view_once) {
      downloadLink = document.createElement("a");
      downloadLink.className = "media-download";
      downloadLink.href = data.media_url;
      downloadLink.download = "";
      downloadLink.textContent = "↓";
      downloadLink.setAttribute("aria-label", `Download ${data.media_type}`);
      if (media && media.classList.contains("media-frame")) {
        media.appendChild(downloadLink);
        downloadLink = null;
      }
    }
  }

  const time = document.createElement("small");
  time.className = "message-meta";
  time.textContent = data.created_at || "";
  if (isOwn) {
    const ticks = document.createElement("span");
    ticks.className = "message-ticks";
    ticks.textContent = data.delivered ? "✓✓" : "✓";
    time.append(" ", ticks);
  }

  if (typeof chatConfig !== "undefined" && chatConfig.familyId && !isOwn) {
    message.appendChild(user);
  }
  if (body.textContent) {
    message.appendChild(body);
  }
  if (media) message.appendChild(media);
  if (data.media_type === "call") {
    const callPill = document.createElement("span");
    callPill.className = "call-history-pill";
    callPill.textContent = data.content || "Call";
    message.appendChild(callPill);
  }
  if (downloadLink) message.appendChild(downloadLink);
  message.appendChild(time);
  chatLog.appendChild(message);
  chatLog.scrollTop = chatLog.scrollHeight;
};

socket.on("connect", () => {
  console.log("Connected to socket server");
});

socket.on("user_status", (data) => {
  console.log("User status", data);
  document.querySelectorAll(`[data-presence-user="${data.user_id}"]`).forEach((dot) => {
    dot.classList.toggle("online", data.status === "online");
    dot.classList.toggle("offline", data.status !== "online");
  });
  if (typeof chatConfig !== "undefined" && data.user_id === chatConfig.targetUserId) {
    const label = document.querySelector("[data-presence-label]");
    if (label) label.textContent = data.status === "online" ? "Online" : "Offline";
  }
});

socket.on("new_private_message", (data) => {
  console.log("Private message", data);
});

socket.on("new_family_message", (data) => {
  console.log("Family message", data);
});

socket.on("room_joined", (data) => {
  console.log("Joined room", data);
});

if (typeof chatConfig !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => {
    const chatForm = document.getElementById("chat-form");
    const chatInput = document.getElementById("chat-input");
    const chatLog = document.getElementById("chat-log");
    const chatFile = document.getElementById("chat-file");
    const filePreview = document.getElementById("chat-file-preview");
    const locationButton = document.getElementById("location-button");
    const replyPreview = document.getElementById("reply-preview");
    const viewOnceInput = document.getElementById("view-once");
    const expireInput = document.getElementById("expire-one-minute");
    let replyToId = null;
    let longPressTimer = null;
    const actionMenu = document.createElement("div");
    actionMenu.className = "message-action-menu";
    actionMenu.hidden = true;
    document.body.appendChild(actionMenu);

    const sendTextMessage = (content) => {
      if (chatConfig.familyId) {
        socket.emit("family_message", {
          family_id: chatConfig.familyId,
          content,
          reply_to_id: replyToId,
        });
      } else {
        socket.emit("private_message", {
          recipient_id: chatConfig.targetUserId,
          content,
          reply_to_id: replyToId,
        });
      }
    };

    const uploadChatFile = async (file, content = "") => {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("content", content);
      if (replyToId) formData.append("reply_to_id", replyToId);
      if (viewOnceInput && viewOnceInput.checked) formData.append("view_once", "1");
      if (expireInput && expireInput.checked) formData.append("expires_in", "60");
      if (chatConfig.familyId) {
        formData.append("family_id", chatConfig.familyId);
      } else {
        formData.append("recipient_id", chatConfig.targetUserId);
      }
      const response = await fetch("/chat/upload", {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        window.alert("File could not be sent.");
        return;
      }
      const data = await response.json();
      appendChatMessage(chatLog, data, data.sender_id === chatConfig.currentUserId);
      if (chatLog) {
        chatLog.scrollTop = chatLog.scrollHeight;
      }
    };

    const clearComposerState = () => {
      replyToId = null;
      if (replyPreview) {
        replyPreview.hidden = true;
        replyPreview.textContent = "";
      }
      if (filePreview) {
        filePreview.hidden = true;
        filePreview.innerHTML = "";
      }
      if (viewOnceInput) viewOnceInput.checked = false;
      if (expireInput) expireInput.checked = false;
    };

    const showMessageActions = (message) => {
      const messageId = message.dataset.messageId;
      if (!messageId) return;
      const rect = message.getBoundingClientRect();
      actionMenu.innerHTML = "";
      const actions = [
        ["reply", "Reply"],
        ["forward", "Forward"],
        ["pin", "Pin 24h"],
        ["delete", "Delete"],
      ];
      actions.forEach(([value, label]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.addEventListener("click", () => runMessageAction(value, message));
        actionMenu.appendChild(button);
      });
      actionMenu.style.left = `${Math.min(rect.left, window.innerWidth - 250)}px`;
      actionMenu.style.top = `${Math.max(70, rect.top - 48)}px`;
      actionMenu.hidden = false;
    };

    const runMessageAction = (action, message) => {
      const messageId = message.dataset.messageId;
      actionMenu.hidden = true;
      if (action === "reply") {
        replyToId = messageId;
        if (replyPreview) {
          replyPreview.textContent = `Replying to: ${message.dataset.messageText || "media"}`;
          replyPreview.hidden = false;
        }
        chatInput.focus();
      } else if (action === "delete") {
        fetch(`/chat/message/${messageId}/delete`, { method: "POST" });
      } else if (action === "pin") {
        fetch(`/chat/message/${messageId}/pin`, { method: "POST" }).then((response) => {
          if (response.ok) {
            message.classList.add("is-pinned");
          }
        });
      } else if (action === "forward") {
        const targetType = window.prompt("Forward to: user or family?", "user");
        const targetId = window.prompt(`Enter ${targetType === "family" ? "family" : "user"} id`);
        if (!targetId) return;
        const formData = new FormData();
        formData.append(targetType === "family" ? "family_id" : "recipient_id", targetId);
        fetch(`/chat/message/${messageId}/forward`, {
          method: "POST",
          body: formData,
        }).then((response) => {
          if (!response.ok) window.alert("Message could not be forwarded.");
        });
      }
    };

    if (chatForm) {
      const room = chatConfig.familyId
        ? `family-${chatConfig.familyId}`
        : `private-${Math.min(chatConfig.currentUserId, chatConfig.targetUserId)}-${Math.max(chatConfig.currentUserId, chatConfig.targetUserId)}`;
      socket.emit("join_room", { room });
      if (chatLog) {
        chatLog.scrollTop = chatLog.scrollHeight;
      }

      chatForm.addEventListener("submit", (event) => {
        event.preventDefault();
        const content = chatInput.value.trim();
        if (chatFile && chatFile.files && chatFile.files[0]) {
          uploadChatFile(chatFile.files[0], content);
          chatFile.value = "";
          chatInput.value = "";
          clearComposerState();
          return;
        }
        if (!content) return;
        sendTextMessage(content);
        chatInput.value = "";
        clearComposerState();
      });
    }

    if (chatInput && chatLog) {
      chatInput.addEventListener("focus", () => {
        window.setTimeout(() => {
          chatLog.scrollTop = chatLog.scrollHeight;
        }, 250);
      });
    }

    if (chatFile && filePreview) {
      chatFile.addEventListener("change", () => {
        filePreview.innerHTML = "";
        const file = chatFile.files && chatFile.files[0];
        if (!file) {
          filePreview.hidden = true;
          return;
        }
        filePreview.hidden = false;
        if (file.type.startsWith("image/")) {
          const img = document.createElement("img");
          img.src = URL.createObjectURL(file);
          img.alt = "Selected image";
          filePreview.appendChild(img);
        } else {
          const span = document.createElement("span");
          span.textContent = file.name;
          filePreview.appendChild(span);
        }
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.addEventListener("click", () => {
          chatFile.value = "";
          filePreview.hidden = true;
          filePreview.innerHTML = "";
        });
        filePreview.appendChild(remove);
      });
    }

    if (chatLog) {
      chatLog.addEventListener("contextmenu", (event) => {
        const message = event.target.closest(".chat-message");
        if (!message) return;
        event.preventDefault();
        showMessageActions(message);
      });
      chatLog.addEventListener("touchstart", (event) => {
        const message = event.target.closest(".chat-message");
        if (!message) return;
        longPressTimer = window.setTimeout(() => showMessageActions(message), 650);
      });
      chatLog.addEventListener("touchend", () => window.clearTimeout(longPressTimer));
      document.addEventListener("click", (event) => {
        if (!actionMenu.contains(event.target)) actionMenu.hidden = true;
      });
      chatLog.addEventListener("click", (event) => {
        const viewButton = event.target.closest(".view-once-button");
        if (!viewButton) return;
        const frame = viewButton.closest(".media-frame");
        const messageId = frame && frame.dataset.messageId;
        frame.classList.add("revealed");
        viewButton.remove();
        if (messageId) {
          window.setTimeout(() => {
            fetch(`/chat/message/${messageId}/viewed`, { method: "POST" });
          }, 8000);
        }
      });
    }

    if (locationButton) {
      locationButton.addEventListener("click", () => {
        if (!navigator.geolocation) {
          window.alert("Location sharing is not supported by this browser.");
          return;
        }
        navigator.geolocation.getCurrentPosition(
          (position) => {
            const { latitude, longitude } = position.coords;
            sendTextMessage(
              `My location: https://www.google.com/maps?q=${latitude},${longitude}`,
            );
          },
          () => window.alert("Could not get your location."),
          { enableHighAccuracy: true, timeout: 10000 },
        );
      });
    }

    socket.on("new_private_message", (data) => {
      if (!chatConfig.targetUserId) return;
      const belongsToChat =
        [data.sender_id, data.recipient_id].includes(chatConfig.currentUserId) &&
        [data.sender_id, data.recipient_id].includes(chatConfig.targetUserId);
      if (belongsToChat) {
        appendChatMessage(chatLog, data, data.sender_id === chatConfig.currentUserId);
      }
    });

    socket.on("message_deleted", (data) => {
      const item = chatLog && chatLog.querySelector(`[data-message-id="${data.message_id}"]`);
      if (item) item.remove();
    });

    socket.on("new_family_message", (data) => {
      if (data.family_id === chatConfig.familyId) {
        appendChatMessage(chatLog, data, data.sender_id === chatConfig.currentUserId);
      }
    });

    const callButton = document.getElementById("call-button");
    if (callButton) {
      callButton.addEventListener("click", () => {
        window.location.href = `/calls/${chatConfig.targetUserId}`;
      });
    }
  });
}

socket.on("incoming_call", (data) => {
  if (typeof callConfig !== "undefined") return;
  let overlay = document.querySelector(".incoming-call-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "incoming-call-overlay";
    document.body.appendChild(overlay);
  }
  overlay.innerHTML = `
    <div class="incoming-call-card">
      <span class="eyebrow">Incoming ${data.mode === "audio" ? "audio" : "video"} call</span>
      <h2>${data.sender_name}</h2>
      <p>RiseTogether call</p>
      <div class="incoming-call-actions">
        <button class="call-control end-call" data-decline-call type="button">Decline</button>
        <button class="call-control" data-answer-call type="button">Answer</button>
      </div>
    </div>
  `;
  overlay.hidden = false;
  overlay.querySelector("[data-answer-call]").addEventListener("click", () => {
    window.location.href = `/calls/${data.sender_id}?answer=1&mode=${data.mode || "video"}`;
  });
  overlay.querySelector("[data-decline-call]").addEventListener("click", () => {
    overlay.hidden = true;
  });
});

if (typeof callConfig !== "undefined") {
  document.addEventListener("DOMContentLoaded", async () => {
    const startButton = document.getElementById("start-call");
    const muteButton = document.getElementById("mute-audio");
    const videoButton = document.getElementById("toggle-video");
    const speakerButton = document.getElementById("speakerphone");
    const endButton = document.getElementById("end-call");
    const localVideo = document.getElementById("local-video");
    const remoteVideo = document.getElementById("remote-video");
    const voiceFallback = document.getElementById("voice-fallback");
    const callStatus = document.getElementById("call-status");
    let peerConnection = null;
    let localStream = null;
    let started = false;

    const configuration = {
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    };

    const setStatus = (text) => {
      if (callStatus) callStatus.textContent = text;
    };

    const setVoiceMode = (enabled) => {
      document.body.classList.toggle("voice-call-active", enabled);
      if (voiceFallback) voiceFallback.hidden = !enabled;
      if (localVideo) localVideo.hidden = enabled;
    };

    const getMedia = async () => {
      if (localStream) return localStream;
      const wantsVideo = callConfig.mode !== "audio";
      try {
        localStream = await navigator.mediaDevices.getUserMedia({
          audio: true,
          video: wantsVideo,
        });
      } catch (error) {
        try {
          localStream = await navigator.mediaDevices.getUserMedia({
            audio: true,
            video: false,
          });
        } catch (audioError) {
          setStatus("Microphone/camera blocked by browser");
          window.alert("Please allow microphone/camera permissions in your browser settings.");
          throw audioError;
        }
      }
      localVideo.srcObject = localStream;
      setVoiceMode(!localStream.getVideoTracks().length);
      return localStream;
    };

    const ensurePeerConnection = async () => {
      if (peerConnection) return peerConnection;
      peerConnection = new RTCPeerConnection(configuration);
      peerConnection.ontrack = (event) => {
        remoteVideo.srcObject = event.streams[0];
        const hasRemoteVideo = event.streams[0].getVideoTracks().length > 0;
        setVoiceMode(!hasRemoteVideo);
        setStatus("Connected");
      };
      peerConnection.onicecandidate = (event) => {
        if (event.candidate) {
          socket.emit("ice_candidate", {
            target_id: callConfig.targetUserId,
            candidate: event.candidate,
          });
        }
      };
      peerConnection.onconnectionstatechange = () => {
        if (["connected", "completed"].includes(peerConnection.connectionState)) {
          setStatus("Connected");
        } else if (["failed", "disconnected"].includes(peerConnection.connectionState)) {
          setStatus("Connection interrupted");
        }
      };
      const stream = await getMedia();
      stream.getTracks().forEach((track) => peerConnection.addTrack(track, stream));
      return peerConnection;
    };

    const createOffer = async () => {
      const connection = await ensurePeerConnection();
      const offer = await connection.createOffer();
      await connection.setLocalDescription(offer);
      socket.emit("webrtc_offer", {
        target_id: callConfig.targetUserId,
        offer,
      });
      setStatus("Ringing...");
    };

    const startCall = async () => {
      if (started) return;
      started = true;
      startButton.disabled = true;
      startButton.textContent = "Calling";
      try {
        await ensurePeerConnection();
      } catch (error) {
        started = false;
        startButton.disabled = false;
        startButton.textContent = "Call";
        return;
      }
      if (callConfig.autoAnswer) {
        setStatus("Connecting...");
        socket.emit("ready_for_call", { target_id: callConfig.targetUserId });
      } else {
        setStatus("Ringing...");
        socket.emit("call_invite", {
          target_id: callConfig.targetUserId,
          mode: callConfig.mode,
        });
      }
    };

    const endCall = () => {
      socket.emit("call_ended", { target_id: callConfig.targetUserId, mode: callConfig.mode });
      if (peerConnection) peerConnection.close();
      if (localStream) localStream.getTracks().forEach((track) => track.stop());
      window.location.href = document.referrer || "/messages";
    };

    startButton.addEventListener("click", startCall);
    if (callConfig.autoAnswer || callConfig.autoStart) startCall();

    muteButton.addEventListener("click", async () => {
      if (!localStream) await getMedia();
      localStream.getAudioTracks().forEach((track) => {
        track.enabled = !track.enabled;
        muteButton.textContent = track.enabled ? "Mute" : "Unmute";
        muteButton.classList.toggle("active", !track.enabled);
      });
    });

    videoButton.addEventListener("click", async () => {
      if (!localStream) await getMedia();
      localStream.getVideoTracks().forEach((track) => {
        track.enabled = !track.enabled;
        videoButton.textContent = track.enabled ? "Video" : "Show";
        videoButton.classList.toggle("active", !track.enabled);
        setVoiceMode(!track.enabled);
      });
      if (!localStream.getVideoTracks().length) {
        setStatus("Audio-only call");
      }
    });

    speakerButton.addEventListener("click", () => {
      remoteVideo.muted = !remoteVideo.muted;
      speakerButton.textContent = remoteVideo.muted ? "Speaker off" : "Speaker";
      speakerButton.classList.toggle("active", remoteVideo.muted);
    });

    endButton.addEventListener("click", endCall);

    socket.on("peer_ready", async (data) => {
      if (data.sender_id === callConfig.targetUserId) {
        await createOffer();
      }
    });

    socket.on("call_unavailable", () => {
      setStatus("User is offline");
      window.setTimeout(() => {
        window.location.href = `/chat/${callConfig.targetUserId}`;
      }, 1400);
    });

    socket.on("call_ended", () => {
      setStatus("Call ended");
      if (peerConnection) peerConnection.close();
      if (localStream) localStream.getTracks().forEach((track) => track.stop());
    });

    socket.on("webrtc_offer", async (data) => {
      const connection = await ensurePeerConnection();
      await connection.setRemoteDescription(new RTCSessionDescription(data.offer));
      const answer = await connection.createAnswer();
      await connection.setLocalDescription(answer);
      socket.emit("webrtc_answer", { target_id: data.sender_id, answer });
    });

    socket.on("webrtc_answer", async (data) => {
      if (peerConnection) {
        await peerConnection.setRemoteDescription(
          new RTCSessionDescription(data.answer),
        );
      }
    });

    socket.on("ice_candidate", async (data) => {
      if (peerConnection && data.candidate) {
        await peerConnection.addIceCandidate(new RTCIceCandidate(data.candidate));
      }
    });
  });
}

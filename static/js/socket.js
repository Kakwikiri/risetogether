const socket = io({
  reconnection: true,
  reconnectionAttempts: Infinity,
  reconnectionDelay: 500,
  reconnectionDelayMax: 5000,
  timeout: 20000,
});

let socketEngineLoggingAttached = false;

const socketContext = (extra = {}) => ({
  socketId: socket.id,
  namespace: socket.nsp,
  transport: socket.io && socket.io.engine && socket.io.engine.transport
    ? socket.io.engine.transport.name
    : "unknown",
  ...extra,
});

const socketLog = (event, extra = {}) => {
  console.log("[SOCKET]", socketContext({ event, ...extra }));
};

const pauseOtherVoiceNotes = (currentAudio) => {
  document.querySelectorAll("audio").forEach((audio) => {
    if (audio !== currentAudio && !audio.paused) audio.pause();
  });
};

const enhanceVoiceNotePlayer = (container) => {
  if (!container || container.dataset.enhancedVoiceNote === "1") return;
  const audio = container.querySelector("audio");
  if (!audio) return;
  container.dataset.enhancedVoiceNote = "1";
  audio.addEventListener("play", () => pauseOtherVoiceNotes(audio));
  container.querySelectorAll("[data-audio-speed]").forEach((button) => {
    button.addEventListener("click", () => {
      const speed = Number(button.dataset.audioSpeed || "1");
      audio.playbackRate = speed;
      container.querySelectorAll("[data-audio-speed]").forEach((speedButton) => {
        speedButton.classList.toggle("active", speedButton === button);
      });
    });
  });
};

const createVoiceNoteElement = (url) => {
  const wrapper = document.createElement("div");
  wrapper.className = "voice-note-player";
  const audio = document.createElement("audio");
  audio.controls = true;
  audio.src = url;
  const tools = document.createElement("div");
  tools.className = "voice-note-tools";
  ["1", "1.5", "2"].forEach((speed) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.audioSpeed = speed;
    button.textContent = `${speed}x`;
    tools.appendChild(button);
  });
  const download = document.createElement("a");
  download.className = "chat-file";
  download.href = url;
  download.download = "";
  download.textContent = "Download audio";
  tools.appendChild(download);
  wrapper.append(audio, tools);
  enhanceVoiceNotePlayer(wrapper);
  return wrapper;
};

const isLocationMessage = (content = "") => content.startsWith("My location: https://www.google.com/maps?q=");

const createMessageTextElement = (content = "") => {
  const body = document.createElement("p");
  if (isLocationMessage(content)) {
    const link = document.createElement("a");
    link.className = "location-message-link";
    link.href = content.replace("My location: ", "");
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Open shared location";
    body.appendChild(link);
    return body;
  }
  body.textContent = content;
  return body;
};

const showRealtimeToast = (message) => {
  const toast = document.querySelector("[data-toast]");
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(toast.socketTimer);
  toast.socketTimer = window.setTimeout(() => {
    toast.hidden = true;
  }, 5200);
};

const setRealtimePageStatus = (message) => {
  if (typeof callConfig !== "undefined") {
    const callStatus = document.getElementById("call-status");
    if (callStatus) callStatus.textContent = message;
  }
  if (typeof liveConfig !== "undefined") {
    const liveStatus = document.getElementById("live-status");
    if (liveStatus) {
      liveStatus.textContent = message;
      liveStatus.hidden = false;
    }
  }
};

const attachEngineLogging = () => {
  if (socketEngineLoggingAttached || !socket.io || !socket.io.engine) return;
  socketEngineLoggingAttached = true;
  socket.io.engine.on("upgrade", (transport) => {
    socketLog("transport_upgraded", { transport: transport.name });
  });
  socket.io.engine.on("close", (reason) => {
    socketLog("engine_close", { reason });
  });
};

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
  if (data.sender_id) {
    message.dataset.senderId = data.sender_id;
  }
  message.dataset.messageText = data.content || "";

  const user = document.createElement("span");
  user.className = "chat-user";
  user.textContent = isOwn ? "You" : data.sender_name || `User ${data.sender_id}`;

  const body = data.media_type === "call" || !data.content
    ? null
    : createMessageTextElement(data.content);

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
      media = createVoiceNoteElement(data.media_url);
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
  if (body) {
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
  attachEngineLogging();
  socketLog("connect");
});

socket.on("socket_connected", (data) => {
  socketLog("server_confirmed_connect", data);
});

socket.on("connect_error", (error) => {
  socketLog("connect_error", { message: error && error.message });
  showRealtimeToast("Connection problem. Trying to reconnect...");
  setRealtimePageStatus("Connection problem. Reconnecting...");
});

socket.on("disconnect", (reason) => {
  socketLog("disconnect", { reason });
  if (reason === "io server disconnect") {
    showRealtimeToast("Connection lost. Refresh the page if it does not reconnect.");
    setRealtimePageStatus("Connection lost. Please refresh if it does not reconnect.");
    return;
  }
  showRealtimeToast("Connection lost. Reconnecting...");
  setRealtimePageStatus("Connection lost. Reconnecting...");
});

socket.io.on("reconnect_attempt", (attempt) => {
  socketLog("reconnect_attempt", { attempt });
  setRealtimePageStatus("Reconnecting...");
});

socket.io.on("reconnect", (attempt) => {
  attachEngineLogging();
  socketLog("reconnect", { attempt });
  showRealtimeToast("Reconnected.");
  if (typeof callConfig !== "undefined") setRealtimePageStatus("Reconnected. Continuing call...");
  if (typeof liveConfig !== "undefined") setRealtimePageStatus("Reconnected. Continuing live session...");
});

socket.io.on("reconnect_error", (error) => {
  socketLog("reconnect_error", { message: error && error.message });
});

socket.io.on("reconnect_failed", () => {
  socketLog("reconnect_failed");
  showRealtimeToast("Connection failed. Check your network and refresh.");
  setRealtimePageStatus("Connection failed. Check your network and refresh.");
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
  if (typeof chatConfig === "undefined" || data.sender_id !== chatConfig.currentUserId) {
    incrementBadge("[data-message-badge]", "/messages");
  }
});

socket.on("new_family_message", (data) => {
  console.log("Family message", data);
  if (typeof chatConfig === "undefined" || data.sender_id !== chatConfig.currentUserId) {
    incrementBadge("[data-message-badge]", "/messages");
  }
});

socket.on("room_joined", (data) => {
  console.log("Joined room", data);
});

const incrementBadge = (selector, linkHref) => {
  let badge = document.querySelector(selector);
  if (!badge && linkHref) {
    const link = document.querySelector(`a[href="${linkHref}"]`);
    if (link) {
      badge = document.createElement("span");
      badge.className = "nav-badge";
      badge.dataset[selector.includes("notification") ? "notificationBadge" : "messageBadge"] = "";
      link.append(" ", badge);
    }
  }
  if (!badge) return;
  const current = Number.parseInt(badge.textContent || "0", 10) || 0;
  badge.textContent = current + 1;
};

socket.on("notification_received", (data) => {
  incrementBadge("[data-notification-badge]", "/notifications");
  const toast = document.querySelector("[data-toast]");
  if (toast) {
    toast.textContent = data.message || "New notification";
    toast.hidden = false;
    window.clearTimeout(toast.notificationTimer);
    toast.notificationTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 5200);
  }
});

if (typeof chatConfig !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => {
    const chatForm = document.getElementById("chat-form");
    const chatInput = document.getElementById("chat-input");
    const chatLog = document.getElementById("chat-log");
    const chatFile = document.getElementById("chat-file");
    const filePreview = document.getElementById("chat-file-preview");
    const locationButton = document.getElementById("location-button");
    const voiceNoteButton = document.getElementById("voice-note-button");
    const videoNoteButton = document.getElementById("video-note-button");
    const voicePanel = document.getElementById("voice-note-panel");
    const voiceState = voicePanel ? voicePanel.querySelector("[data-voice-state]") : null;
    const voiceTimer = voicePanel ? voicePanel.querySelector("[data-voice-timer]") : null;
    const voicePreview = voicePanel ? voicePanel.querySelector("[data-voice-preview]") : null;
    const voiceWaveform = voicePanel ? voicePanel.querySelector("[data-voice-waveform]") : null;
    const voicePauseButton = voicePanel ? voicePanel.querySelector("[data-voice-pause]") : null;
    const voiceResumeButton = voicePanel ? voicePanel.querySelector("[data-voice-resume]") : null;
    const voiceStopButton = voicePanel ? voicePanel.querySelector("[data-voice-stop]") : null;
    const voiceRecordAgainButton = voicePanel ? voicePanel.querySelector("[data-voice-record-again]") : null;
    const voiceSendButton = voicePanel ? voicePanel.querySelector("[data-voice-send]") : null;
    const voiceCancelButton = voicePanel ? voicePanel.querySelector("[data-voice-cancel]") : null;
    const videoPanel = document.getElementById("video-note-panel");
    const videoState = videoPanel ? videoPanel.querySelector("[data-video-state]") : null;
    const videoTimer = videoPanel ? videoPanel.querySelector("[data-video-timer]") : null;
    const videoLive = videoPanel ? videoPanel.querySelector("[data-video-live]") : null;
    const videoPreview = videoPanel ? videoPanel.querySelector("[data-video-preview]") : null;
    const videoCameraStatus = videoPanel ? videoPanel.querySelector("[data-video-camera-status]") : null;
    const videoMicStatus = videoPanel ? videoPanel.querySelector("[data-video-mic-status]") : null;
    const videoRecordButton = videoPanel ? videoPanel.querySelector("[data-video-record]") : null;
    const videoStopButton = videoPanel ? videoPanel.querySelector("[data-video-stop]") : null;
    const videoSwitchButton = videoPanel ? videoPanel.querySelector("[data-video-switch]") : null;
    const videoRecordAgainButton = videoPanel ? videoPanel.querySelector("[data-video-record-again]") : null;
    const videoSendButton = videoPanel ? videoPanel.querySelector("[data-video-send]") : null;
    const videoCancelButton = videoPanel ? videoPanel.querySelector("[data-video-cancel]") : null;
    const selectionToolbar = document.querySelector("[data-chat-selection-toolbar]");
    const selectionCount = document.querySelector("[data-selection-count]");
    const selectionCancel = document.querySelector("[data-selection-cancel]");
    const selectionReply = document.querySelector("[data-selection-reply]");
    const selectionPin = document.querySelector("[data-selection-pin]");
    const selectionDelete = document.querySelector("[data-selection-delete]");
    const selectionForward = document.querySelector("[data-selection-forward]");
    const selectionMore = document.querySelector("[data-selection-more]");
    const normalHeaderParts = document.querySelectorAll("[data-chat-normal-header]");
    const pinnedStrip = document.querySelector("[data-pinned-strip]");
    const replyPreview = document.getElementById("reply-preview");
    const viewOnceInput = document.getElementById("view-once");
    const expireInput = document.getElementById("expire-one-minute");
    let replyToId = null;
    let longPressTimer = null;
    let voiceStream = null;
    let voiceRecorder = null;
    let voiceChunks = [];
    let voiceBlob = null;
    let voiceObjectUrl = "";
    let voiceStartedAt = 0;
    let voiceElapsedBeforePause = 0;
    let voiceTimerId = null;
    let voiceCancelled = false;
    let videoStream = null;
    let videoRecorder = null;
    let videoChunks = [];
    let videoBlob = null;
    let videoObjectUrl = "";
    let videoStartedAt = 0;
    let videoTimerId = null;
    let videoCancelled = false;
    let videoFacingMode = "user";
    const selectedMessages = new Map();

    const syncChatBottomSpace = () => {
      if (!chatForm) return;
      const formHeight = Math.ceil(chatForm.getBoundingClientRect().height);
      const openPanel = [voicePanel, videoPanel].find((panel) => panel && !panel.hidden);
      const panelHeight = openPanel ? Math.ceil(openPanel.getBoundingClientRect().height) : 0;
      document.documentElement.style.setProperty("--chat-form-height", `${formHeight}px`);
      document.documentElement.style.setProperty("--chat-panel-height", `${panelHeight}px`);
      if (chatLog) {
        window.requestAnimationFrame(() => {
          chatLog.scrollTop = chatLog.scrollHeight;
        });
      }
    };

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

    const uploadChatFile = async (file, content = "", options = {}) => {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("content", content);
      if (options.mediaKind) formData.append("media_kind", options.mediaKind);
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
        let message = "File could not be sent.";
        try {
          const errorData = await response.json();
          message = errorData.error || message;
        } catch (error) {
          // Keep the generic message when the response is not JSON.
        }
        window.alert(message);
        return false;
      }
      const data = await response.json();
      appendChatMessage(chatLog, data, data.sender_id === chatConfig.currentUserId);
      if (chatLog) {
        chatLog.scrollTop = chatLog.scrollHeight;
      }
      return true;
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

    const clearMessageSelection = () => {
      selectedMessages.forEach((message) => message.classList.remove("is-selected"));
      selectedMessages.clear();
      if (selectionToolbar) selectionToolbar.hidden = true;
      normalHeaderParts.forEach((part) => {
        part.hidden = false;
      });
    };

    const selectedMessageList = () => Array.from(selectedMessages.values());

    const deleteSelectedMessages = async (scope) => {
      const messages = Array.from(selectedMessages.values());
      if (!messages.length) return;
      const allowed = scope === "me" || messages.every(
        (message) => Number(message.dataset.senderId) === chatConfig.currentUserId,
      );
      if (!allowed) {
        window.alert("Only your own messages can be deleted for everyone.");
        return;
      }
      await Promise.all(messages.map(async (message) => {
        const formData = new FormData();
        formData.append("scope", scope);
        const response = await fetch(`/chat/message/${message.dataset.messageId}/delete`, {
          method: "POST",
          body: formData,
        });
        if (response.ok) message.remove();
      }));
      clearMessageSelection();
    };

    const updateSelectionBar = () => {
      const count = selectedMessages.size;
      if (!count) {
        clearMessageSelection();
        return;
      }
      const allOwn = Array.from(selectedMessages.values()).every(
        (message) => Number(message.dataset.senderId) === chatConfig.currentUserId,
      );
      normalHeaderParts.forEach((part) => {
        part.hidden = true;
      });
      if (selectionToolbar) selectionToolbar.hidden = false;
      if (selectionCount) selectionCount.textContent = `${count} selected`;
      if (selectionReply) selectionReply.disabled = count !== 1;
      if (selectionPin) selectionPin.disabled = count !== 1;
      if (selectionDelete) selectionDelete.dataset.scope = allOwn ? "everyone" : "me";
    };

    const toggleMessageSelection = (message) => {
      const messageId = message.dataset.messageId;
      if (!messageId) return;
      if (selectedMessages.has(messageId)) {
        selectedMessages.delete(messageId);
        message.classList.remove("is-selected");
      } else {
        selectedMessages.set(messageId, message);
        message.classList.add("is-selected");
      }
      updateSelectionBar();
    };

    const beginMessageSelection = (message) => {
      if (!message || !message.dataset.messageId) return;
      selectedMessages.forEach((selected) => selected.classList.remove("is-selected"));
      selectedMessages.clear();
      selectedMessages.set(message.dataset.messageId, message);
      message.classList.add("is-selected");
      updateSelectionBar();
    };

    const runMessageAction = (action, message) => {
      const messageId = message.dataset.messageId;
      if (action === "reply") {
        clearMessageSelection();
        replyToId = messageId;
        if (replyPreview) {
          replyPreview.textContent = `Replying to: ${message.dataset.messageText || "media"}`;
          replyPreview.hidden = false;
        }
        chatInput.focus();
      } else if (action === "select") {
        toggleMessageSelection(message);
      } else if (action === "delete_me" || action === "delete_everyone") {
        clearMessageSelection();
        const formData = new FormData();
        formData.append("scope", action === "delete_everyone" ? "everyone" : "me");
        fetch(`/chat/message/${messageId}/delete`, {
          method: "POST",
          body: formData,
        }).then((response) => {
          if (response.ok) {
            message.remove();
          } else {
            window.alert("Message could not be deleted.");
          }
        });
      } else if (action === "pin") {
        clearMessageSelection();
        fetch(`/chat/message/${messageId}/pin`, { method: "POST" }).then((response) => {
          if (response.ok) {
            message.classList.add("is-pinned");
            if (pinnedStrip) {
              const label = message.dataset.messageText || "Pinned message";
              pinnedStrip.innerHTML = "";
              const prefix = document.createElement("span");
              prefix.textContent = "Pinned for 24h";
              const text = document.createElement("strong");
              text.textContent = label;
              pinnedStrip.append(prefix, text);
              pinnedStrip.hidden = false;
            }
          }
          clearMessageSelection();
        });
      } else if (action === "forward") {
        clearMessageSelection();
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

    const runSelectionAction = (action) => {
      const messages = selectedMessageList();
      if (!messages.length) return;
      if (action === "reply" || action === "pin") {
        if (messages.length !== 1) return;
        runMessageAction(action, messages[0]);
      } else if (action === "delete") {
        const allOwn = messages.every(
          (message) => Number(message.dataset.senderId) === chatConfig.currentUserId,
        );
        const scope = allOwn && window.confirm("Delete selected messages for everyone? Press Cancel to delete only for you.")
          ? "everyone"
          : "me";
        deleteSelectedMessages(scope);
      } else if (action === "forward") {
        messages.forEach((message) => runMessageAction("forward", message));
        clearMessageSelection();
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
        if (voiceBlob) {
          sendVoicePreview();
          syncChatBottomSpace();
          return;
        }
        if (videoBlob) {
          sendVideoPreview();
          syncChatBottomSpace();
          return;
        }
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
      chatInput.addEventListener("input", syncChatBottomSpace);
    }

    if (chatForm && "ResizeObserver" in window) {
      new ResizeObserver(syncChatBottomSpace).observe(chatForm);
      syncChatBottomSpace();
    } else {
      syncChatBottomSpace();
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
        beginMessageSelection(message);
      });
      chatLog.addEventListener("touchstart", (event) => {
        const message = event.target.closest(".chat-message");
        if (!message) return;
        longPressTimer = window.setTimeout(() => beginMessageSelection(message), 520);
      });
      chatLog.addEventListener("touchend", () => window.clearTimeout(longPressTimer));
      chatLog.addEventListener("click", (event) => {
        const message = event.target.closest(".chat-message");
        if (message && selectedMessages.size) {
          toggleMessageSelection(message);
          return;
        }
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

    if (selectionCancel) selectionCancel.addEventListener("click", clearMessageSelection);
    if (selectionReply) selectionReply.addEventListener("click", () => runSelectionAction("reply"));
    if (selectionPin) selectionPin.addEventListener("click", () => runSelectionAction("pin"));
    if (selectionDelete) selectionDelete.addEventListener("click", () => runSelectionAction("delete"));
    if (selectionForward) selectionForward.addEventListener("click", () => runSelectionAction("forward"));
    if (selectionMore) {
      selectionMore.addEventListener("click", () => {
        window.alert("Use Delete to choose delete scope, Reply for one message, or Forward for selected messages.");
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

    const getRecorderMimeType = (kind) => {
      const choices = kind === "video"
        ? ["video/webm;codecs=vp8,opus", "video/webm"]
        : ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];
      return choices.find((type) => MediaRecorder.isTypeSupported(type)) || "";
    };

    const formatVoiceDuration = (milliseconds) => {
      const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
      const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
      const seconds = String(totalSeconds % 60).padStart(2, "0");
      return `${minutes}:${seconds}`;
    };

    const currentVoiceElapsed = () => {
      if (!voiceRecorder || voiceRecorder.state === "inactive") return voiceElapsedBeforePause;
      if (voiceRecorder.state === "paused") return voiceElapsedBeforePause;
      return voiceElapsedBeforePause + Date.now() - voiceStartedAt;
    };

    const setVoiceStatus = (status) => {
      if (voiceState) voiceState.textContent = status;
    };

    const setVoiceControls = (state) => {
      if (!voicePanel) return;
      if (voicePauseButton) voicePauseButton.hidden = state !== "recording";
      if (voiceResumeButton) voiceResumeButton.hidden = state !== "paused";
      if (voiceStopButton) voiceStopButton.hidden = !["recording", "paused"].includes(state);
      if (voiceRecordAgainButton) voiceRecordAgainButton.hidden = state !== "preview";
      if (voiceSendButton) voiceSendButton.hidden = state !== "preview";
      if (voiceWaveform) voiceWaveform.classList.toggle("active", state === "recording");
      if (voicePreview) voicePreview.hidden = state !== "preview";
      syncChatBottomSpace();
    };

    const stopVoiceTimer = () => {
      if (voiceTimerId) window.clearInterval(voiceTimerId);
      voiceTimerId = null;
    };

    const startVoiceTimer = () => {
      stopVoiceTimer();
      voiceTimerId = window.setInterval(() => {
        if (voiceTimer) voiceTimer.textContent = formatVoiceDuration(currentVoiceElapsed());
      }, 250);
    };

    const stopVoiceTracks = () => {
      if (voiceStream) {
        voiceStream.getTracks().forEach((track) => track.stop());
      }
      voiceStream = null;
    };

    const clearVoiceObjectUrl = () => {
      if (voiceObjectUrl) URL.revokeObjectURL(voiceObjectUrl);
      voiceObjectUrl = "";
    };

    const setRecorderButtonState = (button, isRecording, idleLabel, activeLabel) => {
      if (!button) return;
      button.classList.toggle("recording", isRecording);
      button.setAttribute("aria-label", isRecording ? activeLabel : idleLabel);
      button.setAttribute("title", isRecording ? activeLabel : idleLabel);
    };

    const resetVoicePanel = (status = "Ready") => {
      stopVoiceTimer();
      stopVoiceTracks();
      clearVoiceObjectUrl();
      voiceRecorder = null;
      voiceChunks = [];
      voiceBlob = null;
      voiceStartedAt = 0;
      voiceElapsedBeforePause = 0;
      voiceCancelled = false;
      if (voiceTimer) voiceTimer.textContent = "00:00";
      if (voicePreview) {
        voicePreview.pause();
        voicePreview.removeAttribute("src");
        voicePreview.load();
      }
      setVoiceStatus(status);
      setVoiceControls("ready");
      if (voicePanel) voicePanel.hidden = true;
      setRecorderButtonState(voiceNoteButton, false, "Record voice note", "Stop voice note");
      syncChatBottomSpace();
    };

    const keepVeryShortVoiceNote = (duration) => {
      if (duration >= 1500) return true;
      return window.confirm("This voice note is very short. Keep it anyway?");
    };

    const finishVoiceRecording = async () => {
      stopVoiceTimer();
      stopVoiceTracks();
      if (voiceCancelled) {
        resetVoicePanel("Ready");
        return;
      }
      setVoiceStatus("Processing");
      setVoiceControls("processing");
      const type = voiceRecorder && voiceRecorder.mimeType ? voiceRecorder.mimeType : "audio/webm";
      voiceBlob = new Blob(voiceChunks, { type });
      const duration = currentVoiceElapsed();
      if (!voiceBlob.size || duration < 800 || !keepVeryShortVoiceNote(duration)) {
        resetVoicePanel("Recording failed");
        window.alert("Voice note was too short to send.");
        return;
      }
      clearVoiceObjectUrl();
      voiceObjectUrl = URL.createObjectURL(voiceBlob);
      if (voicePreview) {
        voicePreview.src = voiceObjectUrl;
        voicePreview.hidden = false;
      }
      setVoiceStatus("Preview");
      setVoiceControls("preview");
      if (voiceTimer) voiceTimer.textContent = formatVoiceDuration(duration);
      setRecorderButtonState(voiceNoteButton, false, "Record voice note", "Stop voice note");
    };

    const startVoiceRecording = async () => {
      if (voiceRecorder && voiceRecorder.state !== "inactive") {
        return;
      }
      if (!navigator.mediaDevices || !window.MediaRecorder) {
        if (voicePanel) voicePanel.hidden = false;
        setVoiceStatus("Recording failed");
        window.alert("Recording is not supported by this browser.");
        return;
      }
      resetVoicePanel("Ready");
      if (voicePanel) voicePanel.hidden = false;
      setVoiceStatus("Requesting microphone");
      try {
        voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (error) {
        setVoiceStatus("Permission denied");
        window.alert("Microphone is blocked.");
        return;
      }
      const mimeType = getRecorderMimeType("audio");
      try {
        voiceRecorder = new MediaRecorder(voiceStream, mimeType ? { mimeType } : undefined);
      } catch (error) {
        stopVoiceTracks();
        setVoiceStatus("Recording failed");
        window.alert("Voice recording could not start.");
        return;
      }
      voiceChunks = [];
      voiceCancelled = false;
      voiceElapsedBeforePause = 0;
      voiceStartedAt = Date.now();
      voiceRecorder.ondataavailable = (event) => {
        if (event.data && event.data.size) voiceChunks.push(event.data);
      };
      voiceRecorder.onstop = finishVoiceRecording;
      voiceRecorder.start();
      setVoiceStatus("Recording");
      setVoiceControls("recording");
      startVoiceTimer();
      setRecorderButtonState(voiceNoteButton, true, "Record voice note", "Stop voice note");
      window.setTimeout(() => {
        if (voiceRecorder && voiceRecorder.state !== "inactive") {
          stopVoiceRecording();
        }
      }, 180000);
    };

    const pauseVoiceRecording = () => {
      if (!voiceRecorder || voiceRecorder.state !== "recording") return;
      voiceElapsedBeforePause += Date.now() - voiceStartedAt;
      voiceRecorder.pause();
      stopVoiceTimer();
      setVoiceStatus("Paused");
      setVoiceControls("paused");
    };

    const resumeVoiceRecording = () => {
      if (!voiceRecorder || voiceRecorder.state !== "paused") return;
      voiceStartedAt = Date.now();
      voiceRecorder.resume();
      startVoiceTimer();
      setVoiceStatus("Recording");
      setVoiceControls("recording");
    };

    const stopVoiceRecording = () => {
      if (!voiceRecorder || voiceRecorder.state === "inactive") return;
      if (voiceRecorder.state === "recording") {
        voiceElapsedBeforePause += Date.now() - voiceStartedAt;
      }
      voiceRecorder.stop();
    };

    const toggleVoiceNote = () => {
      if (voiceRecorder && voiceRecorder.state !== "inactive") {
        stopVoiceRecording();
        return;
      }
      startVoiceRecording();
    };

    const cancelVoiceRecording = () => {
      voiceCancelled = true;
      if (voiceRecorder && voiceRecorder.state !== "inactive") {
        voiceRecorder.stop();
      } else {
        resetVoicePanel("Ready");
      }
    };

    const sendVoicePreview = async () => {
      if (!voiceBlob) return;
      setVoiceStatus("Uploading");
      setVoiceControls("uploading");
      const extension = voiceBlob.type.includes("ogg") ? "ogg" : "webm";
      const file = new File([voiceBlob], `voice-note-${Date.now()}.${extension}`, {
        type: voiceBlob.type || "audio/webm",
      });
      try {
        const sent = await uploadChatFile(file, "Voice note", { mediaKind: "audio" });
        if (!sent) {
          setVoiceStatus("Upload failed");
          setVoiceControls("preview");
          return;
        }
        clearComposerState();
        setVoiceStatus("Sent");
        window.setTimeout(() => resetVoicePanel("Ready"), 900);
      } catch (error) {
        setVoiceStatus("Upload failed");
        setVoiceControls("preview");
      }
    };

    const setVideoStatus = (status) => {
      if (videoState) videoState.textContent = status;
    };

    const setVideoControls = (state) => {
      if (!videoPanel) return;
      if (videoRecordButton) videoRecordButton.hidden = state !== "ready";
      if (videoStopButton) videoStopButton.hidden = state !== "recording";
      if (videoSwitchButton) videoSwitchButton.hidden = !["ready", "preparing"].includes(state);
      if (videoRecordAgainButton) videoRecordAgainButton.hidden = state !== "preview";
      if (videoSendButton) videoSendButton.hidden = state !== "preview";
      if (videoLive) videoLive.hidden = !["ready", "recording"].includes(state);
      if (videoPreview) videoPreview.hidden = state !== "preview";
      syncChatBottomSpace();
    };

    const stopVideoTimer = () => {
      if (videoTimerId) window.clearInterval(videoTimerId);
      videoTimerId = null;
    };

    const startVideoTimer = () => {
      stopVideoTimer();
      videoTimerId = window.setInterval(() => {
        if (videoTimer) videoTimer.textContent = formatVoiceDuration(Date.now() - videoStartedAt);
      }, 250);
    };

    const stopVideoTracks = () => {
      if (videoStream) {
        videoStream.getTracks().forEach((track) => track.stop());
      }
      videoStream = null;
      if (videoLive) {
        videoLive.pause();
        videoLive.srcObject = null;
      }
    };

    const clearVideoObjectUrl = () => {
      if (videoObjectUrl) URL.revokeObjectURL(videoObjectUrl);
      videoObjectUrl = "";
    };

    const resetVideoPanel = (status = "Preparing camera") => {
      stopVideoTimer();
      stopVideoTracks();
      clearVideoObjectUrl();
      videoRecorder = null;
      videoChunks = [];
      videoBlob = null;
      videoStartedAt = 0;
      videoCancelled = false;
      if (videoTimer) videoTimer.textContent = "00:00";
      if (videoPreview) {
        videoPreview.pause();
        videoPreview.removeAttribute("src");
        videoPreview.load();
      }
      if (videoCameraStatus) videoCameraStatus.textContent = "Camera: idle";
      if (videoMicStatus) videoMicStatus.textContent = "Microphone: idle";
      setVideoStatus(status);
      setVideoControls("idle");
      if (videoPanel) videoPanel.hidden = true;
      setRecorderButtonState(videoNoteButton, false, "Record video note", "Stop video note");
      syncChatBottomSpace();
    };

    const prepareVideoNote = async () => {
      if (!navigator.mediaDevices || !window.MediaRecorder) {
        if (videoPanel) videoPanel.hidden = false;
        setVideoStatus("Camera unavailable");
        window.alert("Video recording is not supported by this browser.");
        return;
      }
      resetVideoPanel("Preparing camera");
      if (videoPanel) videoPanel.hidden = false;
      setVideoControls("preparing");
      if (videoCameraStatus) videoCameraStatus.textContent = "Camera: preparing";
      if (videoMicStatus) videoMicStatus.textContent = "Microphone: preparing";
      try {
        videoStream = await navigator.mediaDevices.getUserMedia({
          audio: true,
          video: { facingMode: videoFacingMode },
        });
      } catch (error) {
        setVideoStatus("Permission denied");
        if (videoCameraStatus) videoCameraStatus.textContent = "Camera: unavailable";
        if (videoMicStatus) videoMicStatus.textContent = "Microphone: unavailable";
        window.alert("Camera or microphone is blocked.");
        return;
      }
      if (videoLive) {
        videoLive.srcObject = videoStream;
        try {
          await videoLive.play();
        } catch (error) {
          // Some browsers require a second user tap; controls stay visible.
        }
      }
      if (videoCameraStatus) {
        videoCameraStatus.textContent = `Camera: ${videoStream.getVideoTracks().length ? "ready" : "unavailable"}`;
      }
      if (videoMicStatus) {
        videoMicStatus.textContent = `Microphone: ${videoStream.getAudioTracks().length ? "ready" : "unavailable"}`;
      }
      setVideoStatus("Camera ready");
      setVideoControls("ready");
    };

    const finishVideoRecording = async () => {
      stopVideoTimer();
      stopVideoTracks();
      if (videoCancelled) {
        resetVideoPanel("Preparing camera");
        return;
      }
      setVideoStatus("Processing");
      setVideoControls("processing");
      const type = videoRecorder && videoRecorder.mimeType ? videoRecorder.mimeType : "video/webm";
      videoBlob = new Blob(videoChunks, { type });
      const duration = Date.now() - videoStartedAt;
      if (!videoBlob.size || duration < 800) {
        resetVideoPanel("Recording failed");
        window.alert("Video note was too short to send.");
        return;
      }
      clearVideoObjectUrl();
      videoObjectUrl = URL.createObjectURL(videoBlob);
      if (videoPreview) {
        videoPreview.src = videoObjectUrl;
        videoPreview.muted = false;
        videoPreview.hidden = false;
      }
      setVideoStatus("Preview");
      setVideoControls("preview");
      if (videoTimer) videoTimer.textContent = formatVoiceDuration(duration);
      setRecorderButtonState(videoNoteButton, false, "Record video note", "Stop video note");
    };

    const startVideoRecording = () => {
      if (!videoStream || (videoRecorder && videoRecorder.state !== "inactive")) return;
      const mimeType = getRecorderMimeType("video");
      try {
        videoRecorder = new MediaRecorder(videoStream, mimeType ? { mimeType } : undefined);
      } catch (error) {
        setVideoStatus("Recording failed");
        window.alert("Video recording could not start.");
        return;
      }
      videoChunks = [];
      videoCancelled = false;
      videoStartedAt = Date.now();
      videoRecorder.ondataavailable = (event) => {
        if (event.data && event.data.size) videoChunks.push(event.data);
      };
      videoRecorder.onstop = finishVideoRecording;
      videoRecorder.start();
      setVideoStatus("Recording");
      setVideoControls("recording");
      startVideoTimer();
      setRecorderButtonState(videoNoteButton, true, "Record video note", "Stop video note");
      window.setTimeout(() => {
        if (videoRecorder && videoRecorder.state !== "inactive") {
          videoRecorder.stop();
        }
      }, 60000);
    };

    const stopVideoRecording = () => {
      if (!videoRecorder || videoRecorder.state === "inactive") return;
      videoRecorder.stop();
    };

    const toggleVideoNote = async () => {
      if (videoRecorder && videoRecorder.state !== "inactive") {
        stopVideoRecording();
        return;
      }
      if (videoStream) {
        startVideoRecording();
        return;
      }
      await prepareVideoNote();
    };

    const cancelVideoNote = () => {
      videoCancelled = true;
      if (videoRecorder && videoRecorder.state !== "inactive") {
        videoRecorder.stop();
      } else {
        resetVideoPanel("Preparing camera");
      }
    };

    const switchVideoCamera = async () => {
      videoFacingMode = videoFacingMode === "user" ? "environment" : "user";
      await prepareVideoNote();
    };

    const sendVideoPreview = async () => {
      if (!videoBlob) return;
      setVideoStatus("Uploading");
      setVideoControls("uploading");
      const file = new File([videoBlob], `video-note-${Date.now()}.webm`, {
        type: videoBlob.type || "video/webm",
      });
      try {
        const sent = await uploadChatFile(file, "Video note", { mediaKind: "video" });
        if (!sent) {
          setVideoStatus("Upload failed");
          setVideoControls("preview");
          return;
        }
        clearComposerState();
        setVideoStatus("Sent");
        window.setTimeout(() => resetVideoPanel("Preparing camera"), 900);
      } catch (error) {
        setVideoStatus("Upload failed");
        setVideoControls("preview");
      }
    };

    if (voiceNoteButton) {
      voiceNoteButton.addEventListener("click", toggleVoiceNote);
    }

    if (voicePauseButton) {
      voicePauseButton.addEventListener("click", pauseVoiceRecording);
    }

    if (voiceResumeButton) {
      voiceResumeButton.addEventListener("click", resumeVoiceRecording);
    }

    if (voiceStopButton) {
      voiceStopButton.addEventListener("click", stopVoiceRecording);
    }

    if (voiceCancelButton) {
      voiceCancelButton.addEventListener("click", cancelVoiceRecording);
    }

    if (voiceRecordAgainButton) {
      voiceRecordAgainButton.addEventListener("click", startVoiceRecording);
    }

    if (voiceSendButton) {
      voiceSendButton.addEventListener("click", sendVoicePreview);
    }

    if (videoNoteButton) {
      videoNoteButton.addEventListener("click", toggleVideoNote);
    }

    if (videoRecordButton) {
      videoRecordButton.addEventListener("click", startVideoRecording);
    }

    if (videoStopButton) {
      videoStopButton.addEventListener("click", stopVideoRecording);
    }

    if (videoCancelButton) {
      videoCancelButton.addEventListener("click", cancelVideoNote);
    }

    if (videoRecordAgainButton) {
      videoRecordAgainButton.addEventListener("click", prepareVideoNote);
    }

    if (videoSwitchButton) {
      videoSwitchButton.addEventListener("click", switchVideoCamera);
    }

    if (videoSendButton) {
      videoSendButton.addEventListener("click", sendVideoPreview);
    }

    window.addEventListener("beforeunload", () => {
      voiceCancelled = true;
      if (voiceRecorder && voiceRecorder.state !== "inactive") {
        voiceRecorder.stop();
      }
      stopVoiceTracks();
      videoCancelled = true;
      if (videoRecorder && videoRecorder.state !== "inactive") {
        videoRecorder.stop();
      }
      stopVideoTracks();
    });

    document.querySelectorAll(".voice-note-player").forEach(enhanceVoiceNotePlayer);

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
      if (item) {
        selectedMessages.delete(String(data.message_id));
        item.remove();
        updateSelectionBar();
      }
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
  console.log("[call signaling]", {
    event: "client_received_incoming_call",
    callId: data.call_id,
    userId: data.recipient_id,
    targetUserId: data.sender_id,
    socketId: socket.id,
    roomId: data.room_id,
    mode: data.mode,
  });
  const playTone = () => {
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return null;
      const context = new AudioContext();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = 880;
      gain.gain.value = 0.0001;
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start();
      let loud = false;
      const pulse = window.setInterval(() => {
        loud = !loud;
        gain.gain.setTargetAtTime(loud ? 0.08 : 0.0001, context.currentTime, 0.04);
      }, 420);
      return () => {
        window.clearInterval(pulse);
        oscillator.stop();
        context.close();
      };
    } catch (error) {
      return null;
    }
  };
  const stopTone = playTone();
  window.currentIncomingCallStop = stopTone;
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
    if (stopTone) stopTone();
    console.log("[call signaling]", {
      event: "client_answer_incoming_call",
      callId: data.call_id,
      userId: data.recipient_id,
      socketId: socket.id,
      roomId: data.room_id,
    });
    window.location.href = `/calls/${data.sender_id}?answer=1&mode=${data.mode || "video"}`;
  });
  overlay.querySelector("[data-decline-call]").addEventListener("click", () => {
    if (stopTone) stopTone();
    socket.emit("call_declined", {
      target_id: data.sender_id,
      mode: data.mode || "video",
      call_id: data.call_id,
      room_id: data.room_id,
    });
    overlay.hidden = true;
  });
});

socket.on("call_ended", () => {
  if (typeof callConfig !== "undefined") return;
  if (window.currentIncomingCallStop) {
    window.currentIncomingCallStop();
    window.currentIncomingCallStop = null;
  }
  const overlay = document.querySelector(".incoming-call-overlay");
  if (overlay) overlay.hidden = true;
});

socket.on("call_rejected", () => {
  if (typeof callConfig !== "undefined") return;
  if (window.currentIncomingCallStop) {
    window.currentIncomingCallStop();
    window.currentIncomingCallStop = null;
  }
  const overlay = document.querySelector(".incoming-call-overlay");
  if (overlay) overlay.hidden = true;
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
    const callError = document.getElementById("call-error");
    let peerConnection = null;
    let localStream = null;
    let remoteStream = null;
    let started = false;
    let finished = false;
    let callState = "idle";
    let callStartedAt = null;
    let callTimer = null;
    let inviteTimer = null;
    let connectionRecoveryTimer = null;
    const pendingRemoteCandidates = [];

    const configuration = {
      iceServers: callConfig.iceServers || [{ urls: "stun:stun.l.google.com:19302" }],
    };

    const signalLog = (event, extra = {}) => {
      console.log("[call signaling]", {
        event,
        callId: callConfig.callId,
        userId: callConfig.currentUserId,
        targetUserId: callConfig.targetUserId,
        socketId: socket.id,
        roomId: callConfig.roomId,
        ...extra,
      });
    };

    const callPayload = (extra = {}) => ({
      target_id: callConfig.targetUserId,
      call_id: callConfig.callId,
      room_id: callConfig.roomId,
      ...extra,
    });

    const setStatus = (text) => {
      if (callStatus) callStatus.textContent = text;
    };

    const setCallState = (nextState, message) => {
      callState = nextState;
      document.body.dataset.callState = nextState;
      signalLog("client_call_state_changed", { state: nextState, message });
      if (message) setStatus(message);
    };

    const showCallError = (message, error = null) => {
      signalLog("client_call_error", {
        message,
        error: error && (error.message || error.name),
      });
      if (callError) {
        callError.textContent = message;
        callError.hidden = false;
      }
      setStatus(message);
    };

    const clearCallError = () => {
      if (callError) {
        callError.textContent = "";
        callError.hidden = true;
      }
    };

    const formatDuration = (seconds) => {
      const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
      const rest = (seconds % 60).toString().padStart(2, "0");
      return `${minutes}:${rest}`;
    };

    const startCallTimer = () => {
      if (callTimer) return;
      callStartedAt = Date.now();
      callTimer = window.setInterval(() => {
        const elapsed = Math.floor((Date.now() - callStartedAt) / 1000);
        setStatus(`Connected ${formatDuration(elapsed)}`);
      }, 1000);
      setStatus("Connected 00:00");
    };

    const emitWhenConnected = (eventName, payload) => {
      if (socket.connected) {
        signalLog(`client_emit_${eventName}`, { payload });
        socket.emit(eventName, payload);
        return;
      }
      socket.once("connect", () => {
        signalLog(`client_emit_${eventName}`, { payload });
        socket.emit(eventName, payload);
      });
    };

    const cleanupCall = () => {
      if (inviteTimer) {
        window.clearTimeout(inviteTimer);
        inviteTimer = null;
      }
      if (peerConnection) {
        peerConnection.close();
        peerConnection = null;
      }
      if (localStream) {
        localStream.getTracks().forEach((track) => track.stop());
        localStream = null;
      }
      if (remoteVideo) remoteVideo.srcObject = null;
      if (localVideo) localVideo.srcObject = null;
      remoteStream = null;
      if (callTimer) {
        window.clearInterval(callTimer);
        callTimer = null;
      }
      if (connectionRecoveryTimer) {
        window.clearTimeout(connectionRecoveryTimer);
        connectionRecoveryTimer = null;
      }
    };

    const setVoiceMode = (enabled) => {
      document.body.classList.toggle("voice-call-active", enabled);
      if (voiceFallback) voiceFallback.hidden = !enabled;
      if (localVideo) localVideo.hidden = enabled;
    };

    const getMedia = async () => {
      if (localStream) return localStream;
      const wantsVideo = callConfig.mode !== "audio";
      setCallState("preparing_media", wantsVideo ? "Preparing camera..." : "Preparing microphone...");
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
          showCallError("Microphone/camera blocked by browser", audioError);
          throw audioError;
        }
      }
      localVideo.srcObject = localStream;
      try {
        await localVideo.play();
      } catch (error) {
        showCallError("Tap the screen to start your camera preview.", error);
      }
      setVoiceMode(!localStream.getVideoTracks().length);
      signalLog("client_local_media_ready", {
        audioTracks: localStream.getAudioTracks().length,
        videoTracks: localStream.getVideoTracks().length,
      });
      return localStream;
    };

    const ensurePeerConnection = async () => {
      if (peerConnection) return peerConnection;
      signalLog("client_create_peer_connection");
      peerConnection = new RTCPeerConnection(configuration);
      peerConnection.ontrack = async (event) => {
        const [eventStream] = event.streams || [];
        if (eventStream) {
          remoteStream = eventStream;
        } else {
          if (!remoteStream) remoteStream = new MediaStream();
          remoteStream.addTrack(event.track);
        }
        signalLog("client_remote_track_received", {
          trackKind: event.track && event.track.kind,
          streamId: remoteStream && remoteStream.id,
        });
        if (event.track) {
          event.track.addEventListener("mute", () => signalLog("client_remote_track_muted", { trackKind: event.track.kind }));
          event.track.addEventListener("unmute", () => signalLog("client_remote_track_unmuted", { trackKind: event.track.kind }));
          event.track.addEventListener("ended", () => signalLog("client_remote_track_ended", { trackKind: event.track.kind }));
        }
        remoteVideo.srcObject = remoteStream;
        try {
          await remoteVideo.play();
        } catch (error) {
          showCallError("Tap the screen to start remote audio and video.", error);
        }
        const hasRemoteVideo = remoteStream && remoteStream.getVideoTracks().length > 0;
        setVoiceMode(!hasRemoteVideo);
        startCallTimer();
      };
      peerConnection.onsignalingstatechange = () => {
        signalLog("client_signaling_state_changed", {
          signalingState: peerConnection.signalingState,
        });
      };
      peerConnection.onicegatheringstatechange = () => {
        signalLog("client_ice_gathering_state_changed", {
          iceGatheringState: peerConnection.iceGatheringState,
        });
      };
      peerConnection.oniceconnectionstatechange = () => {
        signalLog("client_ice_connection_state_changed", {
          iceConnectionState: peerConnection.iceConnectionState,
        });
      };
      peerConnection.onicecandidate = (event) => {
        if (event.candidate) {
          signalLog("client_send_ice_candidate", {
            candidateType: event.candidate.type,
            candidateMid: event.candidate.sdpMid,
          });
          socket.emit("ice_candidate", callPayload({ candidate: event.candidate }));
        }
      };
      peerConnection.onconnectionstatechange = () => {
        signalLog("client_connection_state_changed", {
          connectionState: peerConnection.connectionState,
        });
        if (["connected", "completed"].includes(peerConnection.connectionState)) {
          if (connectionRecoveryTimer) {
            window.clearTimeout(connectionRecoveryTimer);
            connectionRecoveryTimer = null;
          }
          setCallState("connected");
          startCallTimer();
        } else if (peerConnection.connectionState === "disconnected") {
          setStatus("Network interrupted. Reconnecting...");
          if (!connectionRecoveryTimer) {
            connectionRecoveryTimer = window.setTimeout(() => {
              if (peerConnection && peerConnection.connectionState === "disconnected") {
                setCallState("failed", "Connection interrupted");
              }
              connectionRecoveryTimer = null;
            }, 12000);
          }
        } else if (peerConnection.connectionState === "failed") {
          setCallState("failed", "Connection failed");
        }
      };
      const stream = await getMedia();
      stream.getTracks().forEach((track) => {
        signalLog("client_add_local_track", {
          trackKind: track.kind,
          trackId: track.id,
          enabled: track.enabled,
        });
        peerConnection.addTrack(track, stream);
        track.addEventListener("mute", () => signalLog("client_local_track_muted", { trackKind: track.kind }));
        track.addEventListener("unmute", () => signalLog("client_local_track_unmuted", { trackKind: track.kind }));
        track.addEventListener("ended", () => signalLog("client_local_track_ended", { trackKind: track.kind }));
      });
      return peerConnection;
    };

    const addOrQueueRemoteCandidate = async (candidate) => {
      if (!candidate) return;
      if (!peerConnection || !peerConnection.remoteDescription) {
        pendingRemoteCandidates.push(candidate);
        signalLog("client_queue_remote_ice_candidate", {
          queuedCandidates: pendingRemoteCandidates.length,
        });
        return;
      }
      await peerConnection.addIceCandidate(new RTCIceCandidate(candidate));
      signalLog("client_added_remote_ice_candidate", {
        candidateMid: candidate.sdpMid,
        queuedCandidates: pendingRemoteCandidates.length,
      });
    };

    const flushRemoteCandidates = async () => {
      while (pendingRemoteCandidates.length && peerConnection && peerConnection.remoteDescription) {
        const candidate = pendingRemoteCandidates.shift();
        await peerConnection.addIceCandidate(new RTCIceCandidate(candidate));
        signalLog("client_flushed_remote_ice_candidate", {
          candidateMid: candidate.sdpMid,
          queuedCandidates: pendingRemoteCandidates.length,
        });
      }
    };

    const createOffer = async () => {
      const connection = await ensurePeerConnection();
      setCallState("connecting", "Connecting...");
      signalLog("client_create_webrtc_offer");
      const offer = await connection.createOffer();
      await connection.setLocalDescription(offer);
      signalLog("client_send_webrtc_offer", {
        sdpType: offer.type,
        signalingState: connection.signalingState,
        iceGatheringState: connection.iceGatheringState,
      });
      socket.emit("webrtc_offer", callPayload({ offer }));
    };

    const startCall = async () => {
      if (started) return;
      started = true;
      startButton.disabled = true;
      startButton.textContent = "Calling";
      clearCallError();
      if (callConfig.autoAnswer) {
        setCallState("accepted", "Accepting call...");
        emitWhenConnected("call_accepted", callPayload({ mode: callConfig.mode }));
      } else {
        setCallState("calling", "Calling...");
        emitWhenConnected("call_invite", callPayload({ mode: callConfig.mode }));
        inviteTimer = window.setTimeout(() => {
          if (!["accepted", "connecting", "connected", "ended", "failed"].includes(callState)) {
            setCallState("missed", "No answer");
            cleanupCall();
          }
        }, 50000);
      }
    };

    const endCall = (notifyPeer = true) => {
      if (finished) return;
      finished = true;
      setCallState("ended", "Call ended");
      if (notifyPeer && socket.connected) {
        signalLog("client_emit_call_ended");
        socket.emit("call_ended", callPayload({ mode: callConfig.mode }));
      }
      if (socket.connected) socket.emit("leave_call", callPayload());
      cleanupCall();
      window.setTimeout(() => {
        window.location.href = document.referrer || "/messages";
      }, notifyPeer ? 300 : 0);
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

    speakerButton.addEventListener("click", async () => {
      if (typeof remoteVideo.setSinkId !== "function") {
        showCallError("Speaker routing is controlled by your browser or operating system on this device.");
        return;
      }
      try {
        await remoteVideo.setSinkId("");
        showCallError("Speaker output uses your browser's selected audio device.");
      } catch (error) {
        showCallError("Speaker output could not be changed in this browser.", error);
      }
    });

    endButton.addEventListener("click", () => endCall(true));

    window.addEventListener("pagehide", () => {
      if (started && !finished && socket.connected) {
        socket.emit("call_ended", callPayload({ mode: callConfig.mode }));
        socket.emit("leave_call", callPayload());
      }
    });

    socket.on("room_joined", (data) => {
      if (data.room === callConfig.roomId) {
        signalLog("client_room_joined", data);
      }
    });

    socket.on("call_room_joined", (data) => {
      if (data.call_id === callConfig.callId && data.room_id === callConfig.roomId) {
        signalLog("client_call_room_joined", data);
        if (callConfig.autoAnswer && callState === "accepted") {
          setCallState("preparing_media", "Preparing camera...");
          ensurePeerConnection()
            .then(() => setCallState("connecting", "Connecting..."))
            .catch((error) => {
              setCallState("failed", "Camera or microphone failed");
              showCallError("Camera or microphone failed.", error);
            });
        }
      }
    });

    socket.on("peer_ready", async (data) => {
      signalLog("client_received_call_acceptance", data);
      if (data.sender_id === callConfig.targetUserId && data.call_id === callConfig.callId) {
        if (inviteTimer) {
          window.clearTimeout(inviteTimer);
          inviteTimer = null;
        }
        setCallState("accepted", "Call accepted");
        try {
          await createOffer();
        } catch (error) {
          setCallState("failed", "Could not start call");
          showCallError("Could not start the call.", error);
        }
      }
    });

    socket.on("call_invite_sent", (data) => {
      if (data.call_id !== callConfig.callId) return;
      setCallState("ringing", "Ringing...");
    });

    socket.on("call_unavailable", () => {
      setCallState("unavailable", "User is offline");
      window.setTimeout(() => {
        window.location.href = `/chat/${callConfig.targetUserId}`;
      }, 1400);
    });

    socket.on("call_rejected", (data) => {
      if (data.call_id && data.call_id !== callConfig.callId) return;
      finished = true;
      setCallState("rejected", "Call declined");
      cleanupCall();
      window.setTimeout(() => {
        window.location.href = document.referrer || "/messages";
      }, 1200);
    });

    socket.on("call_timeout", (data) => {
      if (data.call_id && data.call_id !== callConfig.callId) return;
      finished = true;
      setCallState("missed", "No answer");
      cleanupCall();
      window.setTimeout(() => {
        window.location.href = document.referrer || "/messages";
      }, 1200);
    });

    socket.on("call_ended", (data = {}) => {
      if (data.call_id && data.call_id !== callConfig.callId) return;
      if (finished) return;
      finished = true;
      setCallState(data.declined ? "rejected" : "ended", data.declined ? "Call declined" : "Call ended");
      cleanupCall();
      window.setTimeout(() => {
        window.location.href = document.referrer || "/messages";
      }, 900);
    });

    socket.on("webrtc_offer", async (data) => {
      signalLog("client_received_webrtc_offer", data);
      if (data.call_id && data.call_id !== callConfig.callId) return;
      try {
        const connection = await ensurePeerConnection();
        setCallState("connecting", "Connecting...");
        await connection.setRemoteDescription(new RTCSessionDescription(data.offer));
        signalLog("client_set_remote_offer", { sdpType: data.offer && data.offer.type });
        await flushRemoteCandidates();
        const answer = await connection.createAnswer();
        await connection.setLocalDescription(answer);
        signalLog("client_send_webrtc_answer", {
          sdpType: answer.type,
          signalingState: connection.signalingState,
          iceGatheringState: connection.iceGatheringState,
        });
        socket.emit("webrtc_answer", callPayload({ target_id: data.sender_id, answer }));
      } catch (error) {
        setCallState("failed", "Could not answer call");
        showCallError("Could not answer the call.", error);
      }
    });

    socket.on("webrtc_answer", async (data) => {
      signalLog("client_received_webrtc_answer", data);
      if (data.call_id && data.call_id !== callConfig.callId) return;
      if (peerConnection) {
        try {
          await peerConnection.setRemoteDescription(
            new RTCSessionDescription(data.answer),
          );
          signalLog("client_set_remote_answer", { sdpType: data.answer && data.answer.type });
          await flushRemoteCandidates();
        } catch (error) {
          setCallState("failed", "Could not connect call");
          showCallError("Could not connect the call.", error);
        }
      }
    });

    socket.on("ice_candidate", async (data) => {
      signalLog("client_received_ice_candidate", data);
      if (data.call_id && data.call_id !== callConfig.callId) return;
      try {
        await addOrQueueRemoteCandidate(data.candidate);
      } catch (error) {
        showCallError("Network candidate could not be added.", error);
      }
    });
  });
}

if (typeof liveConfig !== "undefined") {
  document.addEventListener("DOMContentLoaded", async () => {
    const liveVideo = document.getElementById("live-video");
    const liveStatus = document.getElementById("live-status");
    const muteButton = document.getElementById("live-mute");
    const cameraButton = document.getElementById("live-camera");
    const hostState = document.getElementById("live-host-state");
    const liveDuration = document.getElementById("live-duration");
    const liveHostDuration = document.getElementById("live-host-duration");
    const liveComments = document.getElementById("live-comments");
    const liveCommentForm = document.getElementById("live-comment-form");
    const liveCommentInput = document.getElementById("live-comment-input");
    const hostConnections = new Map();
    const hostPendingCandidates = new Map();
    const pendingViewerCandidates = [];
    let localStream = null;
    let liveRemoteStream = null;
    let viewerConnection = null;
    let liveStartedAt = Date.now();
    let liveTimer = null;

    const configuration = {
      iceServers: liveConfig.iceServers || [{ urls: "stun:stun.l.google.com:19302" }],
    };

    const setLiveStatus = (message) => {
      if (liveStatus) {
        liveStatus.textContent = message;
        liveStatus.hidden = !message;
      }
    };

    const liveLog = (event, extra = {}) => {
      console.log("[LIVE]", {
        event,
        sessionId: liveConfig.sessionId,
        isHost: liveConfig.isHost,
        socketId: socket.id,
        ...extra,
      });
    };

    const formatLiveDuration = (seconds) => {
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60).toString().padStart(2, "0");
      const rest = (seconds % 60).toString().padStart(2, "0");
      return hours ? `${hours}:${minutes}:${rest}` : `${minutes}:${rest}`;
    };

    const startLiveTimer = () => {
      if (liveTimer) return;
      const render = () => {
        const elapsed = Math.floor((Date.now() - liveStartedAt) / 1000);
        const text = formatLiveDuration(elapsed);
        if (liveDuration) liveDuration.textContent = text;
        if (liveHostDuration) liveHostDuration.textContent = text;
      };
      render();
      liveTimer = window.setInterval(render, 1000);
    };

    const appendLiveComment = (data) => {
      if (!liveComments || data.session_id !== liveConfig.sessionId) return;
      const item = document.createElement("div");
      item.className = "live-comment";
      const meta = document.createElement("span");
      meta.textContent = `${data.sender_name || "Someone"} · ${data.created_at || ""}`;
      const body = document.createElement("p");
      body.textContent = data.content || "";
      item.append(meta, body);
      liveComments.appendChild(item);
      liveComments.scrollTop = liveComments.scrollHeight;
    };

    const joinLive = () => {
      const payload = {
        session_id: liveConfig.sessionId,
        role: liveConfig.isHost ? "host" : "viewer",
      };
      if (socket.connected) socket.emit("join_live", payload);
      else socket.once("connect", () => socket.emit("join_live", payload));
    };

    const startHostStream = async () => {
      try {
        localStream = await navigator.mediaDevices.getUserMedia({
          audio: true,
          video: { facingMode: "user" },
        });
      } catch (error) {
        setLiveStatus("Camera or microphone is blocked by the browser.");
        return;
      }
      if (liveVideo) {
        liveVideo.srcObject = localStream;
        liveVideo.muted = true;
        liveVideo.controls = false;
        try {
          await liveVideo.play();
        } catch (error) {
          setLiveStatus("Tap the video to show your camera preview.");
        }
      }
      liveLog("host_camera_ready", {
        audioTracks: localStream.getAudioTracks().length,
        videoTracks: localStream.getVideoTracks().length,
      });
      if (hostState) hostState.textContent = "You are live";
      setLiveStatus("");
      startLiveTimer();
      joinLive();
    };

    const createHostConnection = async (viewerSid) => {
      if (!localStream || hostConnections.has(viewerSid)) return;
      const connection = new RTCPeerConnection(configuration);
      hostConnections.set(viewerSid, connection);
      localStream.getTracks().forEach((track) => connection.addTrack(track, localStream));
      connection.onicecandidate = (event) => {
        if (event.candidate) {
          liveLog("host_send_ice_candidate", {
            viewerSid,
            candidateMid: event.candidate.sdpMid,
            candidateType: event.candidate.type,
          });
          socket.emit("live_ice_candidate", {
            session_id: liveConfig.sessionId,
            candidate: event.candidate,
            target_sid: viewerSid,
          });
        }
      };
      connection.onconnectionstatechange = () => {
        liveLog("host_connection_state_changed", {
          viewerSid,
          connectionState: connection.connectionState,
        });
        if (["failed", "closed"].includes(connection.connectionState)) {
          hostConnections.delete(viewerSid);
          hostPendingCandidates.delete(viewerSid);
        }
      };
      const offer = await connection.createOffer();
      await connection.setLocalDescription(offer);
      liveLog("host_send_offer", { viewerSid, sdpType: offer.type });
      socket.emit("live_offer", {
        session_id: liveConfig.sessionId,
        viewer_sid: viewerSid,
        offer,
      });
    };

    const addOrQueueHostCandidate = async (viewerSid, candidate) => {
      const connection = hostConnections.get(viewerSid);
      if (!connection || !connection.remoteDescription) {
        const queue = hostPendingCandidates.get(viewerSid) || [];
        queue.push(candidate);
        hostPendingCandidates.set(viewerSid, queue);
        liveLog("host_queue_ice_candidate", { viewerSid, queuedCandidates: queue.length });
        return;
      }
      await connection.addIceCandidate(new RTCIceCandidate(candidate));
      liveLog("host_added_ice_candidate", { viewerSid, candidateMid: candidate.sdpMid });
    };

    const flushHostCandidates = async (viewerSid) => {
      const connection = hostConnections.get(viewerSid);
      const queue = hostPendingCandidates.get(viewerSid) || [];
      while (queue.length && connection && connection.remoteDescription) {
        const candidate = queue.shift();
        await connection.addIceCandidate(new RTCIceCandidate(candidate));
        liveLog("host_flushed_ice_candidate", { viewerSid, candidateMid: candidate.sdpMid, queuedCandidates: queue.length });
      }
      if (!queue.length) hostPendingCandidates.delete(viewerSid);
    };

    const addOrQueueViewerCandidate = async (candidate) => {
      if (!viewerConnection || !viewerConnection.remoteDescription) {
        pendingViewerCandidates.push(candidate);
        liveLog("viewer_queue_ice_candidate", { queuedCandidates: pendingViewerCandidates.length });
        return;
      }
      await viewerConnection.addIceCandidate(new RTCIceCandidate(candidate));
      liveLog("viewer_added_ice_candidate", { candidateMid: candidate.sdpMid });
    };

    const flushViewerCandidates = async () => {
      while (pendingViewerCandidates.length && viewerConnection && viewerConnection.remoteDescription) {
        const candidate = pendingViewerCandidates.shift();
        await viewerConnection.addIceCandidate(new RTCIceCandidate(candidate));
        liveLog("viewer_flushed_ice_candidate", {
          candidateMid: candidate.sdpMid,
          queuedCandidates: pendingViewerCandidates.length,
        });
      }
    };

    const startViewerConnection = async (offer) => {
      if (viewerConnection) {
        viewerConnection.close();
      }
      viewerConnection = new RTCPeerConnection(configuration);
      viewerConnection.ontrack = async (event) => {
        const [eventStream] = event.streams || [];
        if (eventStream) {
          liveRemoteStream = eventStream;
        } else {
          if (!liveRemoteStream) liveRemoteStream = new MediaStream();
          liveRemoteStream.addTrack(event.track);
        }
        liveLog("viewer_remote_track_received", {
          trackKind: event.track && event.track.kind,
          streamId: liveRemoteStream && liveRemoteStream.id,
        });
        if (liveVideo) {
          liveVideo.srcObject = liveRemoteStream;
          liveVideo.controls = true;
          try {
            await liveVideo.play();
          } catch (error) {
            setLiveStatus("Tap the video to start the live stream.");
          }
        }
        setLiveStatus("");
      };
      viewerConnection.onicecandidate = (event) => {
        if (event.candidate) {
          liveLog("viewer_send_ice_candidate", {
            hostSid: dataHostSid,
            candidateMid: event.candidate.sdpMid,
            candidateType: event.candidate.type,
          });
          socket.emit("live_ice_candidate", {
            session_id: liveConfig.sessionId,
            candidate: event.candidate,
            target_sid: dataHostSid,
          });
        }
      };
      viewerConnection.onconnectionstatechange = () => {
        liveLog("viewer_connection_state_changed", {
          connectionState: viewerConnection.connectionState,
        });
        if (["connected", "completed"].includes(viewerConnection.connectionState)) {
          startLiveTimer();
          setLiveStatus("");
        } else if (viewerConnection.connectionState === "disconnected") {
          setLiveStatus("Network interrupted. Reconnecting...");
        } else if (viewerConnection.connectionState === "failed") {
          setLiveStatus("Could not connect to the live stream.");
        }
      };
      await viewerConnection.setRemoteDescription(new RTCSessionDescription(offer));
      await flushViewerCandidates();
      const answer = await viewerConnection.createAnswer();
      await viewerConnection.setLocalDescription(answer);
      liveLog("viewer_send_answer", { sdpType: answer.type, hostSid: dataHostSid });
      socket.emit("live_answer", {
        session_id: liveConfig.sessionId,
        answer,
      });
    };

    let dataHostSid = null;

    if (liveConfig.status !== "live") {
      setLiveStatus("This live session has ended.");
      return;
    }

    if (liveConfig.isHost) {
      await startHostStream();
    } else {
      joinLive();
    }

    if (muteButton) {
      muteButton.addEventListener("click", () => {
        if (!localStream) return;
        localStream.getAudioTracks().forEach((track) => {
          track.enabled = !track.enabled;
          muteButton.textContent = track.enabled ? "Mute" : "Unmute";
          muteButton.classList.toggle("active", !track.enabled);
          if (hostState) hostState.textContent = track.enabled ? "You are live" : "Live with mic muted";
        });
      });
    }

    if (cameraButton) {
      cameraButton.addEventListener("click", () => {
        if (!localStream) return;
        localStream.getVideoTracks().forEach((track) => {
          track.enabled = !track.enabled;
          cameraButton.textContent = track.enabled ? "Camera" : "Show camera";
          cameraButton.classList.toggle("active", !track.enabled);
          if (hostState) hostState.textContent = track.enabled ? "You are live" : "Live with camera off";
        });
      });
    }

    socket.on("live_viewer_joined", (data) => {
      if (!liveConfig.isHost || data.session_id !== liveConfig.sessionId) return;
      createHostConnection(data.viewer_sid);
    });

    socket.on("live_offer", async (data) => {
      if (liveConfig.isHost || data.session_id !== liveConfig.sessionId) return;
      dataHostSid = data.sender_sid || null;
      await startViewerConnection(data.offer);
    });

    socket.on("live_answer", async (data) => {
      if (!liveConfig.isHost || data.session_id !== liveConfig.sessionId) return;
      const connection = hostConnections.get(data.viewer_sid);
      if (connection) {
        await connection.setRemoteDescription(new RTCSessionDescription(data.answer));
        liveLog("host_received_answer", { viewerSid: data.viewer_sid });
        await flushHostCandidates(data.viewer_sid);
      }
    });

    socket.on("live_ice_candidate", async (data) => {
      if (data.session_id !== liveConfig.sessionId || !data.candidate) return;
      if (liveConfig.isHost) {
        await addOrQueueHostCandidate(data.sender_sid, data.candidate);
      } else {
        await addOrQueueViewerCandidate(data.candidate);
      }
    });

    socket.on("live_comment", (data) => {
      appendLiveComment(data);
    });

    socket.on("live_waiting_for_host", (data) => {
      if (data.session_id === liveConfig.sessionId) {
        setLiveStatus("Waiting for the broadcaster to connect...");
      }
    });

    socket.on("live_viewer_count", (data) => {
      if (data.session_id !== liveConfig.sessionId) return;
      const count = document.getElementById("live-viewer-count");
      if (count) {
        count.textContent = `${data.count || 0} watching`;
      }
    });

    socket.on("live_host_ready", (data) => {
      if (!liveConfig.isHost && data.session_id === liveConfig.sessionId) {
        setLiveStatus("Connecting to the live stream...");
        joinLive();
      }
    });

    socket.on("live_host_left", (data) => {
      if (data.session_id === liveConfig.sessionId) {
        setLiveStatus("The live stream has ended.");
        if (liveVideo) liveVideo.srcObject = null;
        if (liveTimer) {
          window.clearInterval(liveTimer);
          liveTimer = null;
        }
      }
    });

    socket.on("live_unavailable", (data) => {
      if (data.session_id === liveConfig.sessionId) {
        setLiveStatus("This live stream is unavailable.");
      }
    });

    if (liveCommentForm && liveCommentInput) {
      liveCommentForm.addEventListener("submit", (event) => {
        event.preventDefault();
        const content = liveCommentInput.value.trim();
        if (!content) return;
        socket.emit("live_comment", {
          session_id: liveConfig.sessionId,
          content,
        });
        liveCommentInput.value = "";
      });
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const syncVisualViewportHeight = () => {
    const height = window.visualViewport ? window.visualViewport.height : window.innerHeight;
    const offsetTop = window.visualViewport ? window.visualViewport.offsetTop : 0;
    const bottomInset = window.visualViewport
      ? Math.max(0, window.innerHeight - window.visualViewport.height - window.visualViewport.offsetTop)
      : 0;
    document.documentElement.style.setProperty("--visual-vh", `${height}px`);
    document.documentElement.style.setProperty("--visual-offset-top", `${offsetTop}px`);
    document.documentElement.style.setProperty("--keyboard-inset", `${bottomInset}px`);
  };
  syncVisualViewportHeight();
  window.addEventListener("resize", syncVisualViewportHeight);
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", syncVisualViewportHeight);
    window.visualViewport.addEventListener("scroll", syncVisualViewportHeight);
  }

  const savedTheme = localStorage.getItem("theme") || "light";
  document.documentElement.dataset.theme = savedTheme;
  const themeToggle = document.querySelector("[data-theme-toggle]");
  if (themeToggle) {
    themeToggle.textContent = savedTheme === "dark" ? "Light" : "Dark";
    themeToggle.addEventListener("click", () => {
      const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = nextTheme;
      localStorage.setItem("theme", nextTheme);
      themeToggle.textContent = nextTheme === "dark" ? "Light" : "Dark";
    });
  }

  const toast = document.querySelector("[data-toast]");
  const showToast = (message) => {
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => {
      toast.hidden = true;
    }, 5200);
  };

  const urlBase64ToUint8Array = (base64String) => {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let index = 0; index < rawData.length; index += 1) {
      outputArray[index] = rawData.charCodeAt(index);
    }
    return outputArray;
  };

  const pushEnable = document.querySelector("[data-push-enable]");
  const pushDisable = document.querySelector("[data-push-disable]");
  const pushStatus = document.querySelector("[data-push-status]");
  const setPushStatus = (message) => {
    if (pushStatus) pushStatus.textContent = message;
    if (message) showToast(message);
  };

  const getServiceWorkerRegistration = async () => {
    if (!("serviceWorker" in navigator)) {
      throw new Error("Service workers are not supported on this browser.");
    }
    const existing = await navigator.serviceWorker.getRegistration();
    if (existing) return existing;
    return navigator.serviceWorker.register("/service-worker.js?v=20260712-chat-icons", { scope: "/" });
  };

  if (pushEnable) {
    pushEnable.addEventListener("click", async () => {
      try {
        if (!("PushManager" in window) || !("Notification" in window)) {
          setPushStatus("Device notifications are not supported on this browser.");
          return;
        }
        const keyResponse = await fetch("/api/push/public-key");
        const keyData = await keyResponse.json();
        if (!keyData.public_key) {
          setPushStatus("Device notifications are not configured on the server yet.");
          return;
        }
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          setPushStatus("Notification permission was not granted.");
          return;
        }
        const registration = await getServiceWorkerRegistration();
        const subscription =
          (await registration.pushManager.getSubscription()) ||
          (await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(keyData.public_key),
          }));
        const response = await fetch("/api/push/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(subscription),
        });
        if (!response.ok) throw new Error("Subscription could not be saved.");
        setPushStatus("Device notifications enabled.");
      } catch (error) {
        setPushStatus(error.message || "Device notifications could not be enabled.");
      }
    });
  }

  if (pushDisable) {
    pushDisable.addEventListener("click", async () => {
      try {
        const registration = await getServiceWorkerRegistration();
        const subscription = await registration.pushManager.getSubscription();
        const endpoint = subscription ? subscription.endpoint : "";
        if (subscription) await subscription.unsubscribe();
        await fetch("/api/push/unsubscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint }),
        });
        setPushStatus("Device notifications disabled.");
      } catch (error) {
        setPushStatus(error.message || "Device notifications could not be disabled.");
      }
    });
  }

  const navToggle = document.querySelector(".nav-toggle");
  const navLinks = document.querySelector(".nav-links");
  if (navToggle && navLinks) {
    navToggle.addEventListener("click", () => {
      navLinks.classList.toggle("open");
      navToggle.classList.toggle("active");
    });
  }

  const installButton = document.querySelector(".install-button");
  const shareAppButton = document.querySelector("[data-share-app]");
  const installPanel = document.querySelector("[data-install-panel]");
  const installMessage = document.querySelector("[data-install-message]");
  const installClose = document.querySelector("[data-install-close]");
  let deferredPrompt = null;

  const isStandalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;
  const isSecureInstallContext = () =>
    window.isSecureContext || ["localhost", "127.0.0.1"].includes(location.hostname);
  const isIos = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isFirefox = () => navigator.userAgent.toLowerCase().includes("firefox");
  const canShowManualInstall = () => isSecureInstallContext() && (isIos() || isFirefox());

  const openInstallPanel = (message) => {
    if (installMessage) installMessage.textContent = message;
    if (installPanel) installPanel.hidden = false;
  };

  const showManualInstallButton = () => {
    if (!installButton || isStandalone() || deferredPrompt || !canShowManualInstall()) return;
    installButton.hidden = false;
    installButton.classList.remove("ready");
    installButton.textContent = "Install";
  };

  if (installButton) {
    installButton.hidden = true;
  }

  if (installClose && installPanel) {
    installClose.addEventListener("click", () => {
      installPanel.hidden = true;
    });
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    if (installButton) {
      installButton.hidden = false;
      installButton.classList.add("ready");
      installButton.textContent = "Install app";
    }
  });

  if (installButton) {
    installButton.addEventListener("click", async () => {
      if (isStandalone()) {
        showToast("RiseTogether is already installed.");
        return;
      }
      if (!deferredPrompt) {
        openInstallPanel(
          !isSecureInstallContext()
            ? "Install needs HTTPS or localhost. Open the secure site, then use Add to Home Screen."
            : isIos()
              ? "On iPhone or iPad, tap Share, then Add to Home Screen."
              : isFirefox()
                ? "Firefox installs web apps from the browser menu on supported devices. Choose Install or Add to Home screen."
                : "Use the browser menu and choose Install app or Add to Home screen.",
        );
        return;
      }
      deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      showToast(
        choice.outcome === "accepted"
          ? "Installing RiseTogether..."
          : "Install cancelled.",
      );
      deferredPrompt = null;
      installButton.hidden = true;
    });
  }

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    if (installButton) {
      installButton.hidden = true;
    }
    if (installPanel) installPanel.hidden = true;
    showToast("RiseTogether installed successfully.");
  });

  showManualInstallButton();

  if (shareAppButton) {
    shareAppButton.addEventListener("click", async () => {
      const shareData = {
        title: "RiseTogether",
        text: "Join me on RiseTogether.",
        url: window.location.origin,
      };
      try {
        if (navigator.share) {
          await navigator.share(shareData);
          return;
        }
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(shareData.url);
          showToast("App link copied.");
          return;
        }
        window.prompt("Copy this RiseTogether link:", shareData.url);
      } catch (error) {
        if (error && error.name !== "AbortError") {
          showToast("Could not share the app link.");
        }
      }
    });
  }

  document.querySelectorAll(".password-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      const field = button.closest(".password-field");
      const input = field ? field.querySelector("input") : null;
      if (!input) return;
      const isPassword = input.type === "password";
      input.type = isPassword ? "text" : "password";
      button.textContent = isPassword ? "Hide" : "Show";
      button.setAttribute(
        "aria-label",
        isPassword ? "Hide password" : "Show password",
      );
    });
  });

  const passwordFields = document.querySelector("[data-password-fields]");
  const passwordToggle = document.querySelector("[data-toggle-password-fields]");
  const passwordFlag = document.querySelector("[data-password-change-flag]");
  if (passwordFields && passwordToggle && passwordFlag) {
    passwordToggle.addEventListener("click", () => {
      const willOpen = passwordFields.hidden;
      passwordFields.hidden = !willOpen;
      passwordFlag.value = willOpen ? "1" : "0";
      passwordToggle.textContent = willOpen ? "Cancel password change" : "Change password";
      if (willOpen) {
        const firstPassword = passwordFields.querySelector("input[type='password']");
        if (firstPassword) firstPassword.focus();
      } else {
        passwordFields.querySelectorAll("input[type='password']").forEach((input) => {
          input.value = "";
        });
      }
    });
  }

  document.querySelectorAll("[data-media-input]").forEach((input) => {
    input.addEventListener("change", () => {
      const form = input.closest("form");
      const preview = form ? form.querySelector("[data-media-preview]") : null;
      const file = input.files && input.files[0];
      if (!preview) return;

      preview.innerHTML = "";
      if (!file) {
        preview.hidden = true;
        return;
      }

      const url = URL.createObjectURL(file);
      let element;
      if (file.type.startsWith("image/")) {
        element = document.createElement("img");
        element.alt = "Selected media preview";
      } else if (file.type.startsWith("video/")) {
        element = document.createElement("video");
        element.controls = true;
      } else if (file.type.startsWith("audio/")) {
        element = document.createElement("audio");
        element.controls = true;
      }

      if (element) {
        element.src = url;
        preview.appendChild(element);
        const meta = document.createElement("span");
        meta.textContent = file.name;
        preview.appendChild(meta);
        preview.hidden = false;
      } else {
        const fileMeta = document.createElement("span");
        fileMeta.textContent = file.name;
        preview.appendChild(fileMeta);
        preview.hidden = false;
      }
    });
  });

  document.querySelectorAll("[data-family-image-form]").forEach((form) => {
    const input = form.querySelector("[data-family-image-input]");
    const preview = form.querySelector("[data-family-image-preview]");
    const cancel = form.querySelector("[data-family-image-cancel]");
    const save = form.querySelector("[data-family-image-save]");
    const status = form.querySelector("[data-family-image-status]");
    if (!input || !preview || !cancel || !save || !status) return;
    const originalSrc = preview.src;
    let objectUrl = "";
    const clearObjectUrl = () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = "";
    };
    input.addEventListener("change", () => {
      clearObjectUrl();
      const file = input.files && input.files[0];
      if (!file) {
        preview.src = originalSrc;
        save.disabled = true;
        cancel.hidden = true;
        status.hidden = true;
        status.textContent = "";
        return;
      }
      if (!file.type.startsWith("image/")) {
        input.value = "";
        preview.src = originalSrc;
        save.disabled = true;
        cancel.hidden = true;
        status.textContent = "Choose an image file.";
        status.hidden = false;
        return;
      }
      objectUrl = URL.createObjectURL(file);
      preview.src = objectUrl;
      save.disabled = false;
      cancel.hidden = false;
      status.textContent = file.name;
      status.hidden = false;
    });
    cancel.addEventListener("click", () => {
      input.value = "";
      clearObjectUrl();
      preview.src = originalSrc;
      save.disabled = true;
      cancel.hidden = true;
      status.hidden = true;
      status.textContent = "";
    });
    form.addEventListener("submit", () => {
      save.disabled = true;
      save.textContent = "Saving...";
      cancel.hidden = true;
      status.textContent = "Saving picture...";
      status.hidden = false;
    });
  });

  document.querySelectorAll("[data-profile-edit-form]").forEach((form) => {
    const input = form.querySelector("[data-profile-avatar-input]");
    const preview = form.querySelector("[data-profile-avatar-preview]");
    const cancel = form.querySelector("[data-profile-avatar-cancel]");
    const status = form.querySelector("[data-profile-avatar-status]");
    if (!input || !preview || !cancel || !status) return;
    const originalSrc = preview.src;
    let objectUrl = "";
    const clearObjectUrl = () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = "";
    };
    input.addEventListener("change", () => {
      clearObjectUrl();
      const file = input.files && input.files[0];
      if (!file) {
        preview.src = originalSrc;
        cancel.hidden = true;
        status.hidden = true;
        return;
      }
      if (!file.type.startsWith("image/")) {
        input.value = "";
        preview.src = originalSrc;
        cancel.hidden = true;
        status.textContent = "Choose an image file.";
        status.hidden = false;
        return;
      }
      objectUrl = URL.createObjectURL(file);
      preview.src = objectUrl;
      cancel.hidden = false;
      status.textContent = file.name;
      status.hidden = false;
    });
    cancel.addEventListener("click", () => {
      input.value = "";
      clearObjectUrl();
      preview.src = originalSrc;
      cancel.hidden = true;
      status.hidden = true;
      status.textContent = "";
    });
    form.addEventListener("submit", () => {
      status.textContent = "Saving profile...";
      status.hidden = false;
    });
  });

  const familyPanels = Array.from(document.querySelectorAll("[data-family-panel]"));
  const familyTabs = Array.from(document.querySelectorAll("[data-family-tab]"));
  if (familyPanels.length && familyTabs.length) {
    const showFamilyPanel = (id, updateHash = true) => {
      const target = familyPanels.find((panel) => panel.id === id) || familyPanels[0];
      familyPanels.forEach((panel) => {
        panel.hidden = panel !== target;
      });
      familyTabs.forEach((tab) => {
        tab.classList.toggle("active", tab.getAttribute("href") === `#${target.id}`);
      });
      if (updateHash && window.location.hash !== `#${target.id}`) {
        window.history.replaceState(null, "", `#${target.id}`);
      }
    };
    familyTabs.forEach((tab) => {
      tab.addEventListener("click", (event) => {
        const href = tab.getAttribute("href") || "";
        if (!href.startsWith("#")) return;
        event.preventDefault();
        showFamilyPanel(href.slice(1));
      });
    });
    const initialId = window.location.hash ? window.location.hash.slice(1) : "family-home";
    showFamilyPanel(initialId, Boolean(window.location.hash));
  }

  const liveStartForm = document.querySelector("[data-live-start-form]");
  if (liveStartForm) {
    const liveStartStatus = liveStartForm.querySelector("[data-live-start-status]");
    let liveStartConfirmed = false;
    const setLiveStartStatus = (message) => {
      if (!liveStartStatus) return;
      liveStartStatus.textContent = message;
      liveStartStatus.hidden = !message;
    };
    liveStartForm.addEventListener("submit", async (event) => {
      if (liveStartConfirmed) return;
      event.preventDefault();
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setLiveStartStatus("Camera access is not supported by this browser.");
        return;
      }
      setLiveStartStatus("Preparing camera...");
      let stream = null;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: true,
          video: { facingMode: "user" },
        });
      } catch (error) {
        setLiveStartStatus("Camera or microphone is blocked.");
        return;
      } finally {
        if (stream) stream.getTracks().forEach((track) => track.stop());
      }
      setLiveStartStatus("Camera ready. Starting live...");
      liveStartConfirmed = true;
      liveStartForm.submit();
    });
  }

  const showVideoFallback = (video) => {
    const frame = video.closest(".media-frame");
    if (!frame || frame.querySelector(".video-fallback")) return;
    frame.classList.add("video-unsupported");
    const fallback = document.createElement("div");
    fallback.className = "video-fallback";
    const downloadUrl = video.dataset.downloadUrl || video.currentSrc || video.src;
    fallback.innerHTML = `
      <strong>Video format not supported on this browser</strong>
      <span>This usually happens with HEVC/H.265 phone videos. Try downloading it or upload an H.264 MP4.</span>
      ${downloadUrl ? `<a href="${downloadUrl}" download>Download video</a>` : ""}
    `;
    frame.appendChild(fallback);
  };

  document.querySelectorAll("video[data-video-player]").forEach((video) => {
    video.addEventListener("error", () => showVideoFallback(video));
    video.addEventListener("loadedmetadata", () => {
      window.setTimeout(() => {
        if (video.videoWidth === 0 && video.readyState > 0) {
          showVideoFallback(video);
        }
      }, 400);
    });
  });

  document.querySelectorAll(".view-once-media").forEach((frame) => {
    frame.addEventListener("contextmenu", (event) => event.preventDefault());
    frame.querySelectorAll("img, video").forEach((media) => {
      media.draggable = false;
    });
  });

  const autoSlideToggle = document.querySelector("[data-auto-slide]");
  if (autoSlideToggle) {
    autoSlideToggle.checked = localStorage.getItem("autoSlideVideos") !== "0";
    autoSlideToggle.addEventListener("change", () => {
      localStorage.setItem("autoSlideVideos", autoSlideToggle.checked ? "1" : "0");
    });
    document.querySelectorAll(".autoplay-video").forEach((video) => {
      video.addEventListener("ended", () => {
        if (!autoSlideToggle.checked) return;
        const cards = [...document.querySelectorAll(".post-card")];
        const current = video.closest(".post-card");
        const next = cards[cards.indexOf(current) + 1];
        if (next) next.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
  }
});

document.addEventListener("DOMContentLoaded", () => {
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

  const navToggle = document.querySelector(".nav-toggle");
  const navLinks = document.querySelector(".nav-links");
  if (navToggle && navLinks) {
    navToggle.addEventListener("click", () => {
      navLinks.classList.toggle("open");
      navToggle.classList.toggle("active");
    });
  }

  const installButton = document.querySelector(".install-button");
  const installPanel = document.querySelector("[data-install-panel]");
  const installMessage = document.querySelector("[data-install-message]");
  const installClose = document.querySelector("[data-install-close]");
  let deferredPrompt = null;

  const isStandalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;

  const openInstallPanel = (message) => {
    if (installMessage) installMessage.textContent = message;
    if (installPanel) installPanel.hidden = false;
  };

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
    installButton.hidden = false;
    installButton.addEventListener("click", async () => {
      if (isStandalone()) {
        showToast("RiseTogether is already installed.");
        return;
      }
      if (!deferredPrompt) {
        const secure = window.isSecureContext || ["localhost", "127.0.0.1"].includes(location.hostname);
        openInstallPanel(
          secure
            ? "Chrome has not released the automatic install prompt yet. You can still install RiseTogether from the browser menu."
            : "Install needs HTTPS or localhost. Open the secure Render site, then use Add to Home Screen.",
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

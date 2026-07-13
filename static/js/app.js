const csrfMeta = document.querySelector('meta[name="csrf-token"]');
const csrfToken = csrfMeta ? csrfMeta.content : "";
const nativeFetch = window.fetch.bind(window);
window.fetch = (resource, options = {}) => {
  const requestUrl = new URL(
    typeof resource === "string" ? resource : resource.url,
    window.location.origin,
  );
  const method = (options.method || (resource instanceof Request ? resource.method : "GET")).toUpperCase();
  if (requestUrl.origin === window.location.origin && !["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
    const headers = new Headers(options.headers || (resource instanceof Request ? resource.headers : undefined));
    headers.set("X-CSRF-Token", csrfToken);
    options = { ...options, headers };
  }
  return nativeFetch(resource, options);
};

document.addEventListener("DOMContentLoaded", () => {
  const APP_VERSION = "20260713-stage14-point-ledger";
  const dismissedUpdateKey = "risetogether-dismissed-update-version";
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
  const pageBack = document.querySelector("[data-page-back]");
  const updateNotice = document.querySelector("[data-update-notice]");
  const updateNow = document.querySelector("[data-update-now]");
  const updateLater = document.querySelector("[data-update-later]");
  let waitingServiceWorker = null;
  const showToast = (message, tone = "info") => {
    if (!toast) return;
    toast.textContent = message;
    toast.dataset.tone = tone;
    toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => {
      toast.hidden = true;
    }, 5200);
  };

  const confirmationModal = document.querySelector("[data-confirmation-modal]");
  const confirmationMessage = document.querySelector("[data-confirmation-message]");
  const confirmationAccept = document.querySelector("[data-confirmation-accept]");
  const celebrationModal = document.querySelector("[data-celebration-modal]");
  const celebrationTitle = document.querySelector("[data-celebration-title]");
  const celebrationMessage = document.querySelector("[data-celebration-message]");
  let pendingConfirmation = null;

  const openDialog = (dialog) => {
    if (!dialog) return;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  };

  const closeDialog = (dialog) => {
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  };

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-confirm]");
    if (!trigger || trigger.dataset.confirmed === "true") return;
    event.preventDefault();
    pendingConfirmation = trigger;
    if (confirmationMessage) confirmationMessage.textContent = trigger.dataset.confirm || "Please confirm this action.";
    openDialog(confirmationModal);
  });

  if (confirmationAccept) {
    confirmationAccept.addEventListener("click", () => {
      const trigger = pendingConfirmation;
      pendingConfirmation = null;
      closeDialog(confirmationModal);
      if (!trigger) return;
      trigger.dataset.confirmed = "true";
      if (trigger.tagName === "A") window.location.assign(trigger.href);
      else if (trigger.form) trigger.form.requestSubmit(trigger);
      window.setTimeout(() => delete trigger.dataset.confirmed, 0);
    });
  }

  document.querySelectorAll("[data-confirmation-cancel]").forEach((button) => {
    button.addEventListener("click", () => { pendingConfirmation = null; closeDialog(confirmationModal); });
  });

  document.querySelectorAll("[data-celebration-close]").forEach((button) => {
    button.addEventListener("click", () => closeDialog(celebrationModal));
  });

  window.RiseTogetherUI = {
    toast: showToast,
    celebrate: ({ title = "Beautiful progress", message = "Your effort matters. Keep growing, one gentle step at a time." } = {}) => {
      if (celebrationTitle) celebrationTitle.textContent = title;
      if (celebrationMessage) celebrationMessage.textContent = message;
      openDialog(celebrationModal);
    },
  };

  if (pageBack) {
    pageBack.addEventListener("click", () => {
      if (window.history.length > 1) {
        window.history.back();
      } else {
        window.location.href = "/";
      }
    });
  }

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
    return navigator.serviceWorker.register(`/service-worker.js?v=${APP_VERSION}`, { scope: "/" });
  };

  const showUpdateNotice = (worker) => {
    if (localStorage.getItem(dismissedUpdateKey) === APP_VERSION) return;
    waitingServiceWorker = worker;
    if (updateNotice) updateNotice.hidden = false;
  };

  if (updateNow) {
    updateNow.addEventListener("click", () => {
      if (waitingServiceWorker) {
        localStorage.setItem(dismissedUpdateKey, APP_VERSION);
        waitingServiceWorker.postMessage({ type: "SKIP_WAITING" });
      }
    });
  }

  if (updateLater && updateNotice) {
    updateLater.addEventListener("click", () => {
      localStorage.setItem(dismissedUpdateKey, APP_VERSION);
      updateNotice.hidden = true;
    });
  }

  const observeServiceWorkerRegistration = (registration) => {
    if (!registration) return;
    registration.addEventListener("updatefound", () => {
      const newWorker = registration.installing;
      if (!newWorker) return;
      newWorker.addEventListener("statechange", () => {
        if (newWorker.state === "installed" && navigator.serviceWorker.controller) {
          showUpdateNotice(newWorker);
        }
      });
    });
  };

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (window.__riseTogetherReloading) return;
      window.__riseTogetherReloading = true;
      window.location.reload();
    });
    navigator.serviceWorker.getRegistration().then(observeServiceWorkerRegistration);
    getServiceWorkerRegistration()
      .then((registration) => {
        observeServiceWorkerRegistration(registration);
        return registration.update();
      })
      .catch(() => {});
  }

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
  const navScrim = document.querySelector("[data-nav-scrim]");
  if (navToggle && navLinks) {
    const navToggleLabel = navToggle.querySelector(".nav-toggle-label");
    const closeNavigation = ({ restoreFocus = false } = {}) => {
      if (!navLinks.classList.contains("open")) return;
      navLinks.classList.remove("open");
      navToggle.classList.remove("active");
      navToggle.setAttribute("aria-expanded", "false");
      navToggle.setAttribute("aria-label", "Open menu");
      navToggle.title = "Open menu";
      if (navToggleLabel) navToggleLabel.textContent = "Menu";
      document.body.classList.remove("nav-menu-open");
      if (navScrim) navScrim.hidden = true;
      if (restoreFocus) navToggle.focus();
    };
    const openNavigation = () => {
      navLinks.classList.add("open");
      navToggle.classList.add("active");
      navToggle.setAttribute("aria-expanded", "true");
      navToggle.setAttribute("aria-label", "Close menu");
      navToggle.title = "Close menu";
      if (navToggleLabel) navToggleLabel.textContent = "Close";
      document.body.classList.add("nav-menu-open");
      if (navScrim) navScrim.hidden = false;
      const firstLink = navLinks.querySelector("a, button:not([hidden])");
      if (firstLink) window.setTimeout(() => firstLink.focus(), 120);
    };
    navToggle.addEventListener("click", () => {
      if (navLinks.classList.contains("open")) closeNavigation();
      else openNavigation();
    });
    navLinks.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => closeNavigation());
    });
    if (navScrim) navScrim.addEventListener("click", () => closeNavigation({ restoreFocus: true }));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && navLinks.classList.contains("open")) {
        closeNavigation({ restoreFocus: true });
      }
    });
    window.addEventListener("resize", () => {
      if (window.innerWidth > 900) closeNavigation();
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

  document.querySelectorAll("[data-mobile-composer]").forEach((composer) => {
    const open = composer.querySelector("[data-composer-open]");
    const cancel = composer.querySelector("[data-composer-cancel]");
    const textArea = composer.querySelector("textarea");
    const setExpanded = (expanded) => {
      composer.classList.toggle("is-expanded", expanded);
      if (expanded && textArea) textArea.focus();
    };
    if (open) open.addEventListener("click", () => setExpanded(true));
    if (cancel) cancel.addEventListener("click", () => setExpanded(false));

    const promptTarget = composer.querySelector("[data-composer-prompt]");
    const promptInput = composer.querySelector("[data-supportive-prompt-input]");
    let prompts = [];
    try {
      prompts = JSON.parse(composer.dataset.supportivePrompts || "[]");
    } catch (_error) {
      prompts = [];
    }
    if (prompts.length > 1 && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      let promptIndex = 0;
      window.setInterval(() => {
        if (document.hidden || !promptInput || promptInput.value || document.activeElement === promptInput) return;
        promptIndex = (promptIndex + 1) % prompts.length;
        promptInput.placeholder = prompts[promptIndex];
        if (promptTarget) promptTarget.textContent = prompts[promptIndex];
      }, 14000);
    }
  });

  document.querySelectorAll("[data-post-copy]").forEach((copy) => {
    const content = copy.querySelector(".post-content");
    const button = copy.querySelector("[data-read-more]");
    if (!content || !button) return;
    const syncOverflow = () => {
      if (!copy.classList.contains("is-expanded")) {
        button.hidden = content.scrollHeight <= content.clientHeight + 2;
      }
    };
    window.requestAnimationFrame(syncOverflow);
    button.addEventListener("click", () => {
      const expanded = copy.classList.toggle("is-expanded");
      button.textContent = expanded ? "Show less" : "Read more";
      button.setAttribute("aria-expanded", String(expanded));
    });
  });

  const feedLoading = document.querySelector("[data-feed-loading]");
  document.querySelectorAll("[data-feed-filters] a").forEach((link) => {
    link.addEventListener("click", () => {
      if (link.getAttribute("aria-current") === "page" || !feedLoading) return;
      feedLoading.hidden = false;
      const feedPanel = feedLoading.closest(".panel-feed");
      if (feedPanel) feedPanel.setAttribute("aria-busy", "true");
    });
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

  document.querySelectorAll("[data-live-search]").forEach((root) => {
    const input = root.querySelector("[data-live-search-input]");
    const items = Array.from(root.querySelectorAll("[data-search-item]"));
    const count = root.querySelector("[data-live-search-count]");
    const empty = root.querySelector("[data-live-search-empty]");
    if (!input || !items.length) return;
    const singular = root.dataset.liveSearchSingular || "result";
    const plural = root.dataset.liveSearchPlural || `${singular}s`;

    const updateResults = () => {
      const query = input.value.trim().toLowerCase();
      let visible = 0;
      items.forEach((item) => {
        const haystack = (item.dataset.searchText || item.textContent || "").toLowerCase();
        const matches = !query || haystack.includes(query);
        item.hidden = !matches;
        if (matches) visible += 1;
      });
      if (count) {
        const noun = visible === 1 ? singular : plural;
        count.textContent = query ? `${visible} ${noun} match "${input.value.trim()}"` : `Showing ${visible} ${noun}`;
      }
      if (empty) {
        empty.hidden = visible !== 0;
      }
    };

    input.addEventListener("input", updateResults);
    updateResults();
  });

  document.querySelectorAll("[data-remote-search]").forEach((root) => {
    const input = root.querySelector("[data-live-search-input], input[type='search'], input[name='q'], input[name='username']");
    const results = root.querySelector("[data-remote-search-results]") || root.nextElementSibling;
    const endpoint = root.dataset.remoteSearchUrl;
    if (!input || !results || !endpoint) return;
    const fillInput = root.dataset.remoteSearchFillInput === "1";
    const type = root.dataset.remoteSearchType || "results";
    let controller = null;
    let timer = null;

    const setResults = (message, items = []) => {
      results.innerHTML = "";
      if (!message && !items.length) {
        results.hidden = true;
        return;
      }
      if (message) {
        const note = document.createElement("p");
        note.className = "search-suggestion-empty";
        note.textContent = message;
        results.appendChild(note);
      }
      items.forEach((item) => {
        const element = fillInput ? document.createElement("button") : document.createElement("a");
        element.className = "search-suggestion-item";
        if (fillInput) {
          element.type = "button";
          element.addEventListener("click", () => {
            input.value = item.username || item.name || item.label || "";
            results.hidden = true;
            input.focus();
          });
        } else {
          element.href = item.url || "#";
        }
        const label = document.createElement("strong");
        label.textContent = item.label || item.display_name || item.name || item.username || "Result";
        element.appendChild(label);
        const metaText = item.meta || (item.username ? `@${item.username}` : "");
        if (metaText) {
          const meta = document.createElement("span");
          meta.textContent = metaText;
          element.appendChild(meta);
        }
        results.appendChild(element);
      });
      results.hidden = false;
    };

    const runSearch = () => {
      const query = input.value.trim();
      if (!query) {
        setResults("");
        return;
      }
      if (query.length < 1) return;
      if (controller) controller.abort();
      controller = new AbortController();
      const url = new URL(endpoint, window.location.origin);
      url.searchParams.set("q", query);
      setResults("Searching...");
      fetch(url.toString(), {
        credentials: "same-origin",
        signal: controller.signal,
      })
        .then((response) => {
          if (!response.ok) throw new Error("Search failed");
          return response.json();
        })
        .then((data) => {
          const items = Array.isArray(data.results) ? data.results : [];
          if (!items.length) {
            setResults(type === "people" ? "Username not found." : "No results found.");
            return;
          }
          setResults("", items);
        })
        .catch((error) => {
          if (error.name === "AbortError") return;
          setResults("Search is unavailable right now.");
        });
    };

    input.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(runSearch, 220);
    });
    input.addEventListener("focus", () => {
      if (input.value.trim()) runSearch();
    });
  });

  const getOrCreateMediaViewer = () => {
    let viewer = document.querySelector("[data-media-viewer]");
    if (viewer) return viewer;
    viewer = document.createElement("div");
    viewer.className = "media-viewer";
    viewer.dataset.mediaViewer = "1";
    viewer.hidden = true;
    viewer.innerHTML = `
      <button class="media-viewer-close" type="button" data-media-viewer-close aria-label="Close full view">×</button>
      <div class="media-viewer-content" data-media-viewer-content></div>
    `;
    document.body.appendChild(viewer);
    viewer.addEventListener("click", (event) => {
      if (event.target === viewer || event.target.closest("[data-media-viewer-close]")) {
        viewer.hidden = true;
        const content = viewer.querySelector("[data-media-viewer-content]");
        if (content) content.innerHTML = "";
        document.body.classList.remove("media-viewer-open");
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !viewer.hidden) {
        viewer.hidden = true;
        const content = viewer.querySelector("[data-media-viewer-content]");
        if (content) content.innerHTML = "";
        document.body.classList.remove("media-viewer-open");
      }
    });
    return viewer;
  };

  document.addEventListener("click", (event) => {
    if (event.target.closest(".media-download, .view-once-button, a, button")) return;
    const frame = event.target.closest(".media-frame");
    if (!frame || !frame.closest(".chat-log, .post-card, .post-detail, .family-section")) return;
    if (frame.classList.contains("view-once-media") && !frame.classList.contains("revealed")) return;
    const image = event.target.closest("img");
    const video = event.target.closest("video");
    if (!image && !video) return;

    const viewer = getOrCreateMediaViewer();
    const content = viewer.querySelector("[data-media-viewer-content]");
    if (!content) return;
    content.innerHTML = "";
    if (image) {
      const fullImage = document.createElement("img");
      fullImage.src = image.currentSrc || image.src;
      fullImage.alt = image.alt || "Full view image";
      content.appendChild(fullImage);
    } else if (video) {
      const fullVideo = document.createElement("video");
      fullVideo.controls = true;
      fullVideo.autoplay = true;
      fullVideo.playsInline = true;
      fullVideo.src = video.currentSrc || video.src || video.querySelector("source")?.src || "";
      content.appendChild(fullVideo);
    }
    viewer.hidden = false;
    document.body.classList.add("media-viewer-open");
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

  const familyDescriptionPanel = document.querySelector("[data-family-description-panel]");
  const familyDescriptionToggle = document.querySelector("[data-family-description-toggle]");
  const familyDescriptionClose = document.querySelector("[data-family-description-close]");
  if (familyDescriptionPanel && familyDescriptionToggle) {
    const setDescriptionOpen = (open) => {
      familyDescriptionPanel.hidden = !open;
      familyDescriptionToggle.setAttribute("aria-expanded", open ? "true" : "false");
      familyDescriptionToggle.classList.toggle("active", open);
      if (open) {
        familyDescriptionPanel.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    };
    familyDescriptionToggle.addEventListener("click", () => {
      setDescriptionOpen(familyDescriptionPanel.hidden);
    });
    if (familyDescriptionClose) {
      familyDescriptionClose.addEventListener("click", () => setDescriptionOpen(false));
    }
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

  document.querySelectorAll(".reaction-form").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      const submitter = event.submitter;
      if (!submitter || !window.fetch) return;
      event.preventDefault();
      submitter.disabled = true;
      submitter.classList.remove("is-tapping");
      void submitter.offsetWidth;
      submitter.classList.add("is-tapping");
      const formData = new FormData(form);
      if (submitter.name && submitter.value) {
        formData.set(submitter.name, submitter.value);
      }
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: formData,
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            Accept: "application/json",
          },
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          throw new Error(payload.error || "Reaction failed.");
        }
        Object.entries(payload.counts || {}).forEach(([type, count]) => {
          const choice = form.querySelector(`[data-reaction-choice][data-reaction-type="${type}"]`);
          const countTarget = choice ? choice.querySelector("[data-reaction-count]") : null;
          if (countTarget) countTarget.textContent = count;
          const button = choice ? choice.querySelector("[data-reaction-button]") : null;
          const isSelected = payload.selected_reaction === type;
          if (choice) choice.classList.toggle("is-selected", isSelected);
          if (button) {
            button.setAttribute("aria-pressed", isSelected ? "true" : "false");
            const label = button.querySelector(".reaction-label")?.textContent || "Reaction";
            button.setAttribute("aria-label", `${label}. ${count} reaction${count === 1 ? "" : "s"}${isSelected ? ". Selected" : ""}`);
          }
        });
        showToast(payload.message || "Reaction updated.");
      } catch (error) {
        showToast(error.message || "Reaction failed. Please try again.");
      } finally {
        submitter.disabled = false;
        window.setTimeout(() => submitter.classList.remove("is-tapping"), 220);
      }
    });
  });

  const reactionModal = document.querySelector("[data-reaction-modal]");
  const reactionPeople = reactionModal?.querySelector("[data-reaction-people]");
  const reactionTitle = reactionModal?.querySelector("[data-reaction-modal-title]");
  reactionModal?.querySelector("[data-reaction-modal-close]")?.addEventListener("click", () => reactionModal.close());
  reactionModal?.addEventListener("click", (event) => {
    if (event.target === reactionModal) reactionModal.close();
  });

  document.querySelectorAll("[data-reaction-list]").forEach((button) => {
    button.addEventListener("click", async () => {
      const form = button.closest(".reaction-form");
      const type = button.dataset.reactionType;
      const label = form?.querySelector(`[data-reaction-choice][data-reaction-type="${type}"] .reaction-label`)?.textContent || "Reaction";
      if (!form?.dataset.reactorsUrl || !reactionModal || !reactionPeople) return;
      if (reactionTitle) reactionTitle.textContent = `${label} reactions`;
      reactionPeople.replaceChildren();
      const loading = document.createElement("p");
      loading.className = "reaction-people-status";
      loading.textContent = "Loading people…";
      reactionPeople.append(loading);
      reactionModal.showModal();
      try {
        const url = new URL(form.dataset.reactorsUrl, window.location.origin);
        url.searchParams.set("type", type);
        const response = await fetch(url, { headers: { Accept: "application/json" } });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Could not load reactions.");
        reactionPeople.replaceChildren();
        if (!payload.people.length) {
          const empty = document.createElement("p");
          empty.className = "reaction-people-status";
          empty.textContent = "No visible reactions yet.";
          reactionPeople.append(empty);
        }
        payload.people.forEach((person) => {
          const row = document.createElement("a");
          row.className = "reaction-person";
          row.href = person.profile_url;
          row.setAttribute("role", "listitem");
          const avatar = document.createElement("img");
          avatar.src = person.avatar_url;
          avatar.alt = "";
          const text = document.createElement("span");
          const name = document.createElement("strong");
          name.textContent = person.display_name;
          const username = document.createElement("small");
          username.textContent = `@${person.username}`;
          text.append(name, username);
          row.append(avatar, text);
          reactionPeople.append(row);
        });
      } catch (error) {
        reactionPeople.replaceChildren();
        const failure = document.createElement("p");
        failure.className = "reaction-people-status";
        failure.textContent = error.message || "Could not load reactions.";
        reactionPeople.append(failure);
      }
    });
  });

  document.querySelectorAll(".comment-reaction-form").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      const submitter = event.submitter;
      if (!submitter || !window.fetch) return;
      event.preventDefault();
      submitter.disabled = true;
      const data = new FormData(form);
      data.set("reaction_type", submitter.value);
      try {
        const response = await fetch(form.action, { method: "POST", body: data, headers: { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" } });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Could not update encouragement.");
        Object.entries(payload.counts).forEach(([type, count]) => {
          const button = form.querySelector(`[data-comment-reaction="${type}"]`);
          if (!button) return;
          button.setAttribute("aria-pressed", payload.selected_reaction === type ? "true" : "false");
          const countTarget = button.querySelector("span");
          if (countTarget) countTarget.textContent = count;
        });
        showToast(payload.message);
      } catch (error) {
        showToast(error.message || "Could not update encouragement.");
      } finally {
        submitter.disabled = false;
      }
    });
  });

  document.querySelectorAll("[data-copy-post-link]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.shareUrl);
        button.textContent = "Link copied";
        showToast("Post link copied. Privacy checks still apply.");
      } catch (_error) {
        window.prompt("Copy this post link:", button.dataset.shareUrl);
      }
    });
  });

  document.querySelectorAll("[data-challenge-type]").forEach((typeSelect) => {
    const form = typeSelect.closest("form");
    const tierSelect = form?.querySelector("[data-challenge-reward-tier]");
    if (!tierSelect) return;
    const syncRewardTiers = () => {
      const allowed = new Set(typeSelect.selectedOptions[0]?.dataset.rewardTiers.split(",") || []);
      let firstAllowed = null;
      [...tierSelect.options].forEach((option) => {
        option.disabled = !allowed.has(option.value);
        option.hidden = option.disabled;
        if (!option.disabled && !firstAllowed) firstAllowed = option;
      });
      if (!allowed.has(tierSelect.value) && firstAllowed) tierSelect.value = firstAllowed.value;
    };
    typeSelect.addEventListener("change", syncRewardTiers);
    typeSelect.addEventListener("change", () => {
      const frequency = form?.querySelector("[data-completion-frequency]");
      if (frequency && typeSelect.value === "daily_check_in") {
        frequency.value = "daily";
        frequency.dispatchEvent(new Event("change"));
      }
    });
    syncRewardTiers();
  });

  document.querySelectorAll("[data-challenge-advanced-toggle]").forEach((button) => {
    const fields = button.closest("form")?.querySelector("[data-challenge-advanced]");
    if (!fields) return;
    button.addEventListener("click", () => {
      const opening = fields.hidden;
      fields.hidden = !opening;
      button.setAttribute("aria-expanded", opening ? "true" : "false");
      button.textContent = opening ? "Fewer challenge settings" : "More challenge settings";
    });
  });

  document.querySelectorAll("[data-completion-frequency]").forEach((select) => {
    const customField = select.closest("form")?.querySelector("[data-custom-frequency]");
    const customInput = customField?.querySelector("input");
    const syncFrequency = () => {
      const isCustom = select.value === "custom";
      if (customField) customField.hidden = !isCustom;
      if (customInput) customInput.required = isCustom;
    };
    select.addEventListener("change", syncFrequency);
    syncFrequency();
  });

  document.querySelectorAll("[data-mandatory-challenge]").forEach((checkbox) => {
    const limitField = checkbox.closest("form")?.querySelector("[data-participant-limit]");
    const limitInput = limitField?.querySelector("input");
    const syncMandatory = () => {
      if (limitField) limitField.hidden = checkbox.checked;
      if (limitInput && checkbox.checked) limitInput.value = "";
    };
    checkbox.addEventListener("change", syncMandatory);
    syncMandatory();
  });

  document.querySelectorAll("[data-challenge-completion-form]").forEach((form) => {
    const input = form.querySelector("[data-challenge-evidence]");
    const preview = form.querySelector("[data-challenge-evidence-preview]");
    const remove = form.querySelector("[data-challenge-evidence-remove]");
    const progress = form.querySelector("[data-challenge-upload-progress]");
    const bar = form.querySelector("[data-challenge-upload-bar]");
    const progressLabel = form.querySelector("[data-challenge-upload-label]");
    let previewUrl = "";

    const clearEvidence = () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = "";
      if (input) input.value = "";
      if (preview) preview.replaceChildren();
      if (preview) preview.hidden = true;
      if (remove) remove.hidden = true;
    };

    input?.addEventListener("change", () => {
      if (!input.files?.length || !preview) return clearEvidence();
      const file = input.files[0];
      const maximum = Number(input.dataset.maxBytes || 0);
      if (maximum && file.size > maximum) {
        clearEvidence();
        showToast(`That file is larger than ${Math.round(maximum / 1024 / 1024)} MB.`);
        return;
      }
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(file);
      preview.replaceChildren();
      let media;
      if (file.type.startsWith("image/")) {
        media = document.createElement("img");
        media.alt = "Evidence preview";
      } else if (file.type.startsWith("video/")) {
        media = document.createElement("video");
        media.controls = true;
        media.muted = true;
      } else if (file.type.startsWith("audio/")) {
        media = document.createElement("audio");
        media.controls = true;
      } else {
        media = document.createElement("span");
        media.textContent = `${file.name} · ${Math.max(1, Math.round(file.size / 1024))} KB`;
      }
      if (media instanceof HTMLMediaElement || media instanceof HTMLImageElement) media.src = previewUrl;
      preview.append(media);
      preview.hidden = false;
      if (remove) remove.hidden = false;
    });
    remove?.addEventListener("click", clearEvidence);

    form.addEventListener("submit", (event) => {
      if (!window.XMLHttpRequest || !form.reportValidity()) return;
      event.preventDefault();
      const submitter = event.submitter || form.querySelector('[type="submit"]');
      if (submitter) submitter.disabled = true;
      if (progress) progress.hidden = false;
      if (bar) bar.style.width = "0%";
      if (progressLabel) progressLabel.textContent = "Uploading… 0%";
      const xhr = new XMLHttpRequest();
      xhr.open("POST", form.action);
      xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.addEventListener("progress", (uploadEvent) => {
        if (!uploadEvent.lengthComputable) return;
        const percentage = Math.round((uploadEvent.loaded / uploadEvent.total) * 100);
        if (bar) bar.style.width = `${percentage}%`;
        if (progressLabel) progressLabel.textContent = percentage < 100 ? `Uploading… ${percentage}%` : "Processing completion…";
      });
      xhr.addEventListener("load", () => {
        const contentType = xhr.getResponseHeader("Content-Type") || "";
        if (!contentType.includes("application/json")) {
          window.location.assign(xhr.responseURL || form.action);
          return;
        }
        let payload;
        try { payload = JSON.parse(xhr.responseText); } catch (_error) { payload = {}; }
        if (xhr.status >= 400 || !payload.ok) {
          showToast(payload.error || "Completion could not be submitted.");
          if (submitter) submitter.disabled = false;
          if (progress) progress.hidden = true;
          return;
        }
        if (payload.status === "pending") {
          showToast(payload.message || "Completion submitted for approval.");
          window.setTimeout(() => window.location.assign(payload.redirect_url), 900);
          return;
        }
        const celebration = payload.celebration || {};
        const closeButton = document.querySelector("[data-celebration-close]");
        if (closeButton) closeButton.textContent = payload.ask_to_share ? "Choose where to share" : "See my progress";
        window.RiseTogetherUI?.celebrate({ title: celebration.title, message: celebration.message });
        closeButton?.addEventListener("click", () => window.location.assign(payload.redirect_url), { once: true });
      });
      xhr.addEventListener("error", () => {
        showToast("Upload interrupted. Your completion was not submitted.");
        if (submitter) submitter.disabled = false;
        if (progress) progress.hidden = true;
      });
      xhr.send(new FormData(form));
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

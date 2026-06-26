document.querySelectorAll("[data-sidebar-open]").forEach((button) => {
  button.addEventListener("click", () => document.body.classList.add("sidebar-open"));
});

document.querySelectorAll("[data-sidebar-close]").forEach((button) => {
  button.addEventListener("click", () => document.body.classList.remove("sidebar-open"));
});

document.querySelectorAll("[data-single-submit]").forEach((form) => {
  form.addEventListener("submit", () => {
    const button = form.querySelector('button[type="submit"]');
    if (button) {
      button.disabled = true;
      button.dataset.originalText = button.textContent;
      button.textContent = "РЎРѕС…СЂР°РЅСЏРµРјвЂ¦";
    }
  });
});

document.querySelectorAll("[data-live-filter]").forEach((form) => {
  const search = form.querySelector("[data-live-search]");
  let timer;
  const serializeForm = () => new URLSearchParams(new FormData(form)).toString();
  let lastSubmitted = serializeForm();

  const submitFilter = () => {
    const current = serializeForm();
    if (current === lastSubmitted) {
      return;
    }
    lastSubmitted = current;
    if (search) {
      sessionStorage.setItem(
        "asset-chain-live-search",
        JSON.stringify({
          path: window.location.pathname,
          name: search.name,
        }),
      );
    }
    form.requestSubmit();
  };

  if (search) {
    search.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(submitFilter, 180);
    });
  }

  form.querySelectorAll("select").forEach((select) => {
    select.addEventListener("change", submitFilter);
  });
});

try {
  const savedSearch = JSON.parse(
    sessionStorage.getItem("asset-chain-live-search") || "null",
  );
  if (savedSearch?.path === window.location.pathname) {
    const input = document.querySelector(
      `[data-live-filter] [name="${savedSearch.name}"]`,
    );
    if (input) {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
    sessionStorage.removeItem("asset-chain-live-search");
  }
} catch {
  sessionStorage.removeItem("asset-chain-live-search");
}

document.querySelectorAll("[data-row-href]").forEach((row) => {
  const open = () => {
    window.location.href = row.dataset.rowHref;
  };
  row.addEventListener("click", (event) => {
    if (event.target.closest("a, button, form, input, select, textarea")) {
      return;
    }
    open();
  });
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });
});

const focusFirstModalField = (root = document) => {
  const field =
    root.querySelector(
      'form input:not([type="hidden"]):not([disabled]), form select:not([disabled]), form textarea:not([disabled])',
    ) ||
    root.querySelector(
      'input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled])',
    );
  if (field) {
    field.focus();
    if (typeof field.select === "function" && field.tagName === "INPUT") {
      field.select();
    }
  }
};

if (document.querySelector(".modal-shell")) {
  focusFirstModalField();
}

document.querySelectorAll("[data-focus-modal]").forEach((modalElement) => {
  modalElement.addEventListener("shown.bs.modal", () => {
    focusFirstModalField(modalElement);
  });
});
const remoteModalElement = document.getElementById("appRemoteModal");
if (remoteModalElement && window.bootstrap) {
  const remoteModal = new bootstrap.Modal(remoteModalElement);
  const frame = remoteModalElement.querySelector("[data-modal-frame]");
  const title = remoteModalElement.querySelector("[data-modal-title]");
  document.querySelectorAll("[data-modal-url]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      title.textContent = link.dataset.modalTitle || link.textContent.trim() || "Р¤РѕСЂРјР°";
      const url = new URL(link.href, window.location.href);
      url.searchParams.set("modal", "1");
      frame.src = url.toString();
      remoteModal.show();
      remoteModalElement.addEventListener(
        "shown.bs.modal",
        () => {
          frame.focus();
        },
        { once: true },
      );
    });
  });
  frame.addEventListener("load", () => {
    try {
      const frameUrl = new URL(frame.contentWindow.location.href);
      if (frameUrl.href !== "about:blank" && !frameUrl.searchParams.has("modal")) {
        remoteModal.hide();
        window.location.reload();
        return;
      }
      frame.contentWindow.focus();
      focusFirstModalField(frame.contentDocument);
    } catch {
      // Ignore cross-origin/blank iframe states.
    }
  });
  remoteModalElement.addEventListener("hidden.bs.modal", () => {
    frame.src = "about:blank";
  });
}

const telegramModalElement = document.getElementById("telegramLinkModal");
if (telegramModalElement && window.bootstrap) {
  const telegramModal = new bootstrap.Modal(telegramModalElement);
  const input = telegramModalElement.querySelector("#telegramLinkInput");
  const expiry = telegramModalElement.querySelector("[data-telegram-expiry]");
  document.querySelectorAll("[data-telegram-link]").forEach((button) => {
    button.addEventListener("click", () => {
      input.value = button.dataset.telegramLink;
      expiry.textContent = button.dataset.telegramExpires || "24 С‡Р°СЃР°";
      telegramModal.show();
      telegramModalElement.addEventListener("shown.bs.modal", () => input.focus(), { once: true });
    });
  });
  telegramModalElement.querySelector("[data-copy-telegram-link]")?.addEventListener("click", async () => {
    await navigator.clipboard.writeText(input.value);
  });
}

const scannerVideo = document.querySelector("[data-scanner-video]");
if (scannerVideo) {
  const status = document.querySelector("[data-scanner-status]");
  const startButton = document.querySelector("[data-scanner-start]");
  const stopButton = document.querySelector("[data-scanner-stop]");
  let stream;
  let detector;
  let scanning = false;
  let serverDetecting = false;

  const getCsrfToken = () => {
    const inputToken = document.querySelector('input[name="csrfmiddlewaretoken"]')?.value;
    if (inputToken) {
      return inputToken;
    }
    const cookie = document.cookie
      .split(";")
      .map((item) => item.trim())
      .find((item) => item.startsWith("csrftoken="));
    return cookie ? decodeURIComponent(cookie.split("=").slice(1).join("=")) : "";
  };

  const stopScanner = () => {
    scanning = false;
    serverDetecting = false;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    scannerVideo.srcObject = null;
  };

  const captureFrame = () =>
    new Promise((resolve) => {
      if (!scannerVideo.videoWidth || !scannerVideo.videoHeight) {
        resolve(null);
        return;
      }
      const canvas = document.createElement("canvas");
      canvas.width = scannerVideo.videoWidth;
      canvas.height = scannerVideo.videoHeight;
      canvas.getContext("2d").drawImage(scannerVideo, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(resolve, "image/jpeg", 0.85);
    });

  const detectOnServer = async () => {
    if (serverDetecting) {
      return null;
    }
    serverDetecting = true;
    try {
      const blob = await captureFrame();
      if (!blob) {
        return null;
      }
      const formData = new FormData();
      formData.append("image", blob, "frame.jpg");
      const response = await fetch("/scanner/detect/", {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() },
        body: formData,
      });
      if (!response.ok) {
        return null;
      }
      const data = await response.json();
      return data.barcode || null;
    } finally {
      serverDetecting = false;
    }
  };

  const scanLoop = async () => {
    if (!scanning) {
      return;
    }
    try {
      let value = null;
      if (detector) {
        const codes = await detector.detect(scannerVideo);
        value = codes.length ? codes[0].rawValue : null;
      } else {
        value = await detectOnServer();
      }
      if (value) {
        stopScanner();
        window.location.href = `/products/?q=${encodeURIComponent(value)}`;
        return;
      }
    } catch {
      status.textContent = "Не удалось распознать кадр. Попробуйте поднести камеру ближе.";
    }
    window.setTimeout(scanLoop, detector ? 250 : 900);
  };

  startButton?.addEventListener("click", async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      status.textContent = "Камера недоступна в этом режиме. Откройте сайт через HTTPS или localhost, либо введите код вручную.";
      return;
    }
    detector = null;
    if ("BarcodeDetector" in window) {
      try {
        detector = new BarcodeDetector({ formats: ["ean_13", "code_128"] });
      } catch {
        detector = null;
      }
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
      });
      scannerVideo.srcObject = stream;
      await scannerVideo.play();
      scanning = true;
      status.textContent = detector
        ? "Камера запущена. Наведите на штрихкод."
        : "Камера запущена. Сканирование выполняется сервером, держите штрихкод в кадре.";
      scanLoop();
    } catch {
      status.textContent = "Нет доступа к камере. Разрешите доступ, откройте сайт через HTTPS или введите код вручную.";
    }
  });
  stopButton?.addEventListener("click", stopScanner);
}


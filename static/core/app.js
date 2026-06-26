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
      button.textContent = "Сохраняем…";
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
      title.textContent = link.dataset.modalTitle || link.textContent.trim() || "Форма";
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
      expiry.textContent = button.dataset.telegramExpires || "24 часа";
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

  const stopScanner = () => {
    scanning = false;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    scannerVideo.srcObject = null;
  };

  const scanLoop = async () => {
    if (!scanning || !detector) {
      return;
    }
    try {
      const codes = await detector.detect(scannerVideo);
      if (codes.length) {
        const value = codes[0].rawValue;
        stopScanner();
        window.location.href = `/products/?q=${encodeURIComponent(value)}`;
        return;
      }
    } catch {
      status.textContent = "Не удалось распознать кадр. Попробуйте поднести камеру ближе.";
    }
    window.setTimeout(scanLoop, 250);
  };

  startButton?.addEventListener("click", async () => {
    if (!("BarcodeDetector" in window)) {
      status.textContent = "Этот браузер не поддерживает сканирование камерой. Введите штрихкод вручную.";
      return;
    }
    detector = new BarcodeDetector({ formats: ["ean_13", "code_128"] });
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
      });
      scannerVideo.srcObject = stream;
      await scannerVideo.play();
      scanning = true;
      status.textContent = "Камера запущена. Наведите на штрихкод.";
      scanLoop();
    } catch {
      status.textContent = "Нет доступа к камере. Разрешите доступ или введите код вручную.";
    }
  });
  stopButton?.addEventListener("click", stopScanner);
}


(() => {
  const dropzone = document.getElementById("dropzone");
  const picker = document.getElementById("picker");
  const browse = document.getElementById("browse");
  const filelist = document.getElementById("filelist");
  const convertBtn = document.getElementById("convert");
  const clearBtn = document.getElementById("clear");
  const overlay = document.getElementById("overlay");
  const errorBox = document.getElementById("error");
  const ocrBadge = document.getElementById("ocr-badge");

  /** @type {File[]} */
  let files = [];

  function fileKey(f) {
    return `${f.name}|${f.size}|${f.lastModified}`;
  }

  function humanSize(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  function render() {
    filelist.innerHTML = "";
    for (const f of files) {
      const li = document.createElement("li");
      const meta = document.createElement("span");
      meta.textContent = `${f.name} — ${humanSize(f.size)}`;
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "remove";
      rm.setAttribute("aria-label", `Remove ${f.name}`);
      rm.textContent = "×";
      rm.addEventListener("click", () => {
        files = files.filter((x) => fileKey(x) !== fileKey(f));
        render();
      });
      li.appendChild(meta);
      li.appendChild(rm);
      filelist.appendChild(li);
    }
    const empty = files.length === 0;
    convertBtn.disabled = empty;
    clearBtn.disabled = empty;
  }

  function addFiles(list) {
    const existing = new Set(files.map(fileKey));
    for (const f of list) {
      const k = fileKey(f);
      if (!existing.has(k)) {
        existing.add(k);
        files.push(f);
      }
    }
    render();
  }

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.hidden = false;
  }

  function clearError() {
    errorBox.textContent = "";
    errorBox.hidden = true;
  }

  function setBusy(busy) {
    overlay.hidden = !busy;
    convertBtn.disabled = busy || files.length === 0;
  }

  function filenameFromDisposition(header) {
    if (!header) return null;
    const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
    return m ? decodeURIComponent(m[1]) : null;
  }

  // --- drag and drop ---
  ["dragenter", "dragover"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });
  });
  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("dragover");
  });
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer && e.dataTransfer.files) {
      addFiles(e.dataTransfer.files);
    }
  });
  dropzone.addEventListener("click", (e) => {
    if (e.target === dropzone || e.target.classList.contains("dz-text") || e.target.classList.contains("dz-hint")) {
      picker.click();
    }
  });

  // --- browse button ---
  browse.addEventListener("click", (e) => {
    e.stopPropagation();
    picker.click();
  });
  picker.addEventListener("change", () => {
    addFiles(picker.files);
    picker.value = "";
  });

  // --- clear ---
  clearBtn.addEventListener("click", () => {
    files = [];
    clearError();
    render();
  });

  // --- submit ---
  convertBtn.addEventListener("click", async () => {
    if (files.length === 0) return;
    clearError();
    setBusy(true);

    try {
      const fd = new FormData();
      for (const f of files) fd.append("files", f, f.name);

      const resp = await fetch("/convert", { method: "POST", body: fd });

      if (!resp.ok) {
        let detail = `Request failed (${resp.status})`;
        try {
          const j = await resp.json();
          if (j && j.detail) detail = j.detail;
        } catch (_) {}
        showError(detail);
        return;
      }

      const blob = await resp.blob();
      const filename =
        filenameFromDisposition(resp.headers.get("Content-Disposition")) ||
        "converted.zip";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      showError(err && err.message ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  });

  // --- health badge ---
  document.addEventListener("DOMContentLoaded", async () => {
    try {
      const r = await fetch("/health");
      const j = await r.json();
      ocrBadge.textContent = `OCR: ${j.ocr || "unknown"}`;
    } catch (_) {
      ocrBadge.textContent = "OCR: unavailable";
    }
  });

  render();
})();

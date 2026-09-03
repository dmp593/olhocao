const mediaChunkSize = 5 * 1024 * 1024;

function csrfToken() {
  const cookie = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("csrftoken="));
  return cookie ? decodeURIComponent(cookie.slice("csrftoken=".length)) : "";
}

function createMediaViewer(closeLabel) {
  let dialog = document.getElementById("media-viewer");
  if (dialog) return dialog;

  dialog = document.createElement("dialog");
  dialog.id = "media-viewer";
  dialog.className = "ui-media-viewer";

  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.className = "ui-media-viewer-close";
  closeButton.setAttribute("aria-label", closeLabel);
  closeButton.textContent = "✕";
  closeButton.addEventListener("click", () => dialog.close());

  const body = document.createElement("div");
  body.dataset.mediaViewerBody = "";
  body.className = "ui-media-viewer-body";
  dialog.append(closeButton, body);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => {
    body.querySelector("video")?.pause();
    body.replaceChildren();
  });
  document.body.appendChild(dialog);
  return dialog;
}

function showMedia(media, closeLabel) {
  const dialog = createMediaViewer(closeLabel);
  const body = dialog.querySelector("[data-media-viewer-body]");
  const preview = document.createElement(media.tagName === "VIDEO" ? "video" : "img");
  preview.src = media.currentSrc || media.src;
  preview.className = "ui-media-viewer-preview";
  if (preview instanceof HTMLVideoElement) {
    preview.controls = true;
    preview.playsInline = true;
  } else {
    preview.alt = media.alt || "";
  }
  body.replaceChildren(preview);
  dialog.showModal();
}

function progressReporter(container, filename) {
  const row = document.createElement("div");
  row.className = "overflow-hidden rounded-lg bg-primary-100";
  const label = document.createElement("div");
  label.className = "truncate px-2 py-1 text-xs text-primary-700";
  label.textContent = filename;
  const track = document.createElement("div");
  track.className = "h-1.5 bg-primary-200";
  const bar = document.createElement("div");
  bar.className = "h-1.5 bg-accent-600 transition-[width]";
  bar.style.width = "0%";
  track.appendChild(bar);
  row.append(label, track);
  container.appendChild(row);

  return {
    complete(percent) {
      bar.style.width = `${percent}%`;
    },
    fail(message) {
      label.textContent = `${filename}: ${message}`;
      label.classList.add("text-red-700");
      bar.classList.replace("bg-accent-600", "bg-red-700");
    },
  };
}

function appendMedia(section, data) {
  const gallery = section.querySelector("[data-media-gallery]");
  gallery.querySelector("[data-media-empty]")?.remove();

  const figure = document.createElement("figure");
  figure.className = "relative overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-primary-200";
  const isVideo = String(data.content_type || "").startsWith("video");
  const preview = document.createElement(isVideo ? "video" : "img");
  preview.src = data.url;
  preview.className = "h-32 w-full cursor-zoom-in object-cover";
  if (preview instanceof HTMLVideoElement) {
    preview.preload = "metadata";
    preview.muted = true;
  } else {
    preview.loading = "lazy";
    preview.alt = data.filename || "";
  }

  const caption = document.createElement("figcaption");
  caption.className = "truncate px-2 py-1 text-xs text-primary-600";
  caption.textContent = data.filename || "";

  const deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.dataset.deleteUrl = data.delete_url;
  deleteButton.className = "absolute right-1 top-1 rounded-full bg-red-700 px-2 py-1 text-xs text-white transition hover:bg-red-800";
  deleteButton.setAttribute("aria-label", section.dataset.deleteLabel);
  deleteButton.textContent = "✕";
  figure.append(preview, caption, deleteButton);
  gallery.prepend(figure);
}

async function uploadFile(section, file) {
  const progress = progressReporter(section.querySelector("[data-media-progress]"), file.name);
  const totalChunks = Math.ceil(file.size / mediaChunkSize) || 1;
  const uploadId = crypto.randomUUID();

  try {
    for (let index = 0; index < totalChunks; index += 1) {
      const start = index * mediaChunkSize;
      const formData = new FormData();
      formData.append("upload_id", uploadId);
      formData.append("chunk_index", String(index));
      formData.append("total_chunks", String(totalChunks));
      formData.append("filename", file.name);
      formData.append("content_type", file.type);
      formData.append("size", String(file.size));
      formData.append("chunk", file.slice(start, Math.min(start + mediaChunkSize, file.size)), file.name);

      const response = await fetch(section.dataset.uploadUrl, {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken() },
        body: formData,
      });
      if (!response.ok) throw new Error(section.dataset.actionError);

      const data = await response.json();
      progress.complete(Math.round(((index + 1) / totalChunks) * 100));
      if (data.completed) appendMedia(section, data);
    }
  } catch (error) {
    progress.fail(error.message || section.dataset.actionError);
  }
}

function initializeMediaSection(section) {
  const gallery = section.querySelector("[data-media-gallery]");
  gallery.addEventListener("click", async (event) => {
    const deleteButton = event.target.closest("[data-delete-url]");
    if (deleteButton) {
      if (!window.confirm(section.dataset.deleteConfirmation)) return;
      deleteButton.disabled = true;
      try {
        const response = await fetch(deleteButton.dataset.deleteUrl, {
          method: "POST",
          headers: { "X-CSRFToken": csrfToken() },
        });
        if (!response.ok) throw new Error(section.dataset.actionError);
        deleteButton.closest("figure")?.remove();
      } catch (error) {
        window.alert(error.message || section.dataset.actionError);
      } finally {
        deleteButton.disabled = false;
      }
      return;
    }

    const media = event.target.closest("img, video");
    if (media) showMedia(media, section.dataset.closeLabel);
  });

  if (section.dataset.canEdit !== "true") return;
  const dropZone = section.querySelector("[data-media-drop-zone]");
  const input = section.querySelector("[data-media-input]");
  if (!dropZone || !input) return;

  dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropZone.classList.add("ring-2", "ring-accent-400");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("ring-2", "ring-accent-400"));
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("ring-2", "ring-accent-400");
    for (const file of event.dataTransfer.files) uploadFile(section, file);
  });
  input.addEventListener("change", () => {
    for (const file of input.files) uploadFile(section, file);
    input.value = "";
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-stay-media]").forEach(initializeMediaSection);
});

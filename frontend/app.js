const API = "http://127.0.0.1:8000";

const $ = (id) => document.getElementById(id);
const urlInput = $("urlInput");
const analyzeBtn = $("analyzeBtn");
const downloadBtn = $("downloadBtn");
const cancelBtn = $("cancelBtn");
const result = $("result");
const message = $("message");
const quality = $("quality");
const format = $("format");
const progressWrap = $("progressWrap");
const bar = $("bar");
const percent = $("percent");
const statusText = $("statusText");
const fileBtn = $("fileBtn");
const themeBtn = $("themeBtn");

let currentUrl = "";
let currentJob = null;
let formats = [];

function showMessage(text, error=false) {
  message.textContent = text;
  message.style.color = error ? "#f87171" : "";
}

function humanSize(bytes) {
  if (!bytes) return "";
  const units = ["B","KB","MB","GB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length-1) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${units[i]}`;
}

function formatLabel(f) {
  const q = `${f.height}p`;
  const ext = (f.ext || "video").toUpperCase();
  const audio = f.has_audio ? " + audio" : " + audio merge";
  const size = f.filesize ? ` • ${humanSize(f.filesize)}` : "";
  return `${q} • ${ext}${audio}${size}`;
}

analyzeBtn.onclick = async () => {
  const url = urlInput.value.trim();
  if (!url) return showMessage("Masukkan URL video terlebih dahulu.", true);

  analyzeBtn.disabled = true;
  analyzeBtn.textContent = "Analyzing...";
  result.classList.add("hidden");
  fileBtn.classList.add("hidden");
  showMessage("Menganalisis URL...");

  try {
    const res = await fetch(`${API}/api/download`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({url})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Analisis gagal.");

    currentUrl = url;
    formats = data.formats || [];
    $("platform").textContent = data.platform || "";
    $("title").textContent = data.title || "Video";
    $("uploader").textContent = data.uploader ? `Creator: ${data.uploader}` : "";
    $("duration").textContent = data.duration ? `Duration: ${Math.round(data.duration)} detik` : "";
    $("thumb").src = data.thumbnail || "";
    $("thumb").style.display = data.thumbnail ? "block" : "none";

    quality.innerHTML = "";
    formats.forEach((f, idx) => {
      const option = document.createElement("option");
      option.value = f.format_id;
      option.textContent = formatLabel(f);
      if (idx === 0) option.selected = true;
      quality.appendChild(option);
    });

    if (!formats.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Best available";
      quality.appendChild(option);
    }

    result.classList.remove("hidden");
    showMessage("Video berhasil dianalisis.");
  } catch (err) {
    showMessage(err.message, true);
  } finally {
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analyze";
  }
};

downloadBtn.onclick = async () => {
  if (!currentUrl) return;
  downloadBtn.disabled = true;
  cancelBtn.classList.remove("hidden");
  progressWrap.classList.remove("hidden");
  fileBtn.classList.add("hidden");
  bar.style.width = "0%";
  percent.textContent = "0%";
  statusText.textContent = "Creating job...";

  try {
    const res = await fetch(`${API}/download`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({
        url: currentUrl,
        format_id: quality.value || null,
        container: format.value
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Gagal membuat download job.");
    currentJob = data.job_id;
    pollJob(currentJob);
  } catch (err) {
    showMessage(err.message, true);
    resetDownloadUI();
  }
};

async function pollJob(jobId) {
  const timer = setInterval(async () => {
    try {
      const res = await fetch(`${API}/download/${jobId}`);
      const data = await res.json();

      bar.style.width = `${data.progress || 0}%`;
      percent.textContent = `${Math.round(data.progress || 0)}%`;
      statusText.textContent = data.message || data.status;

      if (data.status === "COMPLETED") {
        clearInterval(timer);
        bar.style.width = "100%";
        percent.textContent = "100%";
        statusText.textContent = "Completed";
        fileBtn.href = `${API}/download/${jobId}/file`;
        fileBtn.classList.remove("hidden");
        showMessage("Video selesai diproses.");
        resetDownloadUI(false);
      }

      if (["FAILED","CANCELLED"].includes(data.status)) {
        clearInterval(timer);
        showMessage(data.error || data.message || "Download gagal.", true);
        resetDownloadUI();
      }
    } catch (err) {
      clearInterval(timer);
      showMessage("Tidak dapat membaca status download.", true);
      resetDownloadUI();
    }
  }, 800);
}

cancelBtn.onclick = async () => {
  if (!currentJob) return;
  try {
    await fetch(`${API}/download/${currentJob}/cancel`, {method:"POST"});
  } finally {
    resetDownloadUI();
    showMessage("Permintaan pembatalan dikirim.");
  }
};

function resetDownloadUI(enable=true) {
  downloadBtn.disabled = !enable;
  cancelBtn.classList.add("hidden");
  currentJob = null;
}

themeBtn.onclick = () => {
  document.body.classList.toggle("light");
  themeBtn.textContent = document.body.classList.contains("light") ? "🌙" : "☀️";
};

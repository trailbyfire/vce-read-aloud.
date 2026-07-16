/* VCE Read Aloud — frontend.
 *
 * Playback model: the server synthesizes a WAV (with word timings) for the
 * current essay + voice settings; the browser plays it through an <audio>
 * element and highlights words by matching currentTime against the timing
 * list. The same file (or an MP3 conversion) is what gets exported, so
 * what you hear is exactly what students get.
 */

"use strict";

// ---------- tiny helpers ----------

const $ = (sel) => document.querySelector(sel);

async function api(path, options = {}) {
  if (options.json !== undefined) {
    options.body = JSON.stringify(options.json);
    options.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    delete options.json;
  }
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* keep statusText */ }
    throw new Error(detail);
  }
  return res.json();
}

let toastTimer = null;
function toast(msg, isError = false, ms = 3500) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

function fmtTime(seconds) {
  if (!isFinite(seconds)) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

// ---------- state ----------

const state = {
  classes: [],
  students: [],
  essays: [],
  essay: null,        // full essay currently open
  synth: null,        // {audio_url, timings, paragraphs, duration_ms}
  synthKey: null,     // settings fingerprint the synth was made with
  settings: null,
  wordSpans: [],      // spans in document order with char ranges
  paraEls: [],
  activeWordIdx: -1,
  activeParaIdx: -1,
  generating: false,
};

const player = $("#player");

// ---------- library ----------

async function refreshClasses() {
  state.classes = await api("/api/classes");
  const opts = ['<option value="">All classes</option>']
    .concat(state.classes.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`));
  const sel = $("#filter-class");
  const prev = sel.value;
  sel.innerHTML = opts.join("");
  if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
}

async function refreshStudents() {
  const classId = $("#filter-class").value;
  state.students = await api(`/api/students${classId ? `?class_id=${classId}` : ""}`);
  const sel = $("#filter-student");
  const prev = sel.value;
  sel.innerHTML = ['<option value="">All students</option>']
    .concat(state.students.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`)).join("");
  if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
}

async function refreshEssays() {
  const params = new URLSearchParams();
  const classId = $("#filter-class").value;
  const studentId = $("#filter-student").value;
  const q = $("#search").value.trim();
  if (classId) params.set("class_id", classId);
  if (studentId) params.set("student_id", studentId);
  if (q) params.set("q", q);
  state.essays = await api(`/api/essays?${params}`);
  renderEssayList();
}

function renderEssayList() {
  const ul = $("#essay-list");
  if (!state.essays.length) {
    ul.innerHTML = '<li class="empty">No essays yet.<br>Press <kbd>N</kbd> to add one.</li>';
    return;
  }
  ul.innerHTML = state.essays.map((e) => `
    <li data-id="${e.id}" class="${state.essay && state.essay.id === e.id ? "active" : ""}">
      <span class="item-title">${esc(e.title)}</span>
      <span class="item-sub">${esc(e.student_name)} · ${esc(e.class_name)}</span>
    </li>`).join("");
}

$("#essay-list").addEventListener("click", (ev) => {
  const li = ev.target.closest("li[data-id]");
  if (li) openEssay(Number(li.dataset.id));
});

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

// ---------- reader ----------

async function openEssay(id) {
  stopPlayback();
  state.essay = await api(`/api/essays/${id}`);
  state.synth = null;
  renderEssayList();
  renderEssay();
}

function renderEssay() {
  const essay = state.essay;
  $("#empty-state").hidden = !!essay;
  $("#reader").hidden = !essay;
  $("#playbar").hidden = !essay;
  if (!essay) return;

  $("#essay-title").textContent = essay.title;
  $("#essay-meta").textContent =
    `${essay.student_name} · ${essay.class_name}` +
    (essay.source_filename ? ` · imported from ${essay.source_filename}` : "");

  // Build paragraphs of word-spans, each span carrying its char range in
  // the raw content so timings (which use content offsets) can find it.
  const container = $("#essay-text");
  container.innerHTML = "";
  state.wordSpans = [];
  state.paraEls = [];
  const content = essay.content;

  essay.paragraphs.forEach((para, pIdx) => {
    const p = document.createElement("p");
    p.dataset.para = pIdx;
    const text = content.slice(para.start, para.end);
    const wordRe = /\S+/g;
    let last = 0, m;
    while ((m = wordRe.exec(text)) !== null) {
      if (m.index > last) p.appendChild(document.createTextNode(text.slice(last, m.index)));
      const span = document.createElement("span");
      span.className = "w";
      span.textContent = m[0];
      span.dataset.s = para.start + m.index;
      span.dataset.e = para.start + m.index + m[0].length;
      p.appendChild(span);
      state.wordSpans.push(span);
      last = m.index + m[0].length;
    }
    if (last < text.length) p.appendChild(document.createTextNode(text.slice(last)));
    container.appendChild(p);
    state.paraEls.push(p);
  });
  state.activeWordIdx = -1;
  state.activeParaIdx = -1;
  updatePlaybar();
}

$("#essay-text").addEventListener("click", async (ev) => {
  if (!state.essay) return;
  const wordSpan = ev.target.closest("span.w");
  const para = ev.target.closest("p[data-para]");
  await ensureSynth();
  if (!state.synth) return;
  if (wordSpan) {
    seekToChar(Number(wordSpan.dataset.s));
  } else if (para) {
    const info = state.synth.paragraphs[Number(para.dataset.para)];
    if (info && info.offset_ms != null) seekMs(info.offset_ms);
  }
  player.play();
});

// ---------- synthesis + playback ----------

function settingsKey() {
  const s = state.settings || {};
  return [s.engine, s.engine === "azure" ? s.azure_voice : s.voice_id, s.rate, s.pitch, s.volume].join("|");
}

async function ensureSynth() {
  if (!state.essay) return null;
  if (state.synth && state.synthKey === settingsKey()) return state.synth;
  if (state.generating) return null;
  state.generating = true;
  setStatus("Generating audio…");
  try {
    const result = await api(`/api/essays/${state.essay.id}/synthesize`, { method: "POST", json: { format: "wav" } });
    state.synth = result;
    state.synthKey = settingsKey();
    player.src = result.audio_url;
    setStatus("");
    return result;
  } catch (err) {
    setStatus("");
    toast(`Couldn't generate audio: ${err.message}`, true, 6000);
    return null;
  } finally {
    state.generating = false;
  }
}

function setStatus(text) { $("#playbar-status").textContent = text; }

async function togglePlay() {
  if (!state.essay) return;
  if (!player.paused) { player.pause(); return; }
  const synth = await ensureSynth();
  if (synth) player.play();
}

function stopPlayback() {
  player.pause();
  player.currentTime = 0;
  clearHighlights();
}

function clearHighlights() {
  if (state.activeWordIdx >= 0 && state.wordSpans[state.activeWordIdx]) {
    state.wordSpans[state.activeWordIdx].classList.remove("speaking");
  }
  if (state.activeParaIdx >= 0 && state.paraEls[state.activeParaIdx]) {
    state.paraEls[state.activeParaIdx].classList.remove("active-para");
  }
  state.activeWordIdx = -1;
  state.activeParaIdx = -1;
}

function seekMs(ms) { player.currentTime = ms / 1000; }

function seekToChar(charPos) {
  const timings = state.synth ? state.synth.timings : [];
  let best = null;
  for (const t of timings) {
    if (charPos >= t.start && charPos < t.end) { best = t; break; }
    if (t.start >= charPos) { best = best || t; break; }
    best = t;
  }
  if (best) seekMs(best.ms);
}

function currentParagraphIdx() {
  const paras = state.synth ? state.synth.paragraphs : [];
  const nowMs = player.currentTime * 1000;
  let idx = 0;
  paras.forEach((p, i) => { if (p.offset_ms != null && p.offset_ms <= nowMs + 50) idx = i; });
  return idx;
}

async function jumpParagraph(delta) {
  const synth = await ensureSynth();
  if (!synth) return;
  const paras = synth.paragraphs;
  let idx = currentParagraphIdx() + delta;
  idx = Math.max(0, Math.min(paras.length - 1, idx));
  const target = paras[idx];
  if (target && target.offset_ms != null) {
    seekMs(target.offset_ms);
    if (player.paused) player.play();
  }
}

// Highlight sync loop.
player.addEventListener("timeupdate", () => { /* keep for coarse updates */ });

function highlightTick() {
  requestAnimationFrame(highlightTick);
  if (!state.synth || player.paused || !state.wordSpans.length) return;
  const nowMs = player.currentTime * 1000;
  const timings = state.synth.timings;

  // Binary search: last timing with ms <= nowMs.
  let lo = 0, hi = timings.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (timings[mid].ms <= nowMs) { found = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  if (found < 0) return;
  const t = timings[found];

  // Map timing char range to a word span (spans are sorted by char start).
  const spanIdx = state.wordSpans.findIndex(
    (sp) => Number(sp.dataset.e) > t.start && Number(sp.dataset.s) < t.end
  );
  if (spanIdx >= 0 && spanIdx !== state.activeWordIdx) {
    if (state.activeWordIdx >= 0 && state.wordSpans[state.activeWordIdx]) {
      state.wordSpans[state.activeWordIdx].classList.remove("speaking");
    }
    const span = state.wordSpans[spanIdx];
    span.classList.add("speaking");
    state.activeWordIdx = spanIdx;
    const rect = span.getBoundingClientRect();
    if (rect.top < 90 || rect.bottom > window.innerHeight - 120) {
      span.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    const pIdx = Number(span.closest("p").dataset.para);
    if (pIdx !== state.activeParaIdx) {
      if (state.activeParaIdx >= 0 && state.paraEls[state.activeParaIdx]) {
        state.paraEls[state.activeParaIdx].classList.remove("active-para");
      }
      state.paraEls[pIdx].classList.add("active-para");
      state.activeParaIdx = pIdx;
    }
  }

  // Progress bar.
  const dur = player.duration || (state.synth.duration_ms / 1000);
  $("#progress-fill").style.width = dur ? `${(player.currentTime / dur) * 100}%` : "0";
  $("#time-label").textContent = `${fmtTime(player.currentTime)} / ${fmtTime(dur)}`;
}
requestAnimationFrame(highlightTick);

player.addEventListener("play", updatePlaybar);
player.addEventListener("pause", updatePlaybar);
player.addEventListener("ended", () => { clearHighlights(); updatePlaybar(); });

function updatePlaybar() {
  $("#btn-play").textContent = player.paused ? "▶" : "⏸";
  if (state.synth) {
    const dur = player.duration || state.synth.duration_ms / 1000;
    $("#time-label").textContent = `${fmtTime(player.currentTime)} / ${fmtTime(dur)}`;
  } else {
    $("#time-label").textContent = "0:00 / 0:00";
    $("#progress-fill").style.width = "0";
  }
}

$("#btn-play").addEventListener("click", togglePlay);
$("#btn-stop").addEventListener("click", stopPlayback);
$("#btn-prev").addEventListener("click", () => jumpParagraph(-1));
$("#btn-next").addEventListener("click", () => jumpParagraph(1));

$("#progress-wrap").addEventListener("click", (ev) => {
  if (!state.synth) return;
  const bar = $("#progress-bar").getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (ev.clientX - bar.left) / bar.width));
  const dur = player.duration || state.synth.duration_ms / 1000;
  player.currentTime = frac * dur;
});

// ---------- export ----------

async function exportAudio(format) {
  if (!state.essay || state.generating) return;
  setStatus(`Exporting ${format.toUpperCase()}…`);
  try {
    const result = await api(`/api/essays/${state.essay.id}/synthesize`, { method: "POST", json: { format } });
    const a = document.createElement("a");
    a.href = result.audio_url;
    a.download = result.audio_url.split("/").pop();
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast(`${format.toUpperCase()} saved (also kept in the app's data/audio folder).`);
  } catch (err) {
    toast(`Export failed: ${err.message}`, true, 6000);
  } finally {
    setStatus("");
  }
}

$("#btn-export-wav").addEventListener("click", () => exportAudio("wav"));
$("#btn-export-mp3").addEventListener("click", () => exportAudio("mp3"));

async function batchExport() {
  const classId = $("#filter-class").value;
  if (!classId) { toast("Pick a class in the filter first, then batch export.", true); return; }
  const cls = state.classes.find((c) => String(c.id) === classId);
  if (!confirm(`Generate MP3 files for every essay in ${cls ? cls.name : "this class"}? This can take a while.`)) return;
  setStatus("Batch exporting…");
  toast("Batch export started — generating audio for the whole class…", false, 8000);
  try {
    const res = await api("/api/export/batch", { method: "POST", json: { class_id: Number(classId), format: "mp3" } });
    toast(`Batch done: ${res.ok_count} succeeded, ${res.fail_count} failed. Files are in ${res.output_dir}`, res.fail_count > 0, 9000);
  } catch (err) {
    toast(`Batch export failed: ${err.message}`, true, 6000);
  } finally {
    setStatus("");
  }
}
$("#btn-batch").addEventListener("click", batchExport);

$("#btn-delete-essay").addEventListener("click", async () => {
  if (!state.essay) return;
  if (!confirm(`Delete “${state.essay.title}” (${state.essay.student_name})? This can't be undone.`)) return;
  await api(`/api/essays/${state.essay.id}`, { method: "DELETE" });
  state.essay = null;
  state.synth = null;
  renderEssay();
  refreshEssays();
});

// ---------- add / import modal ----------

const modalAdd = $("#modal-add");

function openAddModal() {
  fillAddSelectors();
  $("#add-title").value = "";
  $("#add-content").value = "";
  $("#add-files").value = "";
  $("#add-error").hidden = true;
  modalAdd.showModal();
}

function fillAddSelectors() {
  const clsSel = $("#add-class");
  clsSel.innerHTML = state.classes.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")
    || '<option value="">(no classes yet — type one below)</option>';
  const preferred = $("#filter-class").value;
  if (preferred) clsSel.value = preferred;
  fillAddStudents();
}

async function fillAddStudents() {
  const classId = $("#add-class").value;
  let students = [];
  if (classId) students = await api(`/api/students?class_id=${classId}`);
  $("#add-student").innerHTML = students.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("")
    || '<option value="">(no students yet — type one below)</option>';
}
$("#add-class").addEventListener("change", fillAddStudents);

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    $("#tab-paste").hidden = tab.dataset.tab !== "paste";
    $("#tab-file").hidden = tab.dataset.tab !== "file";
  });
});

async function resolveClassAndStudent() {
  let classId = $("#add-class").value;
  const newClass = $("#add-class-new").value.trim();
  if (newClass) {
    const cls = await api("/api/classes", { method: "POST", json: { name: newClass } });
    classId = cls.id;
    $("#add-class-new").value = "";
    await refreshClasses();
  }
  if (!classId) throw new Error("Choose a class or type a new class name.");

  let studentId = $("#add-student").value;
  const newStudent = $("#add-student-new").value.trim();
  if (newStudent) {
    const st = await api("/api/students", { method: "POST", json: { class_id: Number(classId), name: newStudent } });
    studentId = st.id;
    $("#add-student-new").value = "";
  }
  if (!studentId) throw new Error("Choose a student or type a new student name.");
  return { classId: Number(classId), studentId: Number(studentId) };
}

$("#form-add").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const errEl = $("#add-error");
  errEl.hidden = true;
  try {
    const { studentId } = await resolveClassAndStudent();
    const files = $("#add-files").files;
    const pasted = $("#add-content").value.trim();
    let lastEssay = null;

    if (files.length) {
      for (const file of files) {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("student_id", studentId);
        if (files.length === 1 && $("#add-title").value.trim()) {
          fd.append("title", $("#add-title").value.trim());
        }
        lastEssay = await api("/api/essays/import", { method: "POST", body: fd });
      }
      toast(`Imported ${files.length} file${files.length > 1 ? "s" : ""}.`);
    } else if (pasted) {
      lastEssay = await api("/api/essays", {
        method: "POST",
        json: { student_id: studentId, title: $("#add-title").value.trim() || "Untitled", content: pasted },
      });
      toast("Essay saved.");
    } else {
      throw new Error("Paste some text or choose a file.");
    }

    modalAdd.close();
    await refreshClasses();
    await refreshStudents();
    await refreshEssays();
    if (lastEssay) openEssay(lastEssay.id);
  } catch (err) {
    errEl.textContent = err.message;
    errEl.hidden = false;
  }
});

$("#add-cancel").addEventListener("click", () => modalAdd.close());
$("#btn-add").addEventListener("click", openAddModal);

// Drag & drop onto the dropzone.
const dropzone = $("#dropzone");
["dragover", "dragenter"].forEach((type) =>
  dropzone.addEventListener(type, (ev) => { ev.preventDefault(); dropzone.classList.add("dragover"); }));
["dragleave", "drop"].forEach((type) =>
  dropzone.addEventListener(type, (ev) => { ev.preventDefault(); dropzone.classList.remove("dragover"); }));
dropzone.addEventListener("drop", (ev) => {
  $("#add-files").files = ev.dataTransfer.files;
});

// ---------- settings modal ----------

const modalSettings = $("#modal-settings");

async function openSettings() {
  state.settings = await api("/api/settings");
  $("#set-engine").value = state.settings.engine;
  $("#set-rate").value = state.settings.rate;
  $("#set-pitch").value = state.settings.pitch;
  $("#set-volume").value = state.settings.volume;
  $("#set-azure-region").value = state.settings.azure_region || "";
  $("#set-azure-key").value = "";
  syncSliderLabels();
  await loadVoiceOptions();
  toggleEngineUi();
  $("#settings-error").hidden = true;
  modalSettings.showModal();
}

function toggleEngineUi() {
  const azure = $("#set-engine").value === "azure";
  $("#azure-wrap").hidden = !azure;
  $("#local-voice-wrap").hidden = azure;
  $("#azure-key-status").textContent = state.settings && state.settings.azure_key_set
    ? "An API key is saved on this machine. Leave the field blank to keep it."
    : "No API key saved yet.";
}

async function loadVoiceOptions() {
  try {
    const local = await api("/api/voices?engine=local");
    $("#set-voice").innerHTML = local.voices
      .map((v) => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join("")
      || "<option value=''>(no voices found)</option>";
    if (state.settings.voice_id) $("#set-voice").value = state.settings.voice_id;
  } catch (err) {
    $("#set-voice").innerHTML = `<option value="">(couldn't load: ${esc(err.message)})</option>`;
  }
  try {
    const azure = await api("/api/voices?engine=azure");
    $("#set-azure-voice").innerHTML = (azure.voices || [])
      .map((v) => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join("");
    if (state.settings.azure_voice) $("#set-azure-voice").value = state.settings.azure_voice;
  } catch (err) {
    $("#set-azure-voice").innerHTML = "<option value=''>(unavailable)</option>";
  }
}

function syncSliderLabels() {
  $("#val-rate").textContent = $("#set-rate").value;
  $("#val-pitch").textContent = $("#set-pitch").value;
  $("#val-volume").textContent = $("#set-volume").value;
}
["set-rate", "set-pitch", "set-volume"].forEach((id) =>
  $(`#${id}`).addEventListener("input", syncSliderLabels));
$("#set-engine").addEventListener("change", toggleEngineUi);

function collectSettingsPayload() {
  const payload = {
    engine: $("#set-engine").value,
    voice_id: $("#set-voice").value,
    azure_voice: $("#set-azure-voice").value,
    rate: Number($("#set-rate").value),
    pitch: Number($("#set-pitch").value),
    volume: Number($("#set-volume").value),
    azure_region: $("#set-azure-region").value.trim(),
  };
  const key = $("#set-azure-key").value.trim();
  if (key) payload.azure_key = key;
  return payload;
}

async function saveSettings() {
  try {
    state.settings = await api("/api/settings", { method: "PUT", json: collectSettingsPayload() });
    $("#set-azure-key").value = "";
    state.synth = null; // regenerate with new voice next play
    toast("Settings saved.");
    return true;
  } catch (err) {
    const el = $("#settings-error");
    el.textContent = err.message;
    el.hidden = false;
    return false;
  }
}

$("#settings-save").addEventListener("click", async () => {
  if (await saveSettings()) modalSettings.close();
});
$("#settings-close").addEventListener("click", () => modalSettings.close());
$("#btn-settings").addEventListener("click", openSettings);

$("#btn-preview-voice").addEventListener("click", async () => {
  if (!(await saveSettings())) return;
  try {
    setPreviewBusy(true);
    const res = await api("/api/preview", { method: "POST" });
    const preview = new Audio(res.audio_url);
    preview.play();
  } catch (err) {
    toast(`Preview failed: ${err.message}`, true, 6000);
  } finally {
    setPreviewBusy(false);
  }
});

function setPreviewBusy(busy) {
  const btn = $("#btn-preview-voice");
  btn.disabled = busy;
  btn.textContent = busy ? "Generating…" : "🔊 Preview voice";
}

// ---------- pronunciation rules modal ----------

const modalRules = $("#modal-rules");

async function openRules() {
  await renderRules();
  modalRules.showModal();
  $("#rule-find").focus();
}

async function renderRules() {
  const rules = await api("/api/rules");
  $("#rule-list").innerHTML = rules.map((r) => `
    <li>
      <span class="rule-text"><strong>${esc(r.find_text)}</strong> → ${esc(r.replace_text)}</span>
      <button class="rule-del" data-id="${r.id}" title="Delete rule">✕</button>
    </li>`).join("") || '<li class="muted">No fixes yet.</li>';
}

$("#rule-add").addEventListener("click", async () => {
  const find = $("#rule-find").value.trim();
  const replace = $("#rule-replace").value.trim();
  if (!find || !replace) { toast("Fill in both boxes first.", true); return; }
  await api("/api/rules", { method: "POST", json: { find_text: find, replace_text: replace } });
  $("#rule-find").value = "";
  $("#rule-replace").value = "";
  state.synth = null; // audio must regenerate with new rules
  await renderRules();
  $("#rule-find").focus();
});

$("#rule-list").addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".rule-del");
  if (!btn) return;
  await api(`/api/rules/${btn.dataset.id}`, { method: "DELETE" });
  state.synth = null;
  renderRules();
});

$("#rules-close").addEventListener("click", () => modalRules.close());
$("#btn-rules").addEventListener("click", openRules);

// ---------- help modal ----------

$("#btn-help").addEventListener("click", () => $("#modal-help").showModal());
$("#help-close").addEventListener("click", () => $("#modal-help").close());

// ---------- keyboard shortcuts ----------

function anyDialogOpen() {
  return [...document.querySelectorAll("dialog")].some((d) => d.open);
}

document.addEventListener("keydown", (ev) => {
  const tag = document.activeElement ? document.activeElement.tagName : "";
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(tag);

  if (ev.key === "Escape" && !anyDialogOpen() && !typing) { stopPlayback(); return; }
  if (typing || anyDialogOpen()) return;

  const essayIdx = state.essay ? state.essays.findIndex((e) => e.id === state.essay.id) : -1;

  switch (ev.key) {
    case " ":
      ev.preventDefault();
      togglePlay();
      break;
    case "s": case "S":
      stopPlayback();
      break;
    case "j": case "J": case "ArrowDown":
      ev.preventDefault();
      jumpParagraph(1);
      break;
    case "k": case "K": case "ArrowUp":
      ev.preventDefault();
      jumpParagraph(-1);
      break;
    case "ArrowRight":
      if (state.synth) player.currentTime = Math.min(player.duration || 1e9, player.currentTime + 5);
      break;
    case "ArrowLeft":
      if (state.synth) player.currentTime = Math.max(0, player.currentTime - 5);
      break;
    case "PageDown":
      ev.preventDefault();
      if (essayIdx >= 0 && essayIdx < state.essays.length - 1) openEssay(state.essays[essayIdx + 1].id);
      else if (essayIdx < 0 && state.essays.length) openEssay(state.essays[0].id);
      break;
    case "PageUp":
      ev.preventDefault();
      if (essayIdx > 0) openEssay(state.essays[essayIdx - 1].id);
      break;
    case "n": case "N":
      ev.preventDefault();
      openAddModal();
      break;
    case "e": case "E":
      exportAudio("mp3");
      break;
    case "b": case "B":
      batchExport();
      break;
    case "/":
      ev.preventDefault();
      $("#search").focus();
      break;
    case ",":
      openSettings();
      break;
    case "?":
      $("#modal-help").showModal();
      break;
  }
});

// ---------- filters ----------

let searchTimer = null;
$("#search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refreshEssays, 250);
});
$("#search").addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" || ev.key === "Enter") $("#search").blur();
});
$("#filter-class").addEventListener("change", async () => { await refreshStudents(); refreshEssays(); });
$("#filter-student").addEventListener("change", refreshEssays);

// ---------- init ----------

async function init() {
  state.settings = await api("/api/settings");
  await refreshClasses();
  await refreshStudents();
  await refreshEssays();
}
init().catch((err) => toast(`Failed to load: ${err.message}`, true, 8000));

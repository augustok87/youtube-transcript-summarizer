// --- DOM refs (kept) ---
const form = document.getElementById("summarize-form");
const urlInput = document.getElementById("url-input");
const submitBtn = document.getElementById("submit-btn");
const granularitySelect = document.getElementById("granularity");
const customWrapper = document.getElementById("custom-instruction-wrapper");
const customInput = document.getElementById("custom-instruction");
const outputLangInput = document.getElementById("output-language");

// History elements
const historySection = document.getElementById("history-section");
const historyToggle = document.getElementById("history-toggle");
const historyCount = document.getElementById("history-count");
const historyList = document.getElementById("history-list");
const historyClear = document.getElementById("history-clear");

// Card system
const resultsContainer = document.getElementById("results-container");
const cardTemplate = document.getElementById("result-card-template");

// Queue state
const queue = { cards: [], activeCard: null };
const MAX_QUEUED = 5;
let cardIdCounter = 0;

// Configure marked.js
marked.setOptions({
  breaks: false,
  gfm: true,
  headerIds: false,
  mangle: false,
});

// --- History ---

const HISTORY_KEY = "yt-summarizer-history";
const MAX_HISTORY = 25;

const GRANULARITY_LABELS = {
  one_liner: "One-liner",
  tldr: "TL;DR",
  short: "Short",
  detailed: "Detailed",
  chapters: "Chapters",
  custom: "Custom",
};

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY)) || [];
  } catch {
    return [];
  }
}

function saveHistory(history) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
}

function addToHistory(entry) {
  const history = loadHistory();
  const filtered = history.filter(
    (h) => !(h.video_id === entry.video_id && h.granularity === entry.granularity)
  );
  filtered.unshift(entry);
  saveHistory(filtered.slice(0, MAX_HISTORY));
  renderHistory();
}

function thumbnailUrl(videoId, platform) {
  if (platform === "youtube" || !platform) {
    return `https://i.ytimg.com/vi/${videoId}/default.jpg`;
  }
  return `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="48" height="36" fill="%23333"><rect width="48" height="36" rx="4"/><text x="24" y="22" text-anchor="middle" fill="%23666" font-size="10">' + platform[0].toUpperCase() + '</text></svg>')}`;
}

function timeAgo(ts) {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function renderHistory() {
  const history = loadHistory();
  historyCount.textContent = history.length;

  if (history.length === 0) {
    historySection.classList.add("hidden");
    return;
  }

  historySection.classList.remove("hidden");
  historyList.innerHTML = history
    .map(
      (h, i) => `
    <div class="history-item" data-index="${i}">
      <img class="history-thumb" src="${thumbnailUrl(h.video_id, h.platform)}" alt="" loading="lazy">
      <div class="history-info">
        <div class="history-title" title="${h.title}">${h.title}</div>
        <div class="history-meta">
          <span>${formatDuration(h.duration_seconds)}</span>
          <span>${timeAgo(h.timestamp)}</span>
        </div>
      </div>
      <span class="history-granularity">${GRANULARITY_LABELS[h.granularity] || h.granularity}</span>
    </div>`
    )
    .join("");
}

historyToggle.addEventListener("click", () => {
  const isOpen = historyToggle.classList.toggle("open");
  historyList.classList.toggle("hidden", !isOpen);
  historyClear.classList.toggle("hidden", !isOpen);
});

historyList.addEventListener("click", (e) => {
  const item = e.target.closest(".history-item");
  if (!item) return;
  const history = loadHistory();
  const entry = history[item.dataset.index];
  if (!entry) return;

  // Populate form for visibility
  urlInput.value = entry.url || `https://www.youtube.com/watch?v=${entry.video_id}`;
  granularitySelect.value = entry.granularity;
  granularitySelect.dispatchEvent(new Event("change"));
  if (entry.custom_instruction) customInput.value = entry.custom_instruction;
  if (entry.output_language) outputLangInput.value = entry.output_language;

  if (entry.summary) {
    createCachedCard(entry);
  } else {
    const url = entry.url || `https://www.youtube.com/watch?v=${entry.video_id}`;
    enqueueApiCard(url, entry.granularity, entry.custom_instruction || null, entry.output_language || null);
  }
});

historyClear.addEventListener("click", () => {
  localStorage.removeItem(HISTORY_KEY);
  renderHistory();
  historyToggle.classList.remove("open");
  historyList.classList.add("hidden");
  historyClear.classList.add("hidden");
});

renderHistory();

// --- Show/hide custom instruction field ---

granularitySelect.addEventListener("change", () => {
  if (granularitySelect.value === "custom") {
    customWrapper.classList.remove("hidden");
  } else {
    customWrapper.classList.add("hidden");
  }
});

// --- Helpers ---

function parseVideoUrl(url) {
  try {
    const u = new URL(url);
    if (u.hostname === "youtu.be") return { platform: "youtube", id: u.pathname.slice(1) };
    if (u.hostname.includes("youtube.com")) {
      if (u.pathname === "/watch") {
        const v = u.searchParams.get("v");
        return v ? { platform: "youtube", id: v } : null;
      }
      for (const prefix of ["/embed/", "/shorts/", "/v/"]) {
        if (u.pathname.startsWith(prefix)) {
          return { platform: "youtube", id: u.pathname.slice(prefix.length).split("/")[0] };
        }
      }
    }
    if (u.hostname.includes("rumble.com")) {
      const path = u.pathname.replace(/^\/|\/$/g, "");
      if (path) return { platform: "rumble", id: path.replace(".html", "").replace(/\//g, "_") };
    }
  } catch { /* invalid URL */ }
  return null;
}

function extractVideoId(url) {
  const parsed = parseVideoUrl(url);
  return parsed ? parsed.id : null;
}

function findCachedSummary(videoId, granularity) {
  const history = loadHistory();
  const match = history.find(
    (h) => h.video_id === videoId && h.granularity === granularity && h.summary
  );
  if (!match) return null;
  if (Date.now() - match.timestamp > 30 * 24 * 60 * 60 * 1000) return null;
  return match;
}

function formatDuration(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

// --- Card System ---

function createCard(options) {
  const id = ++cardIdCounter;
  const clone = cardTemplate.content.cloneNode(true);
  const el = clone.querySelector(".result-card");

  el.dataset.cardId = id;
  el.dataset.cardState = options.state;

  const titleEl = el.querySelector(".card-title");
  const granularityEl = el.querySelector(".card-granularity");
  const sourceBadge = el.querySelector(".card-source-badge");

  titleEl.textContent = options.title || "Loading...";
  granularityEl.textContent = GRANULARITY_LABELS[options.granularity] || options.granularity;

  if (options.state === "cached") {
    sourceBadge.textContent = "cached";
    sourceBadge.className = "card-source-badge cached";
  } else if (options.state === "queued") {
    sourceBadge.textContent = "queued";
    sourceBadge.className = "card-source-badge queued";
  } else {
    sourceBadge.textContent = "";
  }

  const card = {
    id,
    state: options.state,
    url: options.url,
    granularity: options.granularity,
    customInstruction: options.customInstruction || null,
    outputLanguage: options.outputLanguage || null,
    el,
    abortController: null,
    rawSummary: "",
  };

  // Wire collapse button
  const collapseBtn = el.querySelector(".card-collapse-btn");
  collapseBtn.addEventListener("click", () => collapseCard(card));

  // Wire close button
  const closeBtn = el.querySelector(".card-close-btn");
  closeBtn.addEventListener("click", () => dismissCard(card));

  resultsContainer.prepend(el);
  return card;
}

function createCachedCard(entry) {
  const card = createCard({
    state: "cached",
    title: entry.title,
    granularity: entry.granularity,
    url: entry.url,
  });

  const metaDuration = card.el.querySelector(".card-meta-duration");
  const metaSource = card.el.querySelector(".card-meta-source");
  const metaTokens = card.el.querySelector(".card-meta-tokens");
  const metadataEl = card.el.querySelector(".card-metadata");
  const summaryOutput = card.el.querySelector(".card-summary-output");
  const statusEl = card.el.querySelector(".card-status");

  metaDuration.textContent = formatDuration(entry.duration_seconds);
  metaSource.textContent = `Source: ${entry.source || "cached"}`;
  metaTokens.textContent = "";
  metadataEl.classList.remove("hidden");

  summaryOutput.classList.remove("hidden");
  summaryOutput.classList.add(`granularity-${entry.granularity}`);
  summaryOutput.innerHTML = marked.parse(entry.summary);

  statusEl.classList.add("hidden");

  return card;
}

function enqueueApiCard(url, granularity, customInstruction, outputLanguage) {
  // Enforce queue cap
  const queuedCount = queue.cards.filter((c) => c.state === "queued").length;
  if (queuedCount >= MAX_QUEUED) return;

  const card = createCard({
    state: "queued",
    title: "Loading...",
    granularity,
    url,
    customInstruction,
    outputLanguage,
  });

  // Show queued status
  const statusEl = card.el.querySelector(".card-status");
  const statusText = card.el.querySelector(".card-status-text");
  statusEl.classList.remove("hidden");
  statusText.textContent = "Waiting in queue...";

  queue.cards.push(card);
  updateSubmitButton();
  processQueue();
}

function processQueue() {
  if (queue.activeCard) return;

  const nextIndex = queue.cards.findIndex((c) => c.state === "queued");
  if (nextIndex === -1) return;

  const card = queue.cards[nextIndex];

  // Re-check cache — an earlier card may have cached the result
  const videoId = extractVideoId(card.url);
  if (videoId && card.granularity !== "custom") {
    const cached = findCachedSummary(videoId, card.granularity);
    if (cached) {
      // Convert to cached card in-place
      card.state = "cached";
      card.el.dataset.cardState = "cached";

      const sourceBadge = card.el.querySelector(".card-source-badge");
      sourceBadge.textContent = "cached";
      sourceBadge.className = "card-source-badge cached";

      const titleEl = card.el.querySelector(".card-title");
      titleEl.textContent = cached.title;

      const metaDuration = card.el.querySelector(".card-meta-duration");
      const metaSource = card.el.querySelector(".card-meta-source");
      const metaTokens = card.el.querySelector(".card-meta-tokens");
      const metadataEl = card.el.querySelector(".card-metadata");
      const summaryOutput = card.el.querySelector(".card-summary-output");
      const statusEl = card.el.querySelector(".card-status");

      metaDuration.textContent = formatDuration(cached.duration_seconds);
      metaSource.textContent = `Source: ${cached.source || "cached"}`;
      metaTokens.textContent = "";
      metadataEl.classList.remove("hidden");

      summaryOutput.classList.remove("hidden");
      summaryOutput.classList.add(`granularity-${card.granularity}`);
      summaryOutput.innerHTML = marked.parse(cached.summary);

      statusEl.classList.add("hidden");

      // Remove from queue array and continue
      queue.cards.splice(nextIndex, 1);
      updateSubmitButton();
      processQueue();
      return;
    }
  }

  queue.activeCard = card;
  card.state = "active";
  card.el.dataset.cardState = "active";

  const sourceBadge = card.el.querySelector(".card-source-badge");
  sourceBadge.textContent = "streaming";
  sourceBadge.className = "card-source-badge streaming";

  queue.cards.splice(nextIndex, 1);
  updateSubmitButton();

  runApiCard(card);
}

async function runApiCard(card) {
  const statusEl = card.el.querySelector(".card-status");
  const statusText = card.el.querySelector(".card-status-text");
  const metaDuration = card.el.querySelector(".card-meta-duration");
  const metaSource = card.el.querySelector(".card-meta-source");
  const metaTokens = card.el.querySelector(".card-meta-tokens");
  const metadataEl = card.el.querySelector(".card-metadata");
  const summaryOutput = card.el.querySelector(".card-summary-output");
  const titleEl = card.el.querySelector(".card-title");

  statusEl.classList.remove("hidden");
  statusText.textContent = "Extracting transcript...";

  card.abortController = new AbortController();
  const signal = card.abortController.signal;

  try {
    // Fetch transcript metadata
    const transcriptRes = await fetch("/api/transcript", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: card.url }),
      signal,
    });

    if (!transcriptRes.ok) {
      const err = await transcriptRes.json();
      throw new Error(err.message || "Failed to extract transcript.");
    }

    const transcript = await transcriptRes.json();

    // Update card header with real title
    titleEl.textContent = transcript.title;

    // Show metadata
    metaDuration.textContent = formatDuration(transcript.duration_seconds);
    metaSource.textContent = `Source: ${transcript.source}`;
    metadataEl.classList.remove("hidden");

    statusText.textContent = "Generating summary...";

    // Build summarize request
    const body = { url: card.url, granularity: card.granularity };
    if (card.customInstruction) body.custom_instruction = card.customInstruction;
    if (card.outputLanguage) body.output_language = card.outputLanguage;

    const response = await fetch("/api/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });

    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.message || "Failed to generate summary.");
    }

    // Stream SSE
    summaryOutput.classList.remove("hidden");
    summaryOutput.classList.add(`granularity-${card.granularity}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let currentEvent = null;
    let dataBuffer = [];

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          dataBuffer.push(line.slice(6));
        } else if (line === "" && dataBuffer.length > 0) {
          const data = dataBuffer.join("\n");
          dataBuffer = [];

          if (currentEvent === "progress") {
            statusText.textContent = data;
          } else if (currentEvent === "metadata") {
            try {
              const meta = JSON.parse(data);
              metaTokens.textContent = `Tokens: ${meta.input_tokens} in / ${meta.output_tokens} out`;
            } catch { /* ignore */ }
          } else if (currentEvent === "error") {
            throw new Error(data);
          } else if (currentEvent === "done") {
            // Stream complete
          } else {
            // Summary text delta
            card.rawSummary += data;
            summaryOutput.innerHTML = marked.parse(card.rawSummary);
          }

          currentEvent = null;
        }
      }
    }

    // Success — mark completed
    card.state = "completed";
    card.el.dataset.cardState = "completed";
    statusEl.classList.add("hidden");

    const sourceBadge = card.el.querySelector(".card-source-badge");
    sourceBadge.textContent = "";

    // Add to history
    addToHistory({
      video_id: transcript.video_id,
      title: transcript.title,
      duration_seconds: transcript.duration_seconds,
      source: transcript.source,
      platform: transcript.platform || "youtube",
      url: card.url,
      granularity: card.granularity,
      custom_instruction: card.customInstruction,
      output_language: card.outputLanguage,
      summary: card.rawSummary.length <= 10000 ? card.rawSummary : null,
      timestamp: Date.now(),
    });
  } catch (err) {
    if (err.name === "AbortError") {
      // User dismissed — silently ignore
      return;
    }

    card.state = "error";
    card.el.dataset.cardState = "error";

    const sourceBadge = card.el.querySelector(".card-source-badge");
    sourceBadge.textContent = "";

    statusEl.classList.remove("hidden");
    statusText.textContent = "";

    summaryOutput.classList.remove("hidden");
    summaryOutput.innerHTML = `<p style="color: #e55;">${err.message}</p>`;
  } finally {
    card.abortController = null;
    if (queue.activeCard === card) {
      queue.activeCard = null;
    }
    updateSubmitButton();
    processQueue();
  }
}

function dismissCard(card) {
  // Abort if active
  if (card.abortController) {
    card.abortController.abort();
  }

  // Remove from queue if queued
  const idx = queue.cards.indexOf(card);
  if (idx !== -1) queue.cards.splice(idx, 1);

  // Remove from DOM
  card.el.remove();

  // If this was the active card, clear and advance
  if (queue.activeCard === card) {
    queue.activeCard = null;
    processQueue();
  }

  updateSubmitButton();
}

function collapseCard(card) {
  card.el.classList.toggle("collapsed");
}

function updateSubmitButton() {
  const queuedCount = queue.cards.filter((c) => c.state === "queued").length;
  if (queuedCount > 0) {
    submitBtn.textContent = `Summarize (${queuedCount} queued)`;
  } else {
    submitBtn.textContent = "Summarize";
  }
}

// --- Form submission ---

form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const url = urlInput.value.trim();
  if (!url) return;

  const granularity = granularitySelect.value;
  const customInstruction =
    granularity === "custom" ? customInput.value.trim() : null;
  const outputLanguage = outputLangInput.value.trim() || null;

  // Check frontend cache first
  const videoId = extractVideoId(url);
  if (videoId && granularity !== "custom") {
    const cached = findCachedSummary(videoId, granularity);
    if (cached) {
      createCachedCard(cached);
      return;
    }
  }

  enqueueApiCard(url, granularity, customInstruction, outputLanguage);
});

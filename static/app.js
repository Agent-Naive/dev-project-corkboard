/* Project Corkboard — vanilla UI. Renders what the local API returns. */
(function () {
  "use strict";

  var STATUSES = ["active", "paused", "idea", "shipped", "archive"];
  var PINS = ["pin-red", "pin-brass", "pin-blue", "pin-pearl"];
  var DEFAULT_COLOR = "#f5e6a3";
  var DEFAULT_DEPTH = 4;
  var POSTIT_COLORS = [
    { hex: "#f5e6a3", name: "Canary Yellow" },
    { hex: "#f4b6c8", name: "Power Pink" },
    { hex: "#f5a574", name: "Vital Orange" },
    { hex: "#d4e07a", name: "Acid Lime" },
    { hex: "#b8e0c8", name: "Lucky Green" },
    { hex: "#8ec6e6", name: "Blue Paradise" },
    { hex: "#c5b4e3", name: "Iris" },
    { hex: "#d9c8b4", name: "Kraft" },
  ];

  var state = {
    config: null,
    filter: "all",
    expandedId: null,
    scan: null,
    scanning: false,
    loading: true,
    loadError: null,
    pending: {},
    justAddedId: null,
    flash: null,
    depthReady: false,
    colors: POSTIT_COLORS,
  };

  var els = {
    filters: document.getElementById("filters"),
    form: document.getElementById("scan-form"),
    path: document.getElementById("scan-path"),
    depth: document.getElementById("scan-depth"),
    scanBtn: document.getElementById("scan-btn"),
    chips: document.getElementById("chips"),
    review: document.getElementById("review"),
    board: document.getElementById("board"),
  };

  function $$(sel, root) {
    return Array.from((root || document).querySelectorAll(sel));
  }

  function escapeHtml(value) {
    if (value == null) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function hashStr(s) {
    var h = 2166136261;
    s = String(s || "");
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  function postitColors() {
    return Array.isArray(state.colors) && state.colors.length ? state.colors : POSTIT_COLORS;
  }

  function postitHexes() {
    return postitColors().map(function (c) {
      return String(c.hex || "").toLowerCase();
    });
  }

  function safeColor(c) {
    if (typeof c === "string") {
      var hex = c.trim().toLowerCase();
      if (postitHexes().indexOf(hex) !== -1) return hex;
      if (/^#[0-9a-f]{3,8}$/.test(hex)) return hex;
    }
    return DEFAULT_COLOR;
  }

  function inkFor(hex) {
    var h = hex.replace("#", "");
    if (h.length === 3) {
      h = h.charAt(0) + h.charAt(0) + h.charAt(1) + h.charAt(1) + h.charAt(2) + h.charAt(2);
    }
    var n = parseInt(h.slice(0, 6), 16);
    if (Number.isNaN(n)) return "#1c140e";
    var r = (n >> 16) & 255;
    var g = (n >> 8) & 255;
    var b = n & 255;
    var y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
    return y < 0.45 ? "#f4ead8" : "#1c140e";
  }

  function tiltFor(id) {
    var h = hashStr(id);
    return ((h % 51) - 25) / 10;
  }

  function pinFor(id) {
    return PINS[hashStr(id) % PINS.length];
  }

  function relativeTime(iso) {
    if (!iso) return "";
    var then = Date.parse(iso);
    if (Number.isNaN(then)) return String(iso);
    var diff = Date.now() - then;
    var abs = Math.abs(diff);
    function fmt(n, unit) {
      return (diff >= 0 ? n + unit + " ago" : "in " + n + unit);
    }
    var sec = Math.round(abs / 1000);
    if (sec < 45) return "just now";
    var min = Math.round(sec / 60);
    if (min < 60) return fmt(min, "m");
    var hr = Math.round(min / 60);
    if (hr < 24) return fmt(hr, "h");
    var day = Math.round(hr / 24);
    if (day < 11) return fmt(day, "d");
    if (day < 45) return fmt(Math.round(day / 7), "w");
    var mo = Math.round(day / 30.44);
    if (mo < 18) return fmt(mo, "mo");
    return fmt(Math.round(day / 365.25), "y");
  }

  function projects() {
    var cfg = state.config;
    if (!cfg || !Array.isArray(cfg.projects)) return [];
    return cfg.projects;
  }

  function scanRoots() {
    var cfg = state.config;
    if (!cfg || !cfg.scan || !Array.isArray(cfg.scan.roots)) return [];
    return cfg.scan.roots;
  }

  function formatError(err) {
    if (err == null) return "";
    if (typeof err === "string") return err;
    if (typeof err === "object") {
      if (err.error && err.path) return err.path + ": " + err.error;
      if (err.message && err.path) return err.path + ": " + err.message;
      if (err.error) return String(err.error);
      if (err.message) return String(err.message);
      try {
        return JSON.stringify(err);
      } catch (e) {
        return String(err);
      }
    }
    return String(err);
  }

  async function fetchJSON(url, options) {
    var res = await fetch(url, options);
    var data;
    try {
      data = await res.json();
    } catch (e) {
      throw new Error("The server did not return JSON (" + url + ").");
    }
    return data;
  }

  function postJSON(url, body) {
    return fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async function unpinFromBoard(id) {
    if (!id) return;
    var proj = projects().filter(function (p) {
      return p && String(p.id) === String(id);
    })[0];
    var path = proj && proj.path;
    try {
      var data = await fetchJSON("/api/projects/" + encodeURIComponent(id), {
        method: "DELETE",
      });
      if (!data || data.ok === false) {
        state.loadError = formatError(data && data.error) || "Could not unpin.";
        return;
      }
      if (data.config) applyConfig(data.config);
      else await loadState();
      if (String(state.expandedId) === String(id)) state.expandedId = null;
      if (path) markCandidate(path, { tracked: false });
      render();
    } catch (err) {
      state.loadError = err.message || "Could not unpin.";
    }
  }

  function patchJSON(url, body) {
    return fetchJSON(url, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async function patchProject(id, body) {
    if (!id) return null;
    var data = await patchJSON(
      "/api/projects/" + encodeURIComponent(id),
      body
    );
    if (!data || data.ok === false) {
      state.loadError = formatError(data && data.error) || "Could not update the pin.";
      return null;
    }
    if (data.config) applyConfig(data.config);
    else await loadState();
    render();
    return data;
  }

  async function setStatus(id, status) {
    if (!id || STATUSES.indexOf(status) === -1) return;
    try {
      await patchProject(id, { status: status });
    } catch (err) {
      state.loadError = err.message || "Could not update status.";
    }
  }

  async function toggleHot(id) {
    if (!id) return;
    var proj = projects().filter(function (p) {
      return p && String(p.id) === String(id);
    })[0];
    try {
      await patchProject(id, { hot: !isHot(proj) });
    } catch (err) {
      state.loadError = err.message || "Could not update Hot!.";
    }
  }

  async function setColor(id, hex) {
    if (!id || postitHexes().indexOf(String(hex || "").toLowerCase()) === -1) return;
    try {
      await patchProject(id, { color: hex });
    } catch (err) {
      state.loadError = err.message || "Could not update color.";
    }
  }

  function applyConfig(config, colors) {
    if (!config || typeof config !== "object") return;
    state.config = config;
    if (Array.isArray(colors) && colors.length) state.colors = colors;
    if (!state.depthReady && els.depth) {
      var depth = config.scan && config.scan.max_depth;
      if (typeof depth === "number" && isFinite(depth) && depth >= 0) {
        els.depth.value = String(depth);
      }
      state.depthReady = true;
    }
  }

  async function loadState() {
    try {
      var data = await fetchJSON("/api/state");
      if (!data || data.ok === false) {
        state.loadError = (data && data.error) || "Could not load the board.";
        state.config = data && data.config ? data.config : state.config;
      } else {
        state.loadError = null;
        applyConfig(data.config, data.colors);
      }
    } catch (err) {
      state.loadError = err.message || "Could not reach the local server. Is it running?";
    } finally {
      state.loading = false;
    }
    render();
  }

  function isHot(p) {
    return !!(p && p.hot === true);
  }

  function visibleProjects() {
    var list = projects();
    if (state.filter === "all") return list;
    if (state.filter === "hot") {
      return list.filter(isHot);
    }
    return list.filter(function (p) {
      return p && p.status === state.filter;
    });
  }

  function renderFilters() {
    $$(".filter", els.filters).forEach(function (btn) {
      btn.setAttribute("aria-pressed", btn.getAttribute("data-filter") === state.filter ? "true" : "false");
    });
  }

  function renderChips() {
    var roots = scanRoots();
    if (!roots.length) {
      els.chips.innerHTML = "";
      return;
    }
    els.chips.innerHTML = roots.map(function (root) {
      var label = escapeHtml(root);
      return '<button type="button" class="chip" data-root="' + label + '" title="Fill path">' +
        label + "</button>";
    }).join("");
  }

  function gitSubject(obj) {
    if (!obj || !obj.git) return "";
    return obj.git.last_commit_subject || "";
  }

  function renderReview() {
    var el = els.review;
    if (!state.scan && !state.scanning) {
      el.hidden = true;
      el.innerHTML = "";
      return;
    }
    el.hidden = false;

    if (state.scanning) {
      var pathWait = (els.path.value || "").trim() || "that folder";
      el.innerHTML =
        '<div class="review-head"><h2>Scanning…</h2></div>' +
        "<p class=\"review-meta\">Looking through " + escapeHtml(pathWait) + "</p>";
      return;
    }

    var scan = state.scan;
    if (!scan || scan.ok === false) {
      var err = formatError(scan && (scan.error || scan)) || "Scan failed.";
      el.innerHTML =
        '<button type="button" class="review-close" data-close-review aria-label="Dismiss">×</button>' +
        '<div class="review-head"><h2>Scan failed</h2></div>' +
        '<p class="review-error">' + escapeHtml(err) + "</p>";
      return;
    }

    var candidates = Array.isArray(scan.candidates) ? scan.candidates : [];
    var flags = [];
    if (state.flash) flags.push(state.flash);
    if (Array.isArray(scan.roots) && scan.roots.length > 1) {
      flags.push(scan.roots.length + " folders matched the glob.");
    }
    if (scan.truncated) {
      flags.push("Results were truncated — some folders may be missing.");
    }
    if (typeof scan.elapsed_ms === "number") {
      flags.push("Finished in " + (scan.elapsed_ms >= 1000
        ? (scan.elapsed_ms / 1000).toFixed(1) + "s"
        : Math.round(scan.elapsed_ms) + "ms") + ".");
    }

    var errors = Array.isArray(scan.errors) ? scan.errors : [];
    var errorHtml = errors.length
      ? '<ul class="review-errors">' + errors.map(function (e) {
          return "<li>" + escapeHtml(formatError(e)) + "</li>";
        }).join("") + "</ul>"
      : "";

    var listHtml;
    if (!candidates.length) {
      listHtml = '<p class="review-empty">No projects found under this path.</p>';
    } else {
      listHtml = candidates.map(function (c, i) {
        return renderCandidate(c, i);
      }).join("");
    }

    el.innerHTML =
      '<button type="button" class="review-close" data-close-review aria-label="Dismiss review">×</button>' +
      '<div class="review-head">' +
        "<h2>Scan review</h2>" +
        '<p class="review-meta">' + escapeHtml(scan.root || els.path.value || "") +
          " · " + candidates.length + " candidate" + (candidates.length === 1 ? "" : "s") +
        "</p>" +
      "</div>" +
      (flags.length ? '<p class="review-flags">' + escapeHtml(flags.join(" ")) + "</p>" : "") +
      errorHtml +
      listHtml;
  }

  function renderCandidate(c, i) {
    var name = (c && c.name) || "Untitled";
    var path = (c && c.path) || "";
    var conf = (c && c.confidence) || "";
    var subject = gitSubject(c);
    var tracked = !!(c && c.tracked);
    var ignored = !!(c && c.ignored);
    var pending = path && state.pending[path];
    var confClass = conf === "high" || conf === "medium" || conf === "low" ? "badge-" + conf : "";
    var badges = "";
    if (conf) {
      badges += '<span class="badge ' + confClass + '">' + escapeHtml(conf) + "</span> ";
    }
    if (tracked) badges += '<span class="badge badge-on">on board</span> ';
    if (ignored) badges += '<span class="badge badge-ignored">ignored</span> ';

    var actions = "";
    if (!tracked) {
      if (!ignored) {
        actions +=
          '<button type="button" class="btn" data-ignore="' + i + '"' +
          (pending ? " disabled" : "") + ">Ignore</button>";
      }
      actions +=
        '<button type="button" class="btn btn-add" data-add="' + i + '"' +
        (pending ? " disabled" : "") + ">" +
        (pending ? "Working…" : "Pin to board") + "</button>";
    }

    return (
      '<article class="cand">' +
        "<div>" +
          '<p class="cand-name">' + badges + escapeHtml(name) + "</p>" +
          (path ? '<p class="cand-path">' + escapeHtml(path) + "</p>" : "") +
          (subject ? '<p class="cand-git">' + escapeHtml(subject) + "</p>" : "") +
        "</div>" +
        '<div class="cand-actions">' + actions + "</div>" +
      "</article>"
    );
  }

  function emptyStateHtml() {
    return (
      '<div class="board-message">' +
        "<h2>The board is empty</h2>" +
        "<p>Scan a folder: paste a filesystem path above (for example <code>~/dev</code> or a glob like <code>~/dev-project*</code>) and hit Scan. Review what turns up, then Pin to board or Ignore.</p>" +
        "<p>To put the six SAMPLE cards back: <code>python3 server.py --restore-samples</code>, then refresh. Real pins stay.</p>" +
        "<p>Or open <code>data/config.json</code> in an editor, add objects to the <code>projects</code> array (name, summary, status, color), save, and refresh.</p>" +
      "</div>"
    );
  }

  function renderBoard() {
    if (state.loading && !state.config) {
      els.board.innerHTML =
        '<div class="board-message"><h2>Tacking up the board…</h2><p>Reading local config.</p></div>';
      return;
    }
    if (state.loadError && !projects().length) {
      els.board.innerHTML =
        '<div class="board-message error"><h2>Could not load the board</h2><p>' +
        escapeHtml(state.loadError) + "</p></div>";
      return;
    }

    var list = visibleProjects();
    if (!projects().length) {
      els.board.innerHTML = emptyStateHtml();
      return;
    }
    if (!list.length) {
      els.board.innerHTML =
        '<div class="board-message"><h2>Nothing in this filter</h2>' +
        "<p>No pinned projects marked <code>" +
        escapeHtml(state.filter === "hot" ? "Hot!" : state.filter) +
        "</code>. Try All.</p></div>";
      return;
    }

    var focusId = document.activeElement && document.activeElement.classList.contains("note")
      ? document.activeElement.getAttribute("data-id")
      : null;

    els.board.innerHTML = list.map(renderNote).join("");

    if (focusId) {
      var again = $$(".note", els.board).filter(function (n) {
        return n.getAttribute("data-id") === focusId;
      })[0];
      if (again) again.focus();
    }
  }

  function renderNote(p) {
    var id = p.id != null ? String(p.id) : "";
    var color = safeColor(p.color);
    var ink = inkFor(color);
    var open = state.expandedId && id && state.expandedId === id;
    var tilt = tiltFor(id || p.name);
    var pin = pinFor(id || p.name);
    var when = relativeTime(p.last_activity);
    var status = p.status && STATUSES.indexOf(p.status) !== -1 ? p.status : "";
    var sample = p.sample === true;
    var hot = isHot(p);
    var classes = "note" +
      (open ? " is-open" : "") +
      (sample ? " has-sample" : "") +
      (hot ? " is-hot" : "") +
      (state.justAddedId === id ? " just-added" : "");

    var details = "";
    if (open) {
      var pathLabel = p.path
        ? '<button type="button" class="path-copy" data-copy-cd="' + escapeHtml(p.path) +
          '" title="Copy cd command">' + escapeHtml(p.path) + "</button>"
        : (sample ? "No path on disk (sample)" : "No path");
      var gitBits = [];
      if (p.git && typeof p.git === "object") {
        if (p.git.branch) gitBits.push(escapeHtml(p.git.branch));
        if (p.git.last_commit_subject) gitBits.push(escapeHtml(p.git.last_commit_subject));
        if (p.git.dirty) gitBits.push('<span class="dirty-mark">dirty</span>');
      }
      var gitHtml = gitBits.length
        ? gitBits.join(" · ")
        : "No git info";
      var tags = Array.isArray(p.tags) ? p.tags.filter(Boolean) : [];
      var tagHtml = tags.length
        ? '<ul class="tags">' + tags.map(function (t) {
            return "<li>" + escapeHtml(t) + "</li>";
          }).join("") + "</ul>"
        : "";
      var statusPicks = STATUSES.map(function (s) {
        var on = s === (p.status || "");
        return (
          '<button type="button" class="status-pick status-' + s +
            (on ? " is-current" : "") +
            '" data-set-status="' + s + '" aria-pressed="' + (on ? "true" : "false") + '">' +
            s +
          "</button>"
        );
      }).join("");
      var colorPicks = postitColors().map(function (c) {
        var hex = String(c.hex || "").toLowerCase();
        var on = hex === color;
        return (
          '<button type="button" class="color-dot' + (on ? " is-current" : "") +
            '" data-set-color="' + escapeHtml(hex) +
            '" title="' + escapeHtml(c.name || hex) +
            '" aria-label="' + escapeHtml(c.name || hex) +
            '" aria-pressed="' + (on ? "true" : "false") +
            '" style="background:' + escapeHtml(hex) + '"></button>'
        );
      }).join("");
      details =
        '<div class="note-details">' +
          '<p><span class="k">Path · click to copy cd</span> ' + pathLabel + "</p>" +
          '<p><span class="k">Git</span> ' + gitHtml + "</p>" +
          '<div class="status-row"><span class="k">Status</span>' +
            '<div class="status-picks" role="group" aria-label="Project status">' +
              statusPicks +
            "</div>" +
          "</div>" +
          '<div class="status-row"><span class="k">Hot</span>' +
            '<button type="button" class="hot-toggle' + (hot ? " is-on" : "") +
              '" data-toggle-hot aria-pressed="' + (hot ? "true" : "false") + '">Hot!</button>' +
          "</div>" +
          '<div class="status-row"><span class="k">Color</span>' +
            '<div class="color-picks" role="group" aria-label="Post-it color">' +
              colorPicks +
            "</div>" +
          "</div>" +
          tagHtml +
        "</div>";
    }

    return (
      '<article class="' + classes + '" tabindex="0" role="' + (open ? "article" : "button") + '" data-id="' + escapeHtml(id) + '"' +
        ' style="background:' + color + ";color:" + ink + ";--tilt:" + tilt + 'deg"' +
        ' aria-expanded="' + (open ? "true" : "false") + '">' +
        '<span class="pin ' + pin + '" aria-hidden="true"><span class="pin-head"></span></span>' +
        (sample ? '<span class="note-ribbon">SAMPLE</span>' : "") +
        (hot ? '<span class="note-ribbon hot-ribbon">Hot!</span>' : "") +
        (open ? '<button type="button" class="note-close" data-unpin title="Unpin from board" aria-label="Unpin from board">×</button>' : "") +
        '<h2 class="note-title">' + escapeHtml(p.name || "Untitled") + "</h2>" +
        (p.summary ? '<p class="note-summary">' + escapeHtml(p.summary) + "</p>" : "") +
        '<div class="note-meta">' +
          (status ? '<span class="badge status-' + status + '">' + escapeHtml(status) + "</span>" : "") +
          (when ? '<time class="note-when" datetime="' + escapeHtml(p.last_activity || "") + '" title="' +
            escapeHtml(p.last_activity || "") + '">' + escapeHtml(when) + "</time>" : "") +
        "</div>" +
        details +
      "</article>"
    );
  }

  function render() {
    renderFilters();
    renderChips();
    renderReview();
    renderBoard();
  }

  function toggleNote(id) {
    if (!id) return;
    state.expandedId = state.expandedId === id ? null : id;
    renderBoard();
  }

  function collapseNote() {
    if (!state.expandedId) return;
    state.expandedId = null;
    renderBoard();
  }

  function writeClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      try {
        if (document.execCommand("copy")) resolve();
        else reject(new Error("copy failed"));
      } catch (err) {
        reject(err);
      }
      document.body.removeChild(ta);
    });
  }

  function copyCdCommand(el) {
    var raw = (el.getAttribute("data-copy-cd") || "").trim();
    if (!raw) return;
    var cmd = 'cd "' + raw.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
    var original = el.textContent;
    writeClipboard(cmd)
      .then(function () {
        el.classList.add("is-copied");
        el.textContent = "copied cd …";
        setTimeout(function () {
          if (!el.isConnected) return;
          el.textContent = original;
          el.classList.remove("is-copied");
        }, 1500);
      })
      .catch(function () {
        el.classList.add("is-copied");
        el.textContent = "copy failed";
        setTimeout(function () {
          if (!el.isConnected) return;
          el.textContent = original;
          el.classList.remove("is-copied");
        }, 1500);
      });
  }

  function markCandidate(path, flags) {
    if (!state.scan || !Array.isArray(state.scan.candidates) || !path) return;
    state.scan.candidates.forEach(function (c) {
      if (c && c.path === path) {
        if (flags.tracked != null) c.tracked = flags.tracked;
        if (flags.ignored != null) c.ignored = flags.ignored;
      }
    });
  }

  async function addToBoard(candidate) {
    if (!candidate) return;
    var path = candidate.path;
    if (path) state.pending[path] = true;
    renderReview();
    try {
      var data = await postJSON("/api/projects", candidate);
      if (!data || data.ok === false) {
        state.flash = formatError(data && data.error) || "Could not add that project.";
        return;
      }
      state.flash = data.already_tracked ? "Already on the board." : null;
      if (data.config) applyConfig(data.config);
      else await loadState();
      markCandidate(path, { tracked: true, ignored: false });
      if (data.project && data.project.id) {
        state.justAddedId = String(data.project.id);
        setTimeout(function () {
          if (state.justAddedId === String(data.project.id)) {
            state.justAddedId = null;
            renderBoard();
          }
        }, 500);
      }
    } catch (err) {
      state.flash = err.message || "Could not add that project.";
    } finally {
      if (path) delete state.pending[path];
      render();
    }
  }

  async function ignorePath(candidate) {
    if (!candidate || !candidate.path) return;
    var path = candidate.path;
    state.pending[path] = true;
    renderReview();
    try {
      var data = await postJSON("/api/ignore", { path: path });
      if (!data || data.ok === false) {
        state.flash = formatError(data && data.error) || "Could not ignore that path.";
        return;
      }
      state.flash = null;
      if (data.config) applyConfig(data.config);
      else await loadState();
      markCandidate(path, { ignored: true });
    } catch (err) {
      state.flash = err.message || "Could not ignore that path.";
    } finally {
      delete state.pending[path];
      render();
    }
  }

  async function runScan(ev) {
    if (ev) ev.preventDefault();
    var path = (els.path.value || "").trim();
    if (!path) {
      els.path.focus();
      state.scan = { ok: false, error: "Enter a filesystem path to scan." };
      renderReview();
      return;
    }
    var depth = parseInt(els.depth.value, 10);
    if (!isFinite(depth) || depth < 0) depth = DEFAULT_DEPTH;

    state.scanning = true;
    state.scan = null;
    state.flash = null;
    els.scanBtn.disabled = true;
    renderReview();

    try {
      var data = await postJSON("/api/scan", { path: path, max_depth: depth });
      state.scan = data || { ok: false, error: "Empty scan response." };
    } catch (err) {
      state.scan = {
        ok: false,
        error: err.message || "Could not reach the local server. Is it running?",
      };
    } finally {
      state.scanning = false;
      els.scanBtn.disabled = false;
      renderReview();
    }
  }

  els.filters.addEventListener("click", function (e) {
    var btn = e.target.closest(".filter");
    if (!btn) return;
    state.filter = btn.getAttribute("data-filter") || "all";
    renderFilters();
    renderBoard();
  });

  els.chips.addEventListener("click", function (e) {
    var chip = e.target.closest(".chip");
    if (!chip) return;
    els.path.value = chip.getAttribute("data-root") || chip.textContent || "";
    els.path.focus();
  });

  els.form.addEventListener("submit", runScan);

  els.review.addEventListener("click", function (e) {
    if (e.target.closest("[data-close-review]")) {
      state.scan = null;
      state.flash = null;
      renderReview();
      return;
    }
    var addBtn = e.target.closest("[data-add]");
    var ignBtn = e.target.closest("[data-ignore]");
    var cands = state.scan && Array.isArray(state.scan.candidates) ? state.scan.candidates : [];
    if (addBtn) {
      var ai = parseInt(addBtn.getAttribute("data-add"), 10);
      addToBoard(cands[ai]);
      return;
    }
    if (ignBtn) {
      var ii = parseInt(ignBtn.getAttribute("data-ignore"), 10);
      ignorePath(cands[ii]);
    }
  });

  els.board.addEventListener("click", function (e) {
    var unpin = e.target.closest("[data-unpin]");
    var statusBtn = e.target.closest("[data-set-status]");
    var hotBtn = e.target.closest("[data-toggle-hot]");
    var colorBtn = e.target.closest("[data-set-color]");
    var pathCopy = e.target.closest("[data-copy-cd]");
    var note = e.target.closest(".note");
    if (unpin && note) {
      e.preventDefault();
      e.stopPropagation();
      unpinFromBoard(note.getAttribute("data-id"));
      return;
    }
    if (pathCopy) {
      e.preventDefault();
      e.stopPropagation();
      copyCdCommand(pathCopy);
      return;
    }
    if (hotBtn && note) {
      e.preventDefault();
      e.stopPropagation();
      toggleHot(note.getAttribute("data-id"));
      return;
    }
    if (colorBtn && note) {
      e.preventDefault();
      e.stopPropagation();
      setColor(note.getAttribute("data-id"), colorBtn.getAttribute("data-set-color"));
      return;
    }
    if (statusBtn && note) {
      e.preventDefault();
      e.stopPropagation();
      setStatus(note.getAttribute("data-id"), statusBtn.getAttribute("data-set-status"));
      return;
    }
    if (!note) {
      collapseNote();
      return;
    }
    var id = note.getAttribute("data-id");
    // Open notes stay open when you click them (path copy, status, Hot!).
    if (state.expandedId && id === String(state.expandedId)) return;
    toggleNote(id);
  });

  els.board.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    if (e.target.closest("[data-unpin]") || e.target.closest("[data-toggle-hot]") || e.target.closest("[data-copy-cd]") || e.target.closest("[data-set-color]")) return;
    var note = e.target.closest(".note");
    if (!note || e.target !== note) return;
    e.preventDefault();
    var id = note.getAttribute("data-id");
    if (state.expandedId && id === String(state.expandedId)) return;
    toggleNote(id);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
    collapseNote();
  });

  loadState();
})();

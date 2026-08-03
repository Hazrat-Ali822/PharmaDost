/* Sehatyar offline outbox
 * -------------------------------------------------------------------------
 * When the server cannot be reached, queue data-entry form submits in IndexedDB
 * and replay them when it can. Any form marked `data-offline-kind="<kind>"` is
 * handled; every other form on the site is untouched.
 *
 * Server-authoritative numbers (MRN, token, sale #) are assigned by the server
 * at sync time, so an offline save shows a "saved offline" message and the real
 * number arrives once the queue syncs.
 *
 * WHY A PROBE AND NOT `navigator.onLine`
 * --------------------------------------
 * `navigator.onLine` only reports whether the device has a network link. On a
 * clinic router whose uplink is down it stays true, the form submits into the
 * void, and the browser replaces the page the staff were typing into with an
 * error page — the entry is simply gone. So before every offline-capable submit
 * we ask the server directly (`/offline/ping/`, 204, sub-millisecond). Reachable
 * -> the form submits natively, exactly as it always did. Not reachable -> it
 * goes to the outbox. The result is cached for a few seconds so a busy desk is
 * not probing on every keystroke of work.
 *
 * The server side is offline_sync/ (idempotent by the UUID minted here).
 */
(function () {
  "use strict";

  var DB_NAME = "pharmadost-offline";
  var STORE = "outbox";
  var SYNC_URL = "/offline/sync/";
  var PING_URL = "/offline/ping/";
  var QUEUE_URL = "/offline/queue/";
  var SLIP_URL = "/offline/slip/";

  var PING_TIMEOUT_MS = 3500;   // beyond this the connection is not usable anyway
  var PING_CACHE_MS = 4000;     // trust a recent answer instead of re-probing
  var SYNC_BATCH = 200;         // must not exceed offline_sync.views.MAX_BATCH
  var RESYNC_EVERY_MS = 60000;  // the `online` event is not always fired

  // ---- IndexedDB (tiny promise wrapper) ---------------------------------
  function openDB() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: "uuid" });
        }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function store(db, mode) {
    return db.transaction(STORE, mode).objectStore(STORE);
  }

  function putRecord(rec) {
    return openDB().then(function (db) {
      return new Promise(function (resolve, reject) {
        var r = store(db, "readwrite").put(rec);
        r.onsuccess = function () { resolve(rec); };
        r.onerror = function () { reject(r.error); };
      });
    });
  }

  function allRecords() {
    return openDB().then(function (db) {
      return new Promise(function (resolve, reject) {
        var r = store(db, "readonly").getAll();
        r.onsuccess = function () { resolve(r.result || []); };
        r.onerror = function () { reject(r.error); };
      });
    });
  }

  function deleteRecord(uuid) {
    return openDB().then(function (db) {
      return new Promise(function (resolve) {
        var r = store(db, "readwrite").delete(uuid);
        r.onsuccess = r.onerror = function () { resolve(); };
      });
    });
  }

  // ---- helpers ----------------------------------------------------------
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function getCookie(name) {
    var m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? decodeURIComponent(m.pop()) : "";
  }

  // The cookie is the live token; the <meta> tag is only a fallback. A page
  // restored from the service-worker cache carries whatever token was minted
  // when it was cached, and Django rotates the token on every login — trusting
  // the stale tag makes every sync 403 until the user happens to load a fresh
  // page.
  function csrf() {
    var cookie = getCookie("csrftoken");
    if (cookie) return cookie;
    var meta = document.querySelector('meta[name="csrf-token"]');
    return (meta && meta.getAttribute("content")) || "";
  }

  // Serialise a form to a plain object; repeated names (e.g. medicine_id[])
  // collapse into arrays, matching what Django's request.POST.getlist expects.
  // File inputs cannot ride in the outbox — they are dropped and reported, so a
  // scan or a photo is re-attached once back online rather than silently lost.
  function serialiseForm(form, submitter) {
    var data = {};
    var droppedFiles = [];
    // A <select> posts an id. "Doctor: 7" on a printed slip tells the doctor
    // nothing, and the outbox screen cannot summarise a row of numbers either, so
    // keep the chosen text alongside the value.
    var labels = {};
    Array.prototype.forEach.call(form.querySelectorAll("select[name]"), function (sel) {
      var opt = sel.options[sel.selectedIndex];
      if (opt && opt.value) labels[sel.name] = (opt.text || "").trim();
    });
    new FormData(form).forEach(function (value, key) {
      if (key === "csrfmiddlewaretoken") return;
      if (value instanceof File) {
        if (value.name) droppedFiles.push(value.name);
        return;
      }
      if (Object.prototype.hasOwnProperty.call(data, key)) {
        if (!Array.isArray(data[key])) data[key] = [data[key]];
        data[key].push(value);
      } else {
        data[key] = value;
      }
    });
    // A named submit button is part of the submission (the catalog screens use
    // one to say "add" rather than "update prices").
    if (submitter && submitter.name && !(submitter.name in data)) {
      data[submitter.name] = submitter.value || "";
    }
    return { data: data, droppedFiles: droppedFiles, labels: labels };
  }

  function counts() {
    return allRecords().then(function (recs) {
      var pending = 0, failed = 0;
      recs.forEach(function (r) {
        if (r.status === "failed") failed++; else pending++;
      });
      return { pending: pending, failed: failed, total: recs.length };
    });
  }

  // ---- UI: toasts + a pending badge ------------------------------------
  function toast(msg, kind) {
    var c = document.getElementById("toast-container");
    if (!c) { try { console.log(msg); } catch (e) {} return; }
    var el = document.createElement("div");
    el.textContent = msg;
    el.style.cssText =
      "background:#111827;color:#fff;padding:11px 16px;border-radius:10px;" +
      "margin-top:8px;box-shadow:0 4px 18px rgba(0,0,0,.25);font-size:14px;" +
      "max-width:min(340px,100%);line-height:1.45";
    if (kind === "warn") el.style.background = "#b45309";
    if (kind === "ok") el.style.background = "#047857";
    if (kind === "bad") el.style.background = "#b91c1c";
    c.appendChild(el);
    setTimeout(function () { el.remove(); }, 6500);
  }

  function refreshBadge() {
    return counts().then(function (n) {
      var b = document.getElementById("offline-badge");
      if (!b) return n;
      if (n.total > 0) {
        var parts = [];
        if (n.pending) parts.push("⏳ " + n.pending + " waiting to sync");
        if (n.failed) parts.push("⚠️ " + n.failed + " rejected");
        b.textContent = parts.join("  ·  ");
        // A rejection needs someone to act, so it must not look like progress.
        b.style.background = n.failed ? "#b91c1c" : "#b45309";
        b.style.display = "";
      } else {
        b.style.display = "none";
      }
      return n;
    });
  }

  // ---- server reachability ---------------------------------------------
  var lastPing = { at: 0, ok: false };

  function serverReachable() {
    // No network link at all: no point spending 3.5s finding that out.
    if (!navigator.onLine) return Promise.resolve(false);

    var now = Date.now();
    if (now - lastPing.at < PING_CACHE_MS) return Promise.resolve(lastPing.ok);

    var controller = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (controller) controller.abort(); },
                           PING_TIMEOUT_MS);

    return fetch(PING_URL, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      // A session that has expired answers with a redirect to the login page.
      // That is not "reachable" for our purposes — submitting would throw the
      // typed entry away on a login screen, so queue it instead.
      redirect: "manual",
      signal: controller ? controller.signal : undefined
    }).then(function (res) {
      clearTimeout(timer);
      var ok = res.status === 204 || (res.status >= 200 && res.status < 300);
      lastPing = { at: Date.now(), ok: ok };
      return ok;
    }).catch(function () {
      clearTimeout(timer);
      lastPing = { at: Date.now(), ok: false };
      return false;
    });
  }

  // ---- queue + sync -----------------------------------------------------
  function queue(kind, data, meta) {
    var rec = {
      uuid: uuid(), kind: kind, data: data, status: "pending",
      error: "", createdAt: new Date().toISOString(),
      labels: (meta && meta.labels) || {},
      label: (meta && meta.label) || kind,
      page: (meta && meta.page) || location.pathname
    };
    return putRecord(rec).then(function () { refreshBadge(); return rec; });
  }

  var syncing = false;

  function sync() {
    if (syncing) return Promise.resolve();
    syncing = true;
    // Read the queue *before* probing: with nothing to send there is nothing to
    // find out, and this runs on every page load and on a timer in every open
    // tab. An empty outbox must cost zero requests.
    return allRecords().then(function (recs) {
      var pending = recs.filter(function (r) { return r.status === "pending"; });
      if (!pending.length) { syncing = false; return; }
      return serverReachable().then(function (ok) {
        if (!ok) { syncing = false; return; }
        return postBatches(pending, { applied: 0, failed: 0 });
      });
    }).then(function (tally) {
      syncing = false;
      return refreshBadge().then(function () {
        if (!tally) return;
        if (tally.applied) {
          toast("✅ " + tally.applied + " offline record" +
                (tally.applied > 1 ? "s" : "") + " synced.", "ok");
        }
        if (tally.failed) {
          toast("⚠️ " + tally.failed + " offline record" +
                (tally.failed > 1 ? "s were" : " was") +
                " rejected. Open the Offline Queue to see why.", "bad");
        }
      });
    }).catch(function () {
      // Connection still flaky, or the server hiccuped: leave everything
      // queued and try again on the next round.
      syncing = false;
    });
  }

  // The server caps a batch (MAX_BATCH); a device that was offline for a week
  // must still drain, so send the queue in slices until it is empty.
  function postBatches(pending, tally) {
    if (!pending.length) return Promise.resolve(tally);
    var slice = pending.slice(0, SYNC_BATCH);
    var rest = pending.slice(SYNC_BATCH);

    // `at` is when the entry was actually typed on this device. The server stamps
    // the notifications it raises with it, so a morning's queued work reads as
    // "⏱ 10:32 offline —" history instead of ringing as new work on reconnect.
    var payload = { actions: slice.map(function (r) {
      return { uuid: r.uuid, kind: r.kind, data: r.data, at: r.createdAt };
    }) };

    return fetch(SYNC_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(payload)
    }).then(function (res) {
      if (!res.ok) throw new Error("sync http " + res.status);
      return res.json();
    }).then(function (out) {
      return applyResults(out.results || [], tally);
    }).then(function () {
      return postBatches(rest, tally);
    });
  }

  function applyResults(results, tally) {
    // Process sequentially so IndexedDB writes don't race each other.
    return results.reduce(function (chain, r) {
      return chain.then(function () {
        if (r.status === "applied") {
          tally.applied++;
          return deleteRecord(r.uuid);
        }
        if (r.status === "failed" || r.permanent) {
          tally.failed++;
          return allRecords().then(function (all) {
            var rec = all.filter(function (x) { return x.uuid === r.uuid; })[0];
            if (rec) {
              rec.status = "failed";
              rec.error = r.error || "rejected by the server";
              return putRecord(rec);
            }
          });
        }
        // status === "error" (transient): leave it pending for the next round.
      });
    }, Promise.resolve()).then(function () { return tally; });
  }

  // ---- form interception ------------------------------------------------
  function nativeSubmit(form, submitter) {
    // `form.submit()` does not fire the submit event, so this cannot loop back
    // into us. Constraint validation has already run — the browser only fires
    // `submit` on a form that passed it.
    if (submitter && submitter.name) {
      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = submitter.name;
      hidden.value = submitter.value || "";
      form.appendChild(hidden);
    }
    form.submit();
  }

  function busy(form, on) {
    var btns = form.querySelectorAll('button[type="submit"], input[type="submit"], button:not([type])');
    Array.prototype.forEach.call(btns, function (b) { b.disabled = !!on; });
  }

  function interceptForm(form) {
    form.addEventListener("submit", function (e) {
      // Another script on the page already blocked this submit (the POS blocks a
      // wholesale sale with no customer). Queueing it would smuggle past a rule
      // the desk relies on.
      if (e.defaultPrevented) return;

      e.preventDefault();
      var submitter = e.submitter || null;
      var kind = form.getAttribute("data-offline-kind");

      busy(form, true);
      serverReachable().then(function (ok) {
        if (ok) { nativeSubmit(form, submitter); return; }

        var packed = serialiseForm(form, submitter);
        return queue(kind, packed.data, {
          labels: packed.labels,
          label: form.getAttribute("data-offline-label") || kind
        }).then(function (rec) {
          busy(form, false);
          var msg = "💾 No connection — saved on this device. It will sync, and " +
                    "get its number, when the internet returns.";
          toast(msg, "ok");
          if (packed.droppedFiles.length) {
            toast("📎 " + packed.droppedFiles.join(", ") + " could not be saved " +
                  "offline. Attach the file again once you are back online.", "warn");
          }
          try { form.reset(); } catch (err) {}

          // A registered visit has to leave with a piece of paper. No notification
          // can reach the doctor's screen with no server in between, so the printed
          // slip is the handoff — the patient carries it to the room.
          if (kind === "visit") {
            try {
              sessionStorage.setItem("offline-slip-uuid", rec.uuid);
              sessionStorage.setItem("offline-slip-print", "1");
            } catch (err) {}
            window.location.href = SLIP_URL;
            return;
          }

          var go = form.getAttribute("data-offline-next");
          if (go) window.location.href = go;
        });
      }).catch(function () {
        busy(form, false);
        toast("This device could not save offline (private browsing?). Please try " +
              "again when you have a connection.", "bad");
      });
    });
  }

  // ---- GET Search Interception & Live Offline Filtering -----------------
  function interceptSearchForm(form) {
    form.addEventListener("submit", function (e) {
      if (navigator.onLine && lastPing.ok) return;   // online: submit GET normally
      if (navigator.onLine && Date.now() - lastPing.at > PING_CACHE_MS) return;
      e.preventDefault();
      var input = form.querySelector('input[name="q"], input[type="search"], input[type="text"]');
      var query = input ? (input.value || "").trim().toLowerCase() : "";
      filterDomTables(query);
      toast("🔍 Offline filter: '" + query + "'", "ok");
    });
  }

  function filterDomTables(query) {
    var tables = document.querySelectorAll("table.tbl, table");
    Array.prototype.forEach.call(tables, function (tbl) {
      var rows = tbl.querySelectorAll("tbody tr");
      Array.prototype.forEach.call(rows, function (row) {
        if (row.cells && row.cells.length === 1 && row.classList.contains("muted")) return;
        var text = (row.textContent || row.innerText || "").toLowerCase();
        row.style.display = (!query || text.indexOf(query) !== -1) ? "" : "none";
      });
    });
  }

  function setupLiveOfflineFilter() {
    var searchInputs = document.querySelectorAll('input[name="q"], .search input');
    Array.prototype.forEach.call(searchInputs, function (input) {
      input.addEventListener("input", function () {
        if (navigator.onLine && lastPing.ok) return;
        var query = (input.value || "").trim().toLowerCase();
        filterDomTables(query);
      });
    });
  }

  // ---- boot -------------------------------------------------------------
  function boot() {
    var forms = document.querySelectorAll("form[data-offline-kind]");
    Array.prototype.forEach.call(forms, interceptForm);

    var searchForms = document.querySelectorAll('form[method="get"], form.search');
    Array.prototype.forEach.call(searchForms, interceptSearchForm);
    setupLiveOfflineFilter();

    refreshBadge();
    sync();

    // The `online` event is unreliable on Android and never fires when the link
    // was up all along and only the uplink came back, so poll gently as well.
    setInterval(function () { sync(); }, RESYNC_EVERY_MS);
  }

  window.addEventListener("online", function () {
    lastPing = { at: 0, ok: false };     // force a fresh probe
    sync();
  });
  window.addEventListener("offline", function () {
    lastPing = { at: Date.now(), ok: false };
    toast("📴 Offline — new entries are being saved on this device.", "warn");
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) sync();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // Public surface: the outbox screen (templates/offline/queue.html) reads the
  // queue through this, and it is handy for debugging from the console.
  window.PharmaOffline = {
    queue: queue,
    sync: sync,
    counts: counts,
    all: allRecords,
    remove: deleteRecord,
    retry: function (id) {
      return allRecords().then(function (all) {
        var rec = all.filter(function (x) { return x.uuid === id; })[0];
        if (!rec) return;
        // A retry needs a fresh UUID: the old one is filed FAILED on the server
        // and would just be answered from the ledger.
        rec.uuid = uuid();
        rec.status = "pending";
        rec.error = "";
        return putRecord(rec).then(function () { return deleteRecord(id); })
                             .then(refreshBadge).then(sync);
      });
    },
    refreshBadge: refreshBadge,
    queueUrl: QUEUE_URL
  };
})();

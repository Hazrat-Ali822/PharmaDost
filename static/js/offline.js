/* Sehatyar offline outbox
 * -------------------------------------------------------------------------
 * When the connection is down, queue data-entry form submits in IndexedDB and
 * replay them to the server on reconnect. Any form marked
 * `data-offline-kind="<kind>"` is handled.
 *
 * Server-authoritative numbers (MRN, token, sale #) are assigned by the server
 * at sync time, so an offline save shows a "saved offline" message and the real
 * number arrives once the queue syncs.
 *
 * Online behaviour is deliberately untouched: a form only diverts here when
 * `navigator.onLine` is false, so a good connection submits exactly as before.
 * The server side is offline_sync/ (idempotent by the UUID minted here).
 */
(function () {
  "use strict";

  var DB_NAME = "pharmadost-offline";
  var STORE = "outbox";
  var SYNC_URL = "/offline/sync/";

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

  function csrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return (meta && meta.getAttribute("content")) || getCookie("csrftoken");
  }

  // Serialise a form to a plain object; repeated names (e.g. medicine_id[])
  // collapse into arrays, matching what Django's request.POST.getlist expects.
  function serialiseForm(form) {
    var data = {};
    new FormData(form).forEach(function (value, key) {
      if (key === "csrfmiddlewaretoken") return;
      if (Object.prototype.hasOwnProperty.call(data, key)) {
        if (!Array.isArray(data[key])) data[key] = [data[key]];
        data[key].push(value);
      } else {
        data[key] = value;
      }
    });
    return data;
  }

  function pendingCount() {
    return allRecords().then(function (recs) {
      return recs.filter(function (r) { return r.status === "pending"; }).length;
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
      "max-width:340px;line-height:1.45";
    if (kind === "warn") el.style.background = "#b45309";
    if (kind === "ok") el.style.background = "#047857";
    c.appendChild(el);
    setTimeout(function () { el.remove(); }, 6500);
  }

  function refreshBadge() {
    return pendingCount().then(function (n) {
      var b = document.getElementById("offline-badge");
      if (!b) return n;
      if (n > 0) {
        b.textContent = "⏳ " + n + " waiting to sync";
        b.style.display = "";
      } else {
        b.style.display = "none";
      }
      return n;
    });
  }

  // ---- queue + sync -----------------------------------------------------
  function queue(kind, data) {
    var rec = {
      uuid: uuid(), kind: kind, data: data, status: "pending",
      error: "", createdAt: new Date().toISOString()
    };
    return putRecord(rec).then(function () { refreshBadge(); return rec; });
  }

  var syncing = false;

  function sync() {
    if (syncing || !navigator.onLine) return Promise.resolve();
    syncing = true;
    return allRecords().then(function (recs) {
      var pending = recs.filter(function (r) { return r.status === "pending"; });
      if (!pending.length) { syncing = false; return; }

      var payload = { actions: pending.map(function (r) {
        return { uuid: r.uuid, kind: r.kind, data: r.data };
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
        return applyResults(out.results || []);
      }).then(function (tally) {
        syncing = false;
        return refreshBadge().then(function () {
          if (tally.applied) {
            toast("✅ " + tally.applied + " offline record" +
                  (tally.applied > 1 ? "s" : "") + " synced.", "ok");
          }
          if (tally.failed) {
            toast("⚠️ " + tally.failed + " offline record" +
                  (tally.failed > 1 ? "s were" : " was") +
                  " rejected — they need to be re-entered.", "warn");
          }
        });
      }).catch(function () {
        // Connection still flaky, or the server hiccuped: leave everything
        // queued and try again on the next online event.
        syncing = false;
      });
    }).catch(function () { syncing = false; });
  }

  function applyResults(results) {
    var tally = { applied: 0, failed: 0 };
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
  function interceptForm(form) {
    form.addEventListener("submit", function (e) {
      if (navigator.onLine) return;        // online: submit exactly as before
      e.preventDefault();
      var kind = form.getAttribute("data-offline-kind");
      var data = serialiseForm(form);
      queue(kind, data).then(function () {
        toast("💾 Saved offline. It will sync — and get its " +
              "number — when the internet returns.", "ok");
        try { form.reset(); } catch (err) {}
        var go = form.getAttribute("data-offline-next");
        if (go) window.location.href = go;
      }).catch(function () {
        toast("This device could not save offline (private mode?). Please try " +
              "again when you have a connection.", "warn");
      });
    });
  }

  // ---- GET Search Interception & Live Offline Filtering -----------------
  function interceptSearchForm(form) {
    form.addEventListener("submit", function (e) {
      if (navigator.onLine) return;        // online: submit GET normally
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
        if (navigator.onLine) return;
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
    if (navigator.onLine) sync();
  }

  window.addEventListener("online", function () {
    toast("🔌 Back online — syncing your offline entries…");
    sync();
  });
  window.addEventListener("offline", function () {
    toast("📴 Offline — new entries are being saved on this device.", "warn");
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // Small public surface for debugging / other pages.
  window.PharmaOffline = { queue: queue, sync: sync, pendingCount: pendingCount };
})();

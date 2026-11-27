(function () {
  "use strict";
  var root = document.documentElement;

  // Theme toggle (dark is the default look)
  document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("fragdex-theme", next); } catch (e) { /* ignore */ }
    });
  });

  // Hide / show IP addresses (a view preference, remembered per browser)
  var ipButtons = document.querySelectorAll("[data-ip-toggle]");
  var syncIpButtons = function () {
    var hidden = root.hasAttribute("data-hide-ips");
    ipButtons.forEach(function (b) {
      b.setAttribute("aria-pressed", hidden ? "true" : "false");
      b.setAttribute("title", hidden ? "Show IP addresses" : "Hide IP addresses");
    });
  };
  ipButtons.forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var hide = !root.hasAttribute("data-hide-ips");
      if (hide) root.setAttribute("data-hide-ips", ""); else root.removeAttribute("data-hide-ips");
      try { localStorage.setItem("fragdex-hide-ips", hide ? "1" : "0"); } catch (err) { /* ignore */ }
      syncIpButtons();
    });
  });
  syncIpButtons();

  // Popovers: only one open at a time; close on outside click / Escape
  var pops = document.querySelectorAll("details.pop");
  pops.forEach(function (d) {
    d.addEventListener("toggle", function () {
      if (!d.open) return;
      pops.forEach(function (o) { if (o !== d) o.open = false; });
      var body = d.querySelector(".pop-body");
      if (body) {
        body.classList.remove("right");
        var r = body.getBoundingClientRect();
        if (r.right > window.innerWidth - 8) body.classList.add("right");
      }
    });
  });
  document.addEventListener("click", function (e) {
    pops.forEach(function (d) { if (d.open && !d.contains(e.target)) d.open = false; });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") pops.forEach(function (d) { d.open = false; });
  });

  // Copy-to-clipboard buttons
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      var text = btn.getAttribute("data-copy");
      var done = function () {
        var old = btn.getAttribute("title");
        btn.setAttribute("title", "Copied!");
        btn.classList.add("copied");
        setTimeout(function () { btn.setAttribute("title", old || "Copy"); btn.classList.remove("copied"); }, 1200);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done);
      } else {
        var ta = document.createElement("textarea");
        ta.value = text; document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); done(); } catch (err) { /* ignore */ }
        document.body.removeChild(ta);
      }
    });
  });

  // Confirm before destructive submits
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!window.confirm(f.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  // Filter selects / checkboxes submit immediately
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });

  // Upload drop zone
  var dz = document.querySelector(".dropzone");
  if (dz) {
    var input = dz.querySelector("input[type=file]");
    var label = dz.querySelector("[data-files-label]");
    var submit = document.querySelector("[data-upload-submit]");
    var show = function () {
      var n = input.files.length;
      label.textContent = n ? n + " file" + (n === 1 ? "" : "s") + " selected" : label.getAttribute("data-default");
      if (submit) submit.disabled = n === 0;
    };
    dz.addEventListener("click", function (e) { if (e.target !== input) input.click(); });
    dz.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove("over"); });
    });
    dz.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); }
    });
    var form = dz.closest("form");
    form.addEventListener("submit", function () {
      if (submit) { submit.disabled = true; submit.textContent = "Uploading…"; }
    });
    show();
  }

  // Settings: live progress of the background Steam avatar / ban refresh
  var sp = document.querySelector("[data-steam-progress]");
  if (sp) {
    var spField = function (k) { return document.querySelector('[data-sp="' + k + '"]'); };
    var num = function (n) { return Number(n).toLocaleString(); };
    var dur = function (s) {
      if (s < 90) return Math.max(1, Math.round(s)) + " s";
      if (s < 5400) return Math.round(s / 60) + " min";
      return (s / 3600).toFixed(1) + " h";
    };
    var spRender = function (p) {
      sp.setAttribute("data-state", p.state);
      spField("done").textContent = num(p.done);
      spField("total").textContent = num(p.total);
      spField("percent").textContent = p.percent;
      spField("avatars").textContent = num(p.avatars);
      spField("bar").style.width = p.percent + "%";
      sp.querySelector("[role=progressbar]").setAttribute("aria-valuenow", p.percent);
      spField("last_run").textContent = p.last_run || "—";
      var text;
      if (p.state === "nokey") text = "Add a key to start";
      else if (p.state === "running") text = "Fetching from Steam… " + num(p.pending) + " left" + (p.eta ? " · about " + dur(p.eta) : "");
      else if (p.state === "error") text = "Last run failed" + (p.next_in != null ? " · retrying in " + dur(p.next_in) : "");
      else if (p.pending) text = num(p.pending) + " waiting" + (p.next_in != null ? " · next run in " + dur(p.next_in) : "");
      else text = "All players up to date";
      spField("status").textContent = text;
      var err = spField("error");
      err.textContent = p.error || "";
      err.hidden = !p.error;
    };
    var spPoll = function () {
      fetch("/admin/settings/steam.json", { credentials: "same-origin" }).then(function (r) { return r.json(); })
        .then(function (p) {
          spRender(p);
          if (p.state !== "nokey") setTimeout(spPoll, p.state === "running" ? 2000 : 15000);
        }).catch(function () { setTimeout(spPoll, 15000); });
    };
    if (sp.getAttribute("data-state") !== "nokey") spPoll();
  }

  // Settings: live progress of the FragQ links sync, from what the worker has received and processed
  var fq = document.querySelector("[data-fragq-progress]");
  if (fq) {
    var fqField = function (k) { return fq.querySelector('[data-fq="' + k + '"]'); };
    var fqLabel = document.querySelector("[data-fq-label]");
    var fqBar = fq.querySelector("[role=progressbar]");
    var fqCount = function (n) { return Number(n).toLocaleString(); };
    var fqBytes = function (n) {
      if (n < 1024) return n + " B";
      if (n < 1048576) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
      return (n / 1048576).toFixed(1) + " MB";
    };
    var fqTime = function (s) { return s < 90 ? s + " s" : Math.floor(s / 60) + " min " + (s % 60) + " s"; };
    var fqStage = function (p) {
      var of = fqCount(p.done) + " of " + fqCount(p.total);
      if (p.stage === "download") {
        if (!p.done) return "Waiting for FragQ to answer…";
        return "Downloading · " + fqBytes(p.done) + (p.total ? " of " + fqBytes(p.total) + " · " + p.percent + "%" : " received");
      }
      if (p.stage === "save") return "Saving links · " + of;
      if (p.stage === "match") return "Matching players · " + of;
      if (p.stage === "players") return "Updating new players · " + of;
      return "Connecting to FragQ…";
    };
    var fqWasRunning = fq.getAttribute("data-state") === "running";
    var fqRender = function (p) {
      if (!p.running) {
        if (fqLabel) fqLabel.textContent = "Sync now";
        if (fqWasRunning) {
          fq.setAttribute("data-state", "idle");
          fq.removeAttribute("data-indeterminate");
          fqField("title").textContent = "Sync finished";
          fqField("bar").style.width = "100%";
          var status = fqField("status"), link = document.createElement("a");
          link.href = "/admin/settings#fragq";
          link.textContent = "Reload to see the results";
          link.addEventListener("click", function () { window.location.reload(); });
          status.textContent = "";
          status.appendChild(link);
        }
        fqWasRunning = false;
        return;
      }
      fqWasRunning = true;
      fq.hidden = false;
      fq.setAttribute("data-state", "running");
      if (fqLabel) fqLabel.textContent = "Syncing…";
      fqField("title").textContent = "Syncing " + (p.connection || "FragQ");
      fqField("count").textContent = p.count > 1 ? "Connection " + p.number + " of " + p.count : "";
      var known = p.percent !== null && p.done > 0;
      fq.toggleAttribute("data-indeterminate", !known);
      fqField("bar").style.width = known ? p.percent + "%" : "";
      if (known) fqBar.setAttribute("aria-valuenow", p.percent); else fqBar.removeAttribute("aria-valuenow");
      fqField("status").textContent = fqStage(p);
      fqField("elapsed").textContent = fqTime(p.elapsed);
    };
    var fqPoll = function () {
      fetch("/admin/settings/fragq.json", { credentials: "same-origin" }).then(function (r) { return r.json(); })
        .then(function (p) {
          fqRender(p);
          setTimeout(fqPoll, p.running ? 1000 : 5000);
        }).catch(function () { setTimeout(fqPoll, 10000); });
    };
    fqPoll();
  }

  // Settings: live progress of the base IP refresh, from the bytes downloaded and the ranges and addresses processed
  var vp = document.querySelector("[data-vpn-progress]");
  if (vp) {
    var vpField = function (k) { return vp.querySelector('[data-vp="' + k + '"]'); };
    var vpLabel = document.querySelector("[data-vp-label]");
    var vpBar = vp.querySelector("[role=progressbar]");
    var vpCount = function (n) { return Number(n).toLocaleString(); };
    var vpBytes = function (n) {
      if (n < 1024) return n + " B";
      if (n < 1048576) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
      return (n / 1048576).toFixed(1) + " MB";
    };
    var vpTime = function (s) { return s < 90 ? s + " s" : Math.floor(s / 60) + " min " + (s % 60) + " s"; };
    var vpStage = function (p) {
      var of = vpCount(p.done) + " of " + vpCount(p.total);
      if (p.stage === "download") {
        if (!p.done) return "Connecting to " + (p.source || "the list") + "…";
        return "Downloading " + p.source + " · " + vpBytes(p.done) + (p.total ? " of " + vpBytes(p.total) + " · " + p.percent + "%" : " received");
      }
      if (p.stage === "save") return "Saving " + (p.source ? p.source + " " : "") + "ranges · " + of;
      if (p.stage === "match") return "Matching addresses · " + of;
      return "Starting…";
    };
    var vpWasRunning = vp.getAttribute("data-state") === "running";
    var vpBaseAt = vp.getAttribute("data-base-at") || "";
    var vpRender = function (p) {
      if (!p.running && p.wanted) {      // asked for, and the worker hasn't picked it up yet
        vpWasRunning = true;
        vp.hidden = false;
        vp.setAttribute("data-indeterminate", "");
        vpField("status").textContent = "Starting…";
        return;
      }
      if (!p.running) {
        if (p.base_at && p.base_at !== vpBaseAt) vpWasRunning = true;   // finished between two polls
        vpBaseAt = p.base_at || vpBaseAt;
        if (vpLabel) vpLabel.textContent = "Refresh base IPs";
        if (vpWasRunning) {
          vp.setAttribute("data-state", "idle");
          vp.removeAttribute("data-indeterminate");
          vpField("title").textContent = "Finished";
          vpField("count").textContent = "";
          vpField("bar").style.width = "100%";
          vp.hidden = false;
          var status = vpField("status"), link = document.createElement("a");
          link.href = "/admin/settings#vpn";
          link.textContent = "Reload to see the results";
          link.addEventListener("click", function () { window.location.reload(); });
          status.textContent = "";
          status.appendChild(link);
        }
        vpWasRunning = false;
        return;
      }
      vpWasRunning = true;
      vp.hidden = false;
      vp.setAttribute("data-state", "running");
      if (vpLabel) vpLabel.textContent = "Refreshing…";
      vpField("title").textContent = p.state === "matching" ? "Matching addresses" : "Refreshing base IPs";
      vpField("count").textContent = p.count > 1 && p.stage !== "match" ? "List " + p.number + " of " + p.count : "";
      var known = p.percent !== null && p.done > 0;
      vp.toggleAttribute("data-indeterminate", !known);
      vpField("bar").style.width = known ? p.percent + "%" : "";
      if (known) vpBar.setAttribute("aria-valuenow", p.percent); else vpBar.removeAttribute("aria-valuenow");
      vpField("status").textContent = vpStage(p);
      vpField("elapsed").textContent = vpTime(p.elapsed);
    };
    var vpPoll = function () {
      fetch("/admin/settings/vpn.json", { credentials: "same-origin" }).then(function (r) { return r.json(); })
        .then(function (p) {
          vpRender(p);
          setTimeout(vpPoll, p.running ? 1000 : 5000);
        }).catch(function () { setTimeout(vpPoll, 10000); });
    };
    vpPoll();
  }

  // Import page: refresh while jobs are queued/running
  var watch = document.querySelector("[data-import-active]");
  if (watch && parseInt(watch.getAttribute("data-import-active"), 10) > 0) {
    var last = watch.getAttribute("data-import-last");
    var active = watch.getAttribute("data-import-active");
    var poll = function () {
      fetch("/import/status.json", { credentials: "same-origin" }).then(function (r) { return r.json(); })
        .then(function (s) {
          if (String(s.active) !== active || String(s.last) !== last) window.location.reload();
          else setTimeout(poll, 3000);
        }).catch(function () { setTimeout(poll, 10000); });
    };
    setTimeout(poll, 3000);
  }
})();

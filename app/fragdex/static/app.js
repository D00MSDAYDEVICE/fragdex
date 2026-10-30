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

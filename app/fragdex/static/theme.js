// Loaded in <head> so saved view preferences apply before first paint.
(function () {
  var root = document.documentElement, t = null, hide = null;
  try {
    t = localStorage.getItem("fragdex-theme");
    hide = localStorage.getItem("fragdex-hide-ips");
  } catch (e) { /* storage blocked */ }
  if (t === "light" || t === "dark") root.setAttribute("data-theme", t);
  if (hide === "1") root.setAttribute("data-hide-ips", "");
})();

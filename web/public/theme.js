// Runs before first paint so the page never flashes the wrong theme.
(function () {
  var stored = null;
  try { stored = localStorage.getItem('spillikin-theme'); } catch (error) { /* storage unavailable */ }
  var light = stored ? stored === 'light' : window.matchMedia('(prefers-color-scheme: light)').matches;
  document.documentElement.dataset.theme = light ? 'light' : 'dark';
})();

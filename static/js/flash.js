// The flash panel of a complete ESP32-family build (feature browser-flashing).
// The panel arrives in an htmx swap or in the first page load, so both events start the checks.
(function () {
  'use strict';

  function show(panel, ids) {
    ids.forEach(id => { panel.querySelector('#' + id).hidden = false; });
  }

  // Load esptool-js from the URL that the panel names, so the module comes from this host only.
  function loadEsptool(panel) {
    const start = panel.querySelector('#flash-start');
    import(panel.dataset.esptoolUrl).then(module => {
      panel.esptool = module;
      start.disabled = false;
    }, () => {
      show(panel, ['flash-downloads']);
    });
  }

  function check(panel) {
    if (panel.dataset.flashChecked) {
      return;
    }
    panel.dataset.flashChecked = 'true';
    // Chrome removes navigator.serial outside a secure context, so the HTTPS message goes first.
    if (!window.isSecureContext) {
      show(panel, ['flash-insecure', 'flash-downloads']);
    } else if (!('serial' in window.navigator)) {
      show(panel, ['flash-unsupported', 'flash-downloads']);
    } else {
      show(panel, ['flash-ready', 'flash-start']);
      loadEsptool(panel);
    }
  }

  function checkAll() {
    document.querySelectorAll('#flash-panel').forEach(check);
  }

  document.addEventListener('htmx:load', evt => {
    const elt = evt.detail.elt;
    const panel = elt.id === 'flash-panel' ? elt : elt.querySelector('#flash-panel');
    if (panel) {
      check(panel);
    }
  });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', checkAll);
  } else {
    checkAll();
  }
})();

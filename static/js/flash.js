// The flash panel of a complete ESP32-family build (feature browser-flashing).
// The panel arrives in an htmx swap or in the first page load, so both events start the checks.
// The esptool-js calls follow the 0.7.1 type definitions: https://github.com/espressif/esptool-js (v0.7.1).
(function () {
  'use strict';

  // The first rate is fast. Some USB serial bridges fail at it, so the second rate is the fallback.
  const BAUD_RATES = [921600, 115200];
  // esptool-js 0.7.1 throws this message when no ROM bootloader answers the sync.
  const SYNC_FAILURE = 'Failed to connect with the device';
  // ESP-IDF writes the partition table at 0x8000. The table holds at most 0xC00 bytes.
  const PARTITION_TABLE_OFFSET = 0x8000;
  const PARTITION_TABLE_SIZE = 0xc00;
  const PARTITION_MAGIC = 0x50aa;
  const STEP_TEXT = {
    connecting: 'Connecting. If this takes more than 10 seconds, hold BOOT and press RESET.',
    erasing: 'Erasing.',
    writing: 'Writing.',
    verifying: 'Verifying.',
    resetting: 'Resetting.',
  };
  const DONE_TEXT = {
    full: 'Done. The device restarts with your factory defaults.',
    update: 'Done. The device restarts and keeps its saved settings.',
  };
  const NO_ESP32 = 'No ESP32 found on this port.';
  const OTHER_TABLE = 'The partition table on the device differs from this build. Use Full install.';
  const NO_APP_PARTITION = 'The build manifest has no app partition. Use Full install.';
  // Each flow state replaces the one before it, so the operator sees one state at a time.
  const FLOW_STATES = ['flash-ready', 'flash-confirm', 'flash-progress', 'flash-done', 'flash-error'];

  function show(panel, ids) {
    ids.forEach(id => { panel.querySelector('#' + id).hidden = false; });
  }

  function showState(panel, ids) {
    FLOW_STATES.forEach(id => { panel.querySelector('#' + id).hidden = !ids.includes(id); });
  }

  function progress(panel, step, percent) {
    panel.querySelector('#flash-step').textContent = STEP_TEXT[step];
    const bar = panel.querySelector('#flash-bar');
    bar.style.width = percent + '%';
    bar.textContent = percent + '%';
    bar.setAttribute('aria-valuenow', percent);
  }

  // esptool-js verifies a write only when the caller gives an MD5 function, and Web Crypto has no MD5.
  // This is the MD5 algorithm of RFC 1321.
  const MD5_SHIFTS = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
  const MD5_K = Array.from({ length: 64 }, (_, i) => Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) >>> 0);

  function md5Hex(bytes) {
    const padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, (bytes.length * 8) >>> 0, true);
    view.setUint32(padded.length - 4, Math.floor(bytes.length / 0x20000000), true);
    const state = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476];
    for (let offset = 0; offset < padded.length; offset += 64) {
      let [a, b, c, d] = state;
      for (let i = 0; i < 64; i++) {
        let f;
        let g;
        if (i < 16) {
          f = (b & c) | (~b & d);
          g = i;
        } else if (i < 32) {
          f = (d & b) | (~d & c);
          g = (5 * i + 1) % 16;
        } else if (i < 48) {
          f = b ^ c ^ d;
          g = (3 * i + 5) % 16;
        } else {
          f = c ^ (b | ~d);
          g = (7 * i) % 16;
        }
        const shift = MD5_SHIFTS[(i >> 4) * 4 + (i % 4)];
        const sum = (a + f + MD5_K[i] + view.getUint32(offset + g * 4, true)) | 0;
        a = d;
        d = c;
        c = b;
        b = (b + ((sum << shift) | (sum >>> (32 - shift)))) | 0;
      }
      state[0] = (state[0] + a) | 0;
      state[1] = (state[1] + b) | 0;
      state[2] = (state[2] + c) | 0;
      state[3] = (state[3] + d) | 0;
    }
    return state.map(word => [0, 8, 16, 24]
      .map(bits => ((word >>> bits) & 0xff).toString(16).padStart(2, '0')).join('')).join('');
  }

  // Upstream esp32_pre.py copies offsets and sizes from the partitions CSV: an integer, a hex string, or "<n>K" / "<n>M".
  function parseSize(value) {
    if (typeof value === 'number') {
      return value;
    }
    const text = String(value).trim();
    if (/^\d+$/.test(text)) {
      return parseInt(text, 10);
    }
    if (/^0x[0-9a-f]+$/i.test(text)) {
      return parseInt(text, 16);
    }
    const scaled = /^(\d+)([KM])$/i.exec(text);
    if (scaled) {
      return parseInt(scaled[1], 10) * (scaled[2].toUpperCase() === 'K' ? 1024 : 1024 * 1024);
    }
    return NaN;
  }

  // Each entry of an ESP-IDF partition table has 32 bytes: magic, type, subtype, offset, size, name, flags.
  function devicePartitions(table) {
    const view = new DataView(table.buffer, table.byteOffset, table.byteLength);
    const partitions = [];
    for (let i = 0; i + 32 <= table.byteLength && view.getUint16(i, true) === PARTITION_MAGIC; i += 32) {
      partitions.push([view.getUint32(i + 4, true), view.getUint32(i + 8, true)]);
    }
    return partitions;
  }

  async function download(url, name) {
    const response = await fetch(url + '/' + encodeURIComponent(name));
    if (!response.ok) {
      throw new Error('The artifact ' + name + ' did not download (HTTP ' + response.status + ').');
    }
    return response;
  }

  // Return the artifact to write, its flash offset, and the partition table of the build for an update.
  async function planWrite(artifactsUrl, mode) {
    const response = await fetch(artifactsUrl);
    if (!response.ok) {
      throw new Error('The artifact list did not download (HTTP ' + response.status + ').');
    }
    const artifacts = await response.json();
    const byKind = kind => artifacts.find(artifact => artifact.kind === kind);
    if (mode === 'full') {
      const factory = byKind('factory');
      if (!factory) {
        throw new Error('The build has no factory artifact.');
      }
      return { name: factory.name, address: 0, partitions: null };
    }
    const app = byKind('app');
    const manifest = byKind('manifest');
    if (!app || !manifest) {
      throw new Error(NO_APP_PARTITION);
    }
    const parts = (await (await download(artifactsUrl, manifest.name)).json()).part || [];
    const appPartition = parts.find(part => part.type === 'app');
    if (!appPartition) {
      throw new Error(NO_APP_PARTITION);
    }
    return {
      name: app.name,
      address: parseSize(appPartition.offset),
      partitions: parts.map(part => [parseSize(part.offset), parseSize(part.size)]),
    };
  }

  function manualCommand(mode, plan) {
    const target = mode === 'full' ? '--erase-all 0x0' : '0x' + plan.address.toString(16);
    return 'esptool.py --port <PORT> --baud ' + BAUD_RATES[0] + ' write_flash ' + target + ' ' + plan.name;
  }

  async function disconnect(transport) {
    try {
      await transport.disconnect();
    } catch (error) {
      // The port is already closed when the device is unplugged, and the flow reports the first error only.
    }
  }

  async function connect(esptool, port) {
    let lastError;
    for (const baudrate of BAUD_RATES) {
      const transport = new esptool.Transport(port, false);
      const loader = new esptool.ESPLoader({ transport, baudrate });
      try {
        await loader.main();
        return { loader, transport };
      } catch (error) {
        lastError = error;
        // The next attempt opens the port again, so this attempt must close it.
        await disconnect(transport);
        // Each attempt syncs at the ROM rate of 115200, so a sync failure repeats at the lower rate.
        if (String(error && error.message).includes(SYNC_FAILURE)) {
          break;
        }
      }
    }
    throw lastError;
  }

  async function checkPartitionTable(loader, partitions) {
    const device = devicePartitions(await loader.readFlash(PARTITION_TABLE_OFFSET, PARTITION_TABLE_SIZE));
    const same = device.length === partitions.length &&
      device.every(([offset, size], i) => offset === partitions[i][0] && size === partitions[i][1]);
    if (!same) {
      throw new Error(OTHER_TABLE);
    }
  }

  function bindFlow(panel) {
    const el = id => panel.querySelector('#' + id);
    let port = null;
    let mode = 'full';

    function showError(error, plan) {
      const message = error && error.message ? error.message : String(error);
      el('flash-error-text').textContent = message.includes(SYNC_FAILURE) ? NO_ESP32 : message;
      el('flash-manual').hidden = !plan;
      el('flash-command').textContent = plan ? manualCommand(mode, plan) : '';
      // The mode choice stays, so the operator can select Full install when an error advises it.
      showState(panel, ['flash-error', 'flash-ready']);
    }

    async function run() {
      showState(panel, ['flash-progress']);
      progress(panel, 'connecting', 0);
      let plan = null;
      let transport = null;
      try {
        plan = await planWrite(panel.dataset.artifactsUrl, mode);
        const data = new Uint8Array(await (await download(panel.dataset.artifactsUrl, plan.name)).arrayBuffer());
        const connection = await connect(panel.esptool, port);
        transport = connection.transport;
        const loader = connection.loader;
        if (plan.partitions) {
          await checkPartitionTable(loader, plan.partitions);
        }
        if (mode === 'full') {
          progress(panel, 'erasing', 0);
          await loader.eraseFlash();
        }
        progress(panel, 'writing', 0);
        await loader.writeFlash({
          fileArray: [{ data, address: plan.address }],
          // "keep" leaves the bootloader header of the artifact as the build made it.
          flashMode: 'keep',
          flashFreq: 'keep',
          flashSize: 'keep',
          eraseAll: false,
          compress: true,
          // esptool-js compares this MD5 with the flash after the last block, so a complete write starts the verify.
          reportProgress: (index, written, total) => {
            progress(panel, written < total ? 'writing' : 'verifying', Math.floor((100 * written) / total));
          },
          calculateMD5Hash: md5Hex,
        });
        progress(panel, 'resetting', 100);
        await loader.after('hard_reset');
        await disconnect(transport);
        el('flash-done').textContent = DONE_TEXT[mode];
        showState(panel, ['flash-done', 'flash-ready']);
      } catch (error) {
        if (transport) {
          await disconnect(transport);
        }
        showError(error, plan);
      }
    }

    // A full install erases the device, so it waits for the operator to confirm the erase warning.
    function begin() {
      if (mode === 'full') {
        showState(panel, ['flash-confirm']);
      } else {
        run();
      }
    }

    // requestPort needs the click of the operator, so the port picker opens before any other step.
    el('flash-start').addEventListener('click', async () => {
      mode = el('flash-mode-update').checked ? 'update' : 'full';
      try {
        port = await navigator.serial.requestPort();
      } catch (error) {
        if (error.name !== 'NotFoundError') {
          showError(error, null);
        }
        return;
      }
      begin();
    });
    el('flash-confirm-erase').addEventListener('click', run);
    el('flash-cancel').addEventListener('click', () => showState(panel, ['flash-ready']));
    el('flash-retry').addEventListener('click', begin);
  }

  // Load esptool-js from the URL that the panel names, so the module comes from this host only.
  function loadEsptool(panel) {
    const start = panel.querySelector('#flash-start');
    import(panel.dataset.esptoolUrl).then(module => {
      panel.esptool = module;
      bindFlow(panel);
      start.disabled = false;
    }, () => {
      panel.querySelector('#flash-ready').hidden = true;
      show(panel, ['flash-load-failed', 'flash-downloads']);
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

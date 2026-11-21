// Runs static/js/flash.js in Node with a fake page, a fake serial port and a fake esptool-js.
// No real port opens. The harness prints one JSON object: the event log and the final panel state.
//
// Usage: node --experimental-vm-modules flash_flow_harness.cjs <flash.js> <scenario JSON>
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const vm = require('vm');

const [script, scenarioJson] = process.argv.slice(2);
const scenario = JSON.parse(scenarioJson);
const log = [];

const IDS = [
  'flash-ready', 'flash-mode-full', 'flash-mode-update', 'flash-start', 'flash-confirm',
  'flash-erase-warning', 'flash-confirm-erase', 'flash-cancel', 'flash-progress', 'flash-step',
  'flash-bar', 'flash-done', 'flash-error', 'flash-error-text', 'flash-manual', 'flash-command',
  'flash-retry', 'flash-unsupported', 'flash-insecure', 'flash-load-failed', 'flash-downloads',
];
// The log records each change of these elements, so a test can see the order of the screens.
const LOGGED = ['flash-confirm', 'flash-progress', 'flash-done', 'flash-error', 'flash-ready'];
// The elements that the template marks `hidden`, and the container of each other element, as in flash_panel.html.
const HIDDEN = [...LOGGED, 'flash-unsupported', 'flash-insecure', 'flash-load-failed', 'flash-downloads'];
const PARENT = {
  'flash-mode-full': 'flash-ready', 'flash-mode-update': 'flash-ready', 'flash-start': 'flash-ready',
  'flash-erase-warning': 'flash-confirm', 'flash-confirm-erase': 'flash-confirm', 'flash-cancel': 'flash-confirm',
  'flash-step': 'flash-progress', 'flash-bar': 'flash-progress',
  'flash-error-text': 'flash-error', 'flash-manual': 'flash-error', 'flash-command': 'flash-manual', 'flash-retry': 'flash-error',
};

function element(id) {
  let hidden = HIDDEN.includes(id);
  let text = '';
  return {
    id, disabled: false, checked: false, dataset: {}, style: {}, attributes: {}, listeners: {},
    get hidden() { return hidden; },
    set hidden(value) {
      if (LOGGED.includes(id) && value !== hidden) log.push(`${value ? 'hide' : 'show'}:${id}`);
      hidden = value;
    },
    get textContent() { return text; },
    set textContent(value) {
      if (id === 'flash-step' || id === 'flash-bar') log.push(`${id.slice(6)}:${value}`);
      text = String(value);
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    click() { return this.listeners.click && this.listeners.click({}); },
  };
}
const parts = Object.fromEntries(IDS.map(id => [id, element(id)]));
// The template disables Flash until esptool-js loads.
parts['flash-start'].disabled = true;
parts['flash-mode-full'].checked = scenario.mode !== 'update';
parts['flash-mode-update'].checked = scenario.mode === 'update';
const panel = element('flash-panel');
panel.dataset.esptoolUrl = '/static/vendor/esptool-js-0.7.1/bundle.js';
panel.dataset.artifactsUrl = '/api/v1/builds/b1/artifacts';
panel.querySelector = selector => parts[selector.replace('#', '')] || null;

// The build artifacts, with the names that the fake pio writes for heltec-v4.
const NAME = 'firmware-heltec-v4-2.7.26.54e0d8d';
const MANIFEST_PARTS = [
  { name: 'nvs', type: 'data', subtype: 'nvs', offset: '0x9000', size: '0x5000', flags: null },
  { name: 'otadata', type: 'data', subtype: 'ota', offset: '0xe000', size: '0x2000', flags: null },
  { name: 'app0', type: 'app', subtype: 'ota_0', offset: '0x10000', size: '0x640000', flags: null },
  { name: 'flashApp', type: 'app', subtype: 'ota_1', offset: '0x650000', size: '0xA0000', flags: null },
  { name: 'spiffs', type: 'data', subtype: 'spiffs', offset: '0x6F0000', size: '0x100000', flags: null },
];
const manifest = 'manifest' in scenario ? scenario.manifest : { version: '2.7.26.54e0d8d', part: MANIFEST_PARTS };
const artifacts = scenario.artifacts || [
  { name: `${NAME}.factory.bin`, kind: 'factory', size: 1001 },
  { name: `${NAME}.bin`, kind: 'app', size: 601 },
  { name: `${NAME}.mt.json`, kind: 'manifest', size: 100 },
  { name: `littlefs-heltec-v4-2.7.26.54e0d8d.bin`, kind: 'littlefs', size: 64 },
];
const contents = {};
for (const artifact of artifacts) {
  contents[artifact.name] = Uint8Array.from({ length: artifact.size }, (_, i) => (i * 7 + artifact.size) & 0xff);
}

function response(status, body) {
  return {
    ok: status === 200, status,
    json: async () => JSON.parse(body),
    arrayBuffer: async () => (typeof body === 'string' ? new TextEncoder().encode(body) : body).buffer,
  };
}
async function fakeFetch(url) {
  log.push(`fetch:${url}`);
  if (url === panel.dataset.artifactsUrl) return response(200, JSON.stringify(artifacts));
  const name = decodeURIComponent(url.slice(panel.dataset.artifactsUrl.length + 1));
  if ((scenario.missing || []).includes(name)) return response(404, '{}');
  if (name.endsWith('.mt.json')) return response(200, JSON.stringify(manifest));
  return contents[name] ? response(200, contents[name]) : response(404, '{}');
}

// A partition table in the binary layout of ESP-IDF gen_esp32part.py: 32-byte entries, then an MD5 entry.
function partitionTable(entries) {
  const table = new Uint8Array(0xc00).fill(0xff);
  const view = new DataView(table.buffer);
  entries.forEach(([offset, size], i) => {
    table.set([0xaa, 0x50, 0, 0], i * 32);
    view.setUint32(i * 32 + 4, offset, true);
    view.setUint32(i * 32 + 8, size, true);
  });
  table.set([0xeb, 0xeb], entries.length * 32);
  return table;
}
const SAME_TABLE = [[0x9000, 0x5000], [0xe000, 0x2000], [0x10000, 0x640000], [0x650000, 0xa0000], [0x6f0000, 0x100000]];

const writes = [];
class FakeTransport {
  constructor(port) { this.port = port; log.push('transport:new'); }
  async disconnect() { log.push('transport:disconnect'); }
}
class FakeESPLoader {
  constructor(options) {
    this.baudrate = options.baudrate;
    log.push(`loader:new:${options.baudrate}:${options.transport instanceof FakeTransport}`);
  }
  async main() {
    log.push(`loader:main:${this.baudrate}`);
    if (scenario.syncFail) throw new Error('Failed to connect with the device');
    if ((scenario.failAt || []).includes(this.baudrate)) throw new Error('Timeout');
    return 'ESP32-S3';
  }
  async readFlash(address, size) {
    log.push(`loader:readFlash:0x${address.toString(16)}:0x${size.toString(16)}`);
    return partitionTable(scenario.deviceTable || SAME_TABLE);
  }
  async eraseFlash() { log.push('loader:eraseFlash'); }
  async writeFlash(options) {
    const files = options.fileArray.map(file => `0x${file.address.toString(16)}:${file.data.length}`);
    log.push(`loader:writeFlash:${files.join(',')}`);
    const data = options.fileArray[0].data;
    writes.push({
      eraseAll: options.eraseAll, flashSize: options.flashSize, flashMode: options.flashMode,
      flashFreq: options.flashFreq, compress: options.compress,
      md5: options.calculateMD5Hash ? options.calculateMD5Hash(data) : null,
      expectedMd5: crypto.createHash('md5').update(data).digest('hex'),
      sameBytes: Buffer.from(data).equals(Buffer.from(contents[scenario.mode === 'update' ? `${NAME}.bin` : `${NAME}.factory.bin`] || [])),
    });
    options.reportProgress(0, 0, 200);
    options.reportProgress(0, 100, 200);
    if (scenario.writeError) throw new Error(scenario.writeError);
    options.reportProgress(0, 200, 200);
  }
  async after(mode) { log.push(`loader:after:${mode}`); }
}
globalThis.fakeEsptool = { ESPLoader: FakeESPLoader, Transport: FakeTransport };
const FAKE_MODULE = 'data:text/javascript,export const { ESPLoader, Transport } = globalThis.fakeEsptool;';

const port = { fake: true };
const sandbox = {
  console, setTimeout, TextDecoder, Uint8Array, DataView, Math,
  isSecureContext: true,
  fetch: fakeFetch,
  navigator: {
    serial: {
      requestPort: async () => {
        log.push('serial:requestPort');
        if (scenario.portCancel) {
          const error = new Error('No port selected by the user.');
          error.name = 'NotFoundError';
          throw error;
        }
        return port;
      },
    },
  },
  document: {
    readyState: 'complete',
    addEventListener: () => {},
    querySelectorAll: selector => (selector === '#flash-panel' ? [panel] : []),
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
new vm.Script(fs.readFileSync(script, 'utf8'), {
  filename: script,
  importModuleDynamically: async () => {
    if (scenario.loadFails) throw new Error('import failed');
    return import(FAKE_MODULE);
  },
}).runInContext(sandbox);

const settle = () => new Promise(resolve => setTimeout(resolve, 50));
const visible = id => !parts[id].hidden && (!PARENT[id] || visible(PARENT[id]));

// Answer the erase warning as the scenario says: confirm, cancel, or leave it open.
async function answerWarning() {
  if (!visible('flash-confirm') || scenario.confirm === undefined) return;
  log.push(scenario.confirm ? 'operator:confirm' : 'operator:cancel');
  parts[scenario.confirm ? 'flash-confirm-erase' : 'flash-cancel'].click();
  await settle();
}

(async () => {
  await settle();
  if (!parts['flash-start'].disabled) {
    log.push('operator:flash');
    parts['flash-start'].click();
    await settle();
    await answerWarning();
    if (scenario.retry && visible('flash-error')) {
      scenario.writeError = null;
      log.push('operator:retry');
      parts['flash-retry'].click();
      await settle();
      await answerWarning();
    }
  }
  console.log(JSON.stringify({
    log,
    writes,
    shown: IDS.filter(visible),
    startDisabled: parts['flash-start'].disabled,
    text: Object.fromEntries(['flash-step', 'flash-bar', 'flash-done', 'flash-error-text', 'flash-command', 'flash-erase-warning']
      .map(id => [id, parts[id].textContent])),
    barWidth: parts['flash-bar'].style.width,
  }));
})();

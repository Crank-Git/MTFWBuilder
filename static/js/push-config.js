// The push config page (feature push-config): connect to a device and stop when the device is managed.
// The library calls follow the vendored @meshtastic/core 2.6.7 build and its two transports (ADR 0008).

const el = id => document.getElementById(id);
// Each connect state replaces the one before it, so the operator sees one state at a time.
const CONNECT_STATES = ['push-reading', 'push-connected', 'push-managed', 'push-error'];
// @meshtastic/core 2.6.7 sends this status when the device answers the end of want_config (Types.DeviceStatusEnum).
const DEVICE_CONFIGURED = 7;
const NO_SECURITY = 'The device sent its config without a security section.';

function showState(id) {
  CONNECT_STATES.forEach(state => { el(state).hidden = state !== id; });
}

/**
 * Return a connected MeshDevice for the transport `kind`, "usb" or "bluetooth".
 *
 * Test hook: when `window.__pushConfigTestDevice` is set before the page loads, the page uses that object
 * as the device and opens no transport. The object needs the MeshDevice members that the page calls:
 * `events.onConfigPacket.subscribe`, `events.onDeviceStatus.subscribe`, `configure()` and `disconnect()`.
 *
 * Fails when the operator closes the browser picker (a `NotFoundError`) or when the transport does not open.
 */
async function openDevice(library, kind) {
  if (window.__pushConfigTestDevice) {
    return window.__pushConfigTestDevice;
  }
  // The transport opens the browser picker, so this call must come first in the click handler.
  const transport = kind === 'bluetooth'
    ? await library.bluetooth.TransportWebBluetooth.create()
    : await library.serial.TransportWebSerial.create();
  return new library.core.MeshDevice(transport);
}

/**
 * Return the security config of `device`, which holds `isManaged`.
 *
 * Sends want_config, then waits for the config packet with the security section.
 * Fails when the device ends its config without that section, or when want_config does not send.
 */
function readSecurity(device) {
  return new Promise((resolve, reject) => {
    const stopConfig = device.events.onConfigPacket.subscribe(config => {
      if (config.payloadVariant.case === 'security') {
        stop();
        resolve(config.payloadVariant.value);
      }
    });
    const stopStatus = device.events.onDeviceStatus.subscribe(status => {
      if (status === DEVICE_CONFIGURED) {
        stop();
        reject(new Error(NO_SECURITY));
      }
    });
    function stop() {
      stopConfig();
      stopStatus();
    }
    device.configure().catch(error => {
      stop();
      reject(error);
    });
  });
}

async function connect(library, kind) {
  let device;
  try {
    device = await openDevice(library, kind);
  } catch (error) {
    // The operator closed the picker, so the page stays in the not-connected state.
    if (error.name !== 'NotFoundError') {
      showError(error);
    }
    return;
  }
  showState('push-reading');
  try {
    const security = await readSecurity(device);
    if (security.isManaged) {
      // A managed device accepts admin messages only with an admin key, so the page sends nothing (FR-push-config-11).
      showState('push-managed');
      await close(device);
      return;
    }
    // TODO(#73): Read the full device config and show the diff with the profile.
    showState('push-connected');
  } catch (error) {
    // An open port makes the next Connect fail, so the page closes the connection before it shows the error.
    await close(device);
    showError(error);
  }
}

async function close(device) {
  try {
    await device.disconnect();
  } catch (error) {
    // The port is already closed when the device is unplugged, and the page keeps the state that it shows.
  }
}

function showError(error) {
  el('push-error').textContent = error && error.message ? error.message : String(error);
  showState('push-error');
}

function bind(library) {
  ['usb', 'bluetooth'].forEach(kind => {
    const button = el(kind === 'usb' ? 'push-connect-usb' : 'push-connect-bluetooth');
    button.addEventListener('click', async () => {
      // A second click while the picker is open would open a second connection.
      button.disabled = true;
      try {
        await connect(library, kind);
      } finally {
        button.disabled = false;
      }
    });
    button.disabled = false;
  });
}

// The import map resolves each name to a file in static/vendor/meshtastic/, so the modules come from this host.
function loadLibrary() {
  return Promise.all([
    import('@meshtastic/core'),
    import('@meshtastic/transport-web-serial'),
    import('@meshtastic/transport-web-bluetooth'),
  ]).then(([core, serial, bluetooth]) => ({ core, serial, bluetooth }));
}

function start() {
  const support = window.mtfwSerialSupport();
  // Web Bluetooth also needs a secure context, so the HTTPS message covers both controls.
  if (support === 'insecure') {
    el('push-insecure').hidden = false;
    return;
  }
  const usb = support === 'supported';
  const bluetooth = 'bluetooth' in window.navigator;
  if (!usb && !bluetooth) {
    el('push-unsupported').hidden = false;
    return;
  }
  el('push-connect').hidden = false;
  el('push-connect-usb').hidden = !usb;
  el('push-connect-bluetooth').hidden = !bluetooth;
  loadLibrary().then(bind, () => {
    el('push-connect').hidden = true;
    el('push-load-failed').hidden = false;
  });
}

start();

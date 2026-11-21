// The push config page (feature push-config): connect to a device, read its config, and show the diff with a profile.
// The library calls follow the vendored @meshtastic/core 2.6.7 build and its two transports (ADR 0008).

const el = id => document.getElementById(id);
// Each connect state replaces the one before it, so the operator sees one state at a time.
const CONNECT_STATES = ['push-reading', 'push-connected', 'push-managed', 'push-error'];
// @meshtastic/core 2.6.7 sends this status when the device answers the end of want_config (Types.DeviceStatusEnum).
const DEVICE_CONFIGURED = 7;
const NO_SECURITY = 'The device sent its config without a security section.';
// The build log mask uses the same text, so the operator sees one mask for each secret.
const MASK = '***';
const NOT_READ = '—';
const CHANNEL_SLOTS = 8;
const PSK_LENGTHS = [0, 1, 16, 32];
const ADMIN_KEY_LENGTHS = [32];

// The device stores a POSIX TZ string, and a profile can hold an IANA time zone (`mtfwbuilder/time_zones.py`).
export const tzTable = JSON.parse(el('push-tz-table').textContent);

/**
 * Return the bytes of a key in hex, `{ 0x.. }` or base64 form, or null for any other text.
 *
 * The forms and their order are the forms of `decode_key` in `mtfwbuilder/key_bytes.py`.
 */
function decodeKey(value) {
  const key = value.trim();
  if (/^(?:[0-9a-fA-F]{2})+$/.test(key)) {
    return Uint8Array.from(key.match(/../g), pair => parseInt(pair, 16));
  }
  const list = /^\{\s*(0x[0-9a-fA-F]{1,2}(?:\s*,\s*0x[0-9a-fA-F]{1,2})*)?\s*\}$/.exec(key);
  if (list) {
    return Uint8Array.from(list[1] ? list[1].split(',') : [], item => parseInt(item, 16));
  }
  // `atob` accepts whitespace and a missing pad, and the server rejects both.
  if (key.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(key)) {
    return null;
  }
  return Uint8Array.from(atob(key), char => char.charCodeAt(0));
}

// Each value type converts a profile value and a device value to one form, so the diff compares them directly.
// `profile` returns null for a value that the device cannot take.
const BOOL = { profile: raw => raw === 'true', show: String };
// The form asks whether TX is off, and the protobuf field says whether TX is on.
const TX_DISABLED = { profile: raw => raw !== 'true', show: String };
const UINT = { profile: raw => (/^[0-9]+$/.test(raw) ? Number(raw) : null), show: String };
const TEXT = { profile: raw => raw, show: String };
const SECRET = { profile: raw => raw, show: () => MASK };
// The generator writes the same POSIX TZ string into USERPREFS_TZ_STRING.
const TZ = { profile: raw => (Object.hasOwn(tzTable, raw) ? tzTable[raw] : raw), show: String };

function keyType(lengths, error) {
  return {
    profile: raw => {
      const key = decodeKey(raw);
      return key && lengths.includes(key.length) ? key : null;
    },
    show: () => MASK,
    error,
  };
}

const PSK = keyType(PSK_LENGTHS, 'The value is not hex, base64 or a { 0x.. } byte list of 0, 1, 16 or 32 bytes.');
const ADMIN_KEY = keyType(ADMIN_KEY_LENGTHS, 'The value is not hex, base64 or a { 0x.. } byte list of 32 bytes.');

/**
 * Return the value type of the `Protobuf.Config` enum `name`, for example `Config_LoRaConfig_RegionCode`.
 *
 * The profile holds the C name of the value, and the device sends its number. Both become the value name.
 */
function enumType(name) {
  const prefix = `meshtastic_${name}_`;
  return {
    profile: raw => (raw.startsWith(prefix) ? raw.slice(prefix.length) : raw),
    // A device with newer protobufs can send a number that core 2.6.7 does not name.
    device: (value, context) => context.protobuf.Config[name][value] ?? String(value),
    show: String,
  };
}

// The generator writes the fields of these sections only when the form switch is on.
const LORA = ['lora_enabled'];
const GPS = ['gps_enabled'];
const NETWORK = ['network_enabled'];
const WIFI = ['network_enabled', 'wifi_enabled'];
const MQTT = ['network_enabled', 'mqtt_enabled'];

/**
 * The mapping table: each profile form field, with the protobuf field of @meshtastic/core 2.6.7 that holds it.
 *
 * - `section` is the path of the section in the device read: `config.<case>`, `moduleConfig.<case>`,
 *   `channels.<index>` or `owner`.
 * - `field` is the path of the protobuf field in the section, with protobuf-es names.
 * - `when` names the form switches that must be "true" for the generator to write the field.
 * - `slot` is the channel slot, which counts only when `channels_to_write` is above it.
 *
 * A form field whose protobuf field core 2.6.7 does not have is absent, for example
 * `security_packet_signature_policy`.
 */
export const FIELD_MAP = [
  { form: 'device_role', section: 'config.device', field: 'role', type: enumType('Config_DeviceConfig_Role') },
  {
    form: 'device_rebroadcast_mode',
    section: 'config.device',
    field: 'rebroadcastMode',
    type: enumType('Config_DeviceConfig_RebroadcastMode'),
  },
  { form: 'node_info_broadcast_secs', section: 'config.device', field: 'nodeInfoBroadcastSecs', type: UINT },
  { form: 'tz_string', section: 'config.device', field: 'tzdef', type: TZ },
  { form: 'button_pin', section: 'config.device', field: 'buttonGpio', type: UINT, when: ['button_pin_enabled'] },
  {
    form: 'gps_mode',
    section: 'config.position',
    field: 'gpsMode',
    type: enumType('Config_PositionConfig_GpsMode'),
    when: GPS,
  },
  { form: 'gps_update_interval', section: 'config.position', field: 'gpsUpdateInterval', type: UINT, when: GPS },
  {
    form: 'position_broadcast_interval',
    section: 'config.position',
    field: 'positionBroadcastSecs',
    type: UINT,
    when: GPS,
  },
  { form: 'fixed_position', section: 'config.position', field: 'fixedPosition', type: BOOL, when: GPS },
  {
    form: 'smart_position_enabled',
    section: 'config.position',
    field: 'positionBroadcastSmartEnabled',
    type: BOOL,
    when: GPS,
  },
  {
    form: 'lora_region',
    section: 'config.lora',
    field: 'region',
    type: enumType('Config_LoRaConfig_RegionCode'),
    when: LORA,
  },
  {
    form: 'lora_modem_preset',
    section: 'config.lora',
    field: 'modemPreset',
    type: enumType('Config_LoRaConfig_ModemPreset'),
    when: LORA,
  },
  { form: 'lora_channel_num', section: 'config.lora', field: 'channelNum', type: UINT, when: LORA },
  { form: 'lora_ignore_mqtt', section: 'config.lora', field: 'ignoreMqtt', type: BOOL, when: LORA },
  { form: 'lora_tx_power', section: 'config.lora', field: 'txPower', type: UINT, when: LORA },
  { form: 'lora_tx_disabled', section: 'config.lora', field: 'txEnabled', type: TX_DISABLED, when: LORA },
  { form: 'lora_config_ok_to_mqtt', section: 'config.lora', field: 'configOkToMqtt', type: BOOL, when: LORA },
  { form: 'network_protocols', section: 'config.network', field: 'enabledProtocols', type: UINT, when: NETWORK },
  { form: 'network_ipv6_enabled', section: 'config.network', field: 'ipv6Enabled', type: BOOL, when: NETWORK },
  { form: 'wifi_enabled', section: 'config.network', field: 'wifiEnabled', type: BOOL, when: NETWORK },
  { form: 'wifi_ssid', section: 'config.network', field: 'wifiSsid', type: TEXT, when: WIFI },
  { form: 'wifi_psk', section: 'config.network', field: 'wifiPsk', type: SECRET, when: WIFI },
  { form: 'mqtt_enabled', section: 'moduleConfig.mqtt', field: 'enabled', type: BOOL, when: NETWORK },
  { form: 'mqtt_address', section: 'moduleConfig.mqtt', field: 'address', type: TEXT, when: MQTT },
  { form: 'mqtt_root_topic', section: 'moduleConfig.mqtt', field: 'root', type: TEXT, when: MQTT },
  { form: 'mqtt_username', section: 'moduleConfig.mqtt', field: 'username', type: TEXT, when: MQTT },
  { form: 'mqtt_password', section: 'moduleConfig.mqtt', field: 'password', type: SECRET, when: MQTT },
  { form: 'mqtt_encryption_enabled', section: 'moduleConfig.mqtt', field: 'encryptionEnabled', type: BOOL, when: MQTT },
  { form: 'mqtt_tls_enabled', section: 'moduleConfig.mqtt', field: 'tlsEnabled', type: BOOL, when: MQTT },
  { form: 'telemetry_device_interval', section: 'moduleConfig.telemetry', field: 'deviceUpdateInterval', type: UINT },
  {
    form: 'telemetry_environment_enabled',
    section: 'moduleConfig.telemetry',
    field: 'environmentMeasurementEnabled',
    type: BOOL,
  },
  {
    form: 'telemetry_environment_interval',
    section: 'moduleConfig.telemetry',
    field: 'environmentUpdateInterval',
    type: UINT,
  },
  {
    form: 'telemetry_environment_screen_enabled',
    section: 'moduleConfig.telemetry',
    field: 'environmentScreenEnabled',
    type: BOOL,
  },
  {
    form: 'telemetry_air_quality_enabled',
    section: 'moduleConfig.telemetry',
    field: 'airQualityEnabled',
    type: BOOL,
  },
  {
    form: 'telemetry_air_quality_interval',
    section: 'moduleConfig.telemetry',
    field: 'airQualityInterval',
    type: UINT,
  },
  { form: 'bluetooth_fixed_pin', section: 'config.bluetooth', field: 'fixedPin', type: UINT },
  { form: 'security_is_managed', section: 'config.security', field: 'isManaged', type: BOOL },
  ...[0, 1, 2].map(n => ({ form: `admin_key_${n}`, section: 'config.security', field: `adminKey.${n}`, type: ADMIN_KEY })),
  ...Array.from({ length: CHANNEL_SLOTS }, (_, slot) => [
    ['name', 'settings.name', TEXT],
    ['psk', 'settings.psk', PSK],
    ['precision', 'settings.moduleSettings.positionPrecision', UINT],
    ['uplink_enabled', 'settings.uplinkEnabled', BOOL],
    ['downlink_enabled', 'settings.downlinkEnabled', BOOL],
    ['muted', 'settings.moduleSettings.isClientMuted', BOOL],
  ].map(([name, field, type]) => ({ form: `channel_${slot}[${name}]`, section: `channels.${slot}`, field, type, slot })))
    .flat(),
  { form: 'owner_long_name', section: 'owner', field: 'longName', type: TEXT },
  { form: 'owner_short_name', section: 'owner', field: 'shortName', type: TEXT },
];

const SECTION_LABELS = {
  'config.device': 'Device',
  'config.position': 'Position',
  'config.lora': 'LoRa',
  'config.network': 'Network',
  'moduleConfig.mqtt': 'MQTT',
  'moduleConfig.telemetry': 'Telemetry',
  'config.bluetooth': 'Bluetooth',
  'config.security': 'Security',
  owner: 'Owner',
};

function valueAt(source, path) {
  return path.split('.').reduce((value, part) => (value === undefined || value === null ? undefined : value[part]),
    source);
}

function sameValue(a, b) {
  if (a instanceof Uint8Array && b instanceof Uint8Array) {
    return a.length === b.length && a.every((byte, index) => byte === b[index]);
  }
  return a === b;
}

// The diff shows the protobuf field name in the snake case of the proto file, as the Meshtastic apps do.
function fieldLabel(field) {
  return field.replace(/^settings\.(moduleSettings\.)?/, '')
    .replace(/\.([0-9]+)$/, '[$1]')
    .replace(/[A-Z]/g, letter => `_${letter.toLowerCase()}`);
}

function sectionLabel(section) {
  return section.startsWith('channels.') ? `Channel ${section.slice('channels.'.length)}` : SECTION_LABELS[section];
}

/** Return true when the generator writes the field of `row` for the profile config `config`. */
function applies(row, config) {
  if (row.slot !== undefined && row.slot >= Number(config.channels_to_write ?? '1')) {
    return false;
  }
  return (row.when || []).every(name => config[name] === 'true');
}

function diffRow(row, read, raw, context) {
  const wanted = row.type.profile(String(raw), context);
  if (wanted === null) {
    return null;
  }
  const found = valueAt(read, `${row.section}.${row.field}`);
  const current = found === undefined || !row.type.device ? found : row.type.device(found, context);
  if (sameValue(current, wanted)) {
    return null;
  }
  return {
    form: row.form,
    field: fieldLabel(row.field),
    device: current === undefined ? NOT_READ : row.type.show(current),
    profile: row.type.show(wanted),
  };
}

/**
 * Return the sections with a field where the device value differs from the profile value (FR-push-config-5).
 *
 * Each section is `{section, label, rows}`, in the order of the mapping table. Each row is `{form, field, device,
 * profile}`, with display text and with each secret as `***`.
 *
 * Parameters:
 * - `read`: the device read of `readDevice`.
 * - `config`: the flat form fields of the profile, with the secrets that the operator entered again.
 * - `context`: `{protobuf, tzTable}`, the `Protobuf` namespace of core and the time zone table.
 *
 * A form field that is empty, or that the generator does not write, gives no row.
 */
export function diffSections(read, config, context) {
  const sections = new Map();
  FIELD_MAP.forEach(row => {
    const raw = config[row.form];
    if (raw === undefined || raw === null || raw === '' || !applies(row, config)) {
      return;
    }
    const diff = diffRow(row, read, raw, context);
    if (!diff) {
      return;
    }
    if (!sections.has(row.section)) {
      sections.set(row.section, { section: row.section, label: sectionLabel(row.section), rows: [] });
    }
    sections.get(row.section).rows.push(diff);
  });
  return [...sections.values()];
}

/**
 * Return a connected MeshDevice for the transport `kind`, "usb" or "bluetooth".
 *
 * Test hook: when `window.__pushConfigTestDevice` is set before the page loads, the page uses that object
 * as the device and opens no transport. The object needs the MeshDevice members that the page calls:
 * `configure()`, `disconnect()`, and `subscribe` on the `events` members `onConfigPacket`, `onModuleConfigPacket`,
 * `onChannelPacket`, `onMyNodeInfo`, `onNodeInfoPacket` and `onDeviceStatus`.
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
 * Return the device read: `{config, moduleConfig, channels, owner}` (FR-push-config-4).
 *
 * `config` and `moduleConfig` hold each section by its protobuf case name, `channels` holds each channel by its index,
 * and `owner` is the `User` of the device node.
 * Sends want_config, then collects the packets until the device ends its config.
 * Fails when the config has no security section, or when want_config does not send.
 */
function readDevice(device) {
  return new Promise((resolve, reject) => {
    const read = { config: {}, moduleConfig: {}, channels: {}, owner: undefined };
    const users = new Map();
    let myNodeNum;
    const store = target => packet => {
      // Core 2.6.7 dispatches a packet of an unknown variant with no case, and the page cannot compare it.
      if (packet.payloadVariant.case) {
        target[packet.payloadVariant.case] = packet.payloadVariant.value;
      }
    };
    const events = device.events;
    const stops = [
      events.onConfigPacket.subscribe(store(read.config)),
      events.onModuleConfigPacket.subscribe(store(read.moduleConfig)),
      events.onChannelPacket.subscribe(channel => { read.channels[channel.index] = channel; }),
      events.onMyNodeInfo.subscribe(info => { myNodeNum = info.myNodeNum; }),
      // The device sends a node info for each node that it knows, and the owner is the user of its own node.
      events.onNodeInfoPacket.subscribe(node => { users.set(node.num, node.user); }),
      events.onDeviceStatus.subscribe(status => {
        if (status !== DEVICE_CONFIGURED) {
          return;
        }
        stop();
        read.owner = users.get(myNodeNum);
        if (read.config.security) {
          resolve(read);
        } else {
          reject(new Error(NO_SECURITY));
        }
      }),
    ];
    function stop() {
      stops.forEach(unsubscribe => unsubscribe());
    }
    device.configure().catch(error => {
      stop();
      reject(error);
    });
  });
}

// The secrets that the operator enters again live only in this map, so no request and no storage holds them.
const state = { read: null, profile: null, secrets: new Map(), context: null };

function showState(id) {
  CONNECT_STATES.forEach(name => { el(name).hidden = name !== id; });
}

function sectionElement(section, checked) {
  const box = document.createElement('section');
  box.className = 'mb-3';
  box.dataset.section = section.section;
  const check = document.createElement('div');
  check.className = 'form-check';
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.className = 'form-check-input';
  input.id = `push-section-${section.section}`;
  input.checked = checked;
  const label = document.createElement('label');
  label.className = 'form-check-label fw-bold';
  label.htmlFor = input.id;
  label.textContent = section.label;
  check.append(input, label);
  const table = document.createElement('table');
  table.className = 'table table-sm';
  const head = table.createTHead().insertRow();
  ['Field', 'On device', 'In profile'].forEach(text => {
    const cell = document.createElement('th');
    cell.textContent = text;
    head.append(cell);
  });
  const body = table.createTBody();
  section.rows.forEach(diff => {
    const row = body.insertRow();
    [diff.field, diff.device, diff.profile].forEach(text => { row.insertCell().textContent = text; });
  });
  box.append(check, table);
  return box;
}

/** Return the rows of the mapping table that the profile holds no value for and that the operator must enter. */
function reenterRows(profile) {
  return FIELD_MAP.filter(row => profile.must_reenter.includes(row.form) && applies(row, profile.config));
}

function renderDiff() {
  el('push-choose-profile').hidden = Boolean(state.profile);
  if (!state.read || !state.profile) {
    el('push-diff').hidden = true;
    el('push-no-changes').hidden = true;
    return;
  }
  const config = { ...state.profile.config, ...Object.fromEntries(state.secrets) };
  const sections = diffSections(state.read, config, state.context);
  const container = el('push-diff-sections');
  // A new diff keeps each section that the operator cleared, so a secret entry does not select it again.
  const cleared = new Set([...container.querySelectorAll('[data-section]')]
    .filter(box => !box.querySelector('input').checked).map(box => box.dataset.section));
  container.replaceChildren(...sections.map(section => sectionElement(section, !cleared.has(section.section))));
  const pending = reenterRows(state.profile).some(row => !state.secrets.has(row.form));
  el('push-diff').hidden = sections.length === 0;
  el('push-no-changes').hidden = sections.length > 0 || pending;
  // TODO(#74): Enable Push when a section is selected and the operator entered each secret.
  el('push-send').disabled = true;
}

function secretInput(row) {
  const group = document.createElement('div');
  group.className = 'mb-2';
  const input = document.createElement('input');
  input.type = 'password';
  input.className = 'form-control';
  input.id = `push-reenter-${row.form}`;
  input.dataset.form = row.form;
  // A browser password manager stores what it saves, and a secret must stay out of browser storage.
  input.autocomplete = 'off';
  const label = document.createElement('label');
  label.className = 'form-label';
  label.htmlFor = input.id;
  label.textContent = `${sectionLabel(row.section)}: ${fieldLabel(row.field)}`;
  const error = document.createElement('div');
  error.className = 'invalid-feedback';
  input.addEventListener('input', () => {
    const valid = input.value !== '' && row.type.profile(input.value, state.context) !== null;
    if (valid) {
      state.secrets.set(row.form, input.value);
    } else {
      state.secrets.delete(row.form);
    }
    const invalid = input.value !== '' && !valid;
    input.classList.toggle('is-invalid', invalid);
    error.textContent = invalid ? row.type.error : '';
    renderDiff();
  });
  group.append(label, input, error);
  return group;
}

function renderReenter() {
  const rows = state.profile ? reenterRows(state.profile) : [];
  el('push-reenter-fields').replaceChildren(...rows.map(secretInput));
  el('push-reenter').hidden = rows.length === 0;
}

async function chooseProfile(id) {
  state.profile = null;
  state.secrets.clear();
  el('push-profile-failed').hidden = true;
  if (id) {
    const response = await fetch(`/api/v1/profiles/${encodeURIComponent(id)}`);
    // The operator can choose another profile while this one loads, and the page shows only the last choice.
    if (el('push-profile').value !== id) {
      return;
    }
    if (response.ok) {
      state.profile = await response.json();
    } else {
      el('push-profile-failed').hidden = false;
    }
  }
  renderReenter();
  renderDiff();
}

async function loadProfiles() {
  const response = await fetch('/api/v1/profiles');
  if (!response.ok) {
    el('push-profile-failed').hidden = false;
    return;
  }
  const select = el('push-profile');
  (await response.json()).forEach(profile => select.add(new Option(profile.name, String(profile.id))));
  select.addEventListener('change', () => chooseProfile(select.value));
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
  state.read = null;
  showState('push-reading');
  try {
    const read = await readDevice(device);
    if (read.config.security.isManaged) {
      // A managed device accepts admin messages only with an admin key, so the page sends nothing (FR-push-config-11).
      showState('push-managed');
      await close(device);
      return;
    }
    state.read = read;
    showState('push-connected');
    renderDiff();
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
  state.context = { protobuf: library.core.Protobuf, tzTable };
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
  loadProfiles().catch(() => { el('push-profile-failed').hidden = false; });
  loadLibrary().then(bind, () => {
    el('push-connect').hidden = true;
    el('push-load-failed').hidden = false;
  });
}

start();

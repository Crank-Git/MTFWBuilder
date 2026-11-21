// The push config page (feature push-config): connect to a device, read its config, show the diff with a profile,
// and push the selected sections.
// The library calls follow the vendored @meshtastic/core 2.6.7 build and its two transports (ADR 0008).

const el = id => document.getElementById(id);
// Each connect state replaces the one before it, so the operator sees one state at a time.
const CONNECT_STATES = ['push-reading', 'push-connected', 'push-managed', 'push-error'];
// @meshtastic/core 2.6.7 sends this status when the device answers the end of want_config (Types.DeviceStatusEnum).
const DEVICE_CONFIGURED = 7;
// The transports send this status when the port closes or the Bluetooth link drops (Types.DeviceStatusEnum).
const DEVICE_DISCONNECTED = 2;
// A USB serial bridge keeps the port open while the device restarts, so no drop tells the page that the restart began.
const RESTART_WAIT_MS = 15000;
const LOG_OFF = 7;
const RETRY_MS = 2000;
const RECONNECT_MS = 120000;
// A device that is still starting can miss want_config, and the read then never ends.
const READ_MS = 10000;
const NO_SECURITY = 'The device sent its config without a security section.';
const NO_CONFIG = 'The device did not send its config.';
const READ_DROPPED = 'The connection dropped before the device sent its config.';
// The build log mask uses the same text, so the operator sees one mask for each secret.
const MASK = '***';
const NOT_READ = '—';
// The profile list of admin keys is shorter than the device list, so the push removes the device key.
const NO_KEY = '(none)';
const CHANNEL_SLOTS = 8;
const PSK_LENGTHS = [0, 1, 16, 32];
const ADMIN_KEY_LENGTHS = [32];

// The device stores a POSIX TZ string, and a profile can hold an IANA time zone (`mtfwbuilder/time_zones.py`).
export const tzTable = JSON.parse(el('push-tz-table').textContent);
// The template renders the flag only when the `push_test_hooks` setting is on, and only the tests turn it on.
const TEST_HOOKS = el('push-test-hooks') !== null;

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

// `setFixedPosition` of core 2.6.7 sends `Math.floor(degrees / 1e-7)`, so the page compares that integer.
const DEGREES = {
  profile: raw => (raw.trim() !== '' && Number.isFinite(Number(raw)) ? Math.floor(Number(raw) / 1e-7) : null),
  show: value => (value / 1e7).toFixed(7),
};

/**
 * Return the value type of the `Protobuf.Config` enum `name`, for example `Config_LoRaConfig_RegionCode`.
 *
 * The profile holds the C name of the value, and the device sends its number. Both become the value name.
 */
function enumType(name) {
  const prefix = `meshtastic_${name}_`;
  return {
    // A value name that core 2.6.7 does not have gives no number that the page can send.
    profile: (raw, context) => {
      const value = raw.startsWith(prefix) ? raw.slice(prefix.length) : raw;
      return typeof context.protobuf.Config[name][value] === 'number' ? value : null;
    },
    wire: (value, context) => context.protobuf.Config[name][value],
    // A device with newer protobufs can send a number that core 2.6.7 does not name.
    device: (value, context) => context.protobuf.Config[name][value] ?? String(value),
    show: String,
  };
}

// USERPREFS_FIXED_BLUETOOTH sets the FIXED_PIN pairing mode with the PIN (`NodeDB::installDefaultConfig`).
const FIXED_PIN_MODE = {
  ...enumType('Config_BluetoothConfig_PairingMode'),
  profile: raw => (UINT.profile(raw) === null ? null : 'FIXED_PIN'),
};

// The generator writes the fields of these sections only when the form switch is on.
const LORA = ['lora_enabled'];
const GPS = ['gps_enabled'];
const NETWORK = ['network_enabled'];
const WIFI = ['network_enabled', 'wifi_enabled'];
const MQTT = ['network_enabled', 'mqtt_enabled'];
const FIXED = ['gps_enabled', 'fixed_position'];

/**
 * The mapping table: each profile form field, with the protobuf field of @meshtastic/core 2.6.7 that holds it.
 *
 * - `section` is the path of the section in the device read: `config.<case>`, `moduleConfig.<case>`,
 *   `channels.<index>`, `owner` or `position`.
 * - `field` is the path of the protobuf field in the section, with protobuf-es names.
 * - `when` names the form switches that must be "true" for the generator to write the field.
 * - `slot` is the channel slot, which counts only when `channels_to_write` is above it.
 *
 * A form field that push config does not send is in `NOT_PUSHED`.
 * `fixed_lat` and `fixed_lon` come after the position config: `set_fixed_position` sets `fixed_position`
 * (`AdminModule.cpp`), and a later position config replaces it.
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
  { form: 'fixed_lat', section: 'position', field: 'latitudeI', type: DEGREES, when: FIXED },
  { form: 'fixed_lon', section: 'position', field: 'longitudeI', type: DEGREES, when: FIXED },
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
  { form: 'bluetooth_fixed_pin', section: 'config.bluetooth', field: 'mode', type: FIXED_PIN_MODE },
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

const NO_FIELD = 'Core 2.6.7 has no field for this value.';
const BUILD_ONLY = 'The firmware reads this value only from a build.';
const NOT_MAPPED = 'Push config does not send this value.';
const MESH_BEACON_FIELDS = [
  'listen_enabled', 'broadcast_enabled', 'message', 'interval_secs', 'offer_channel_name', 'offer_channel_psk',
  'offer_frequency_slot', 'offer_preset', 'offer_region',
  ...[0, 1, 2, 3].flatMap(n => ['channel_index', 'frequency_slot', 'preset', 'region'].map(name => `target_${n}_${name}`)),
];

/**
 * The profile form fields that push config does not send, each with the reason that the page shows (ruling 7 on #70).
 *
 * `when` has the meaning of the mapping table. A form field that only switches other fields on is in neither table.
 */
export const NOT_PUSHED = [
  // A managed device ignores each local admin message (`AdminModule::handleReceivedProtobuf`), so push config would
  // lose its own access.
  { form: 'security_is_managed', reason: 'Set managed mode with a build. Push config does not set it.' },
  { form: 'security_packet_signature_policy', reason: NO_FIELD },
  { form: 'telemetry_air_quality_screen_enabled', reason: NO_FIELD },
  ...MESH_BEACON_FIELDS.map(name => ({ form: `mesh_beacon_${name}`, reason: NO_FIELD })),
  { form: 'fixed_alt', reason: 'setFixedPosition of core 2.6.7 takes no altitude.', when: FIXED },
  ...['node_info_reply_suppress_secs', 'event_mode', 'event_mode_hop_limit', 'event_block_position', 'oem_text',
    'oem_font_size', 'oem_image_width', 'oem_image_height', 'oem_image_data']
    .map(form => ({ form, reason: BUILD_ONLY })),
  ...['canned_messages', 'ringtone_rtttl', 'ringtone_nag_secs'].map(form => ({ form, reason: NOT_MAPPED })),
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
  position: 'Fixed position',
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

function filled(raw) {
  return raw !== undefined && raw !== null && raw !== '';
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
    path: row.field,
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
  const profile = withAdminKeys(config);
  const sections = new Map();
  const add = (section, diff) => {
    if (!sections.has(section)) {
      sections.set(section, { section, label: sectionLabel(section), rows: [] });
    }
    sections.get(section).rows.push(diff);
  };
  FIELD_MAP.forEach(row => {
    const raw = profile[row.form];
    const diff = filled(raw) && applies(row, profile) ? diffRow(row, read, raw, context) : null;
    if (diff) {
      add(row.section, diff);
    }
  });
  removedAdminKeys(read, profile).forEach(slot => add('config.security', {
    form: `admin_key_${slot}`,
    path: `adminKey.${slot}`,
    field: `admin_key[${slot}]`,
    device: MASK,
    profile: NO_KEY,
  }));
  return [...sections.values()];
}

/**
 * Return `config` with its valid admin keys moved to the first slots, in slot order (ruling 4 on #70).
 *
 * The push replaces the whole admin key list, so a profile key in slot 1 alone becomes the only device key.
 */
function withAdminKeys(config) {
  const forms = [0, 1, 2].map(slot => `admin_key_${slot}`);
  const keys = forms.map(form => config[form]).filter(raw => filled(raw) && ADMIN_KEY.profile(String(raw)) !== null);
  const result = { ...config };
  forms.forEach((form, slot) => {
    if (slot < keys.length) {
      result[form] = keys[slot];
    } else {
      delete result[form];
    }
  });
  return result;
}

function adminKeyCount(config) {
  return [0, 1, 2].filter(slot => filled(config[`admin_key_${slot}`])).length;
}

/** Return the slots of the device admin keys that a push of `config` removes. `config` comes from `withAdminKeys`. */
function removedAdminKeys(read, config) {
  const count = adminKeyCount(config);
  const found = valueAt(read, 'config.security.adminKey') ?? [];
  return count === 0 ? [] : Object.keys(found).map(Number).filter(slot => slot >= count && found[slot].length > 0);
}

/** Return the `{form, reason}` rows of `NOT_PUSHED` that the profile config `config` holds a value for. */
export function notPushed(config) {
  return NOT_PUSHED.filter(row => filled(config[row.form]) && applies(row, config));
}

// A protobuf-es message keeps a nested plain object as it is, so the page sends plain objects only.
// `create` in core then builds each message again from its fields.
function plain(value) {
  if (value === null || typeof value !== 'object' || value instanceof Uint8Array) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(plain);
  }
  return Object.fromEntries(Object.entries(value).filter(([key]) => key !== '$typeName')
    .map(([key, item]) => [key, plain(item)]));
}

function setValue(target, path, value) {
  const parts = path.split('.');
  let node = target;
  parts.slice(0, -1).forEach((part, index) => {
    node[part] ??= /^[0-9]+$/.test(parts[index + 1]) ? [] : {};
    node = node[part];
  });
  node[parts[parts.length - 1]] = value;
}

// The firmware gives a new channel slot these settings before it reads the userPrefs keys (`Channels::initDefaultChannel`).
function newChannelSettings() {
  return { psk: Uint8Array.of(1), moduleSettings: { positionPrecision: 13 } };
}

function channelMessage(read, slot, context) {
  const role = context.protobuf.Channel.Channel_Role;
  const found = read.channels[slot];
  const channel = found && found.role !== role.DISABLED ? plain(found) : { settings: newChannelSettings() };
  // The firmware gives slot 0 the PRIMARY role and each other slot that the profile counts the SECONDARY role.
  return { ...channel, index: slot, role: slot === 0 ? role.PRIMARY : role.SECONDARY };
}

/**
 * Return the admin call for one section of the diff: `{method, args}`, where `method` is the MeshDevice method.
 *
 * The device replaces the whole section, so the message holds the device values with the profile values on top.
 *
 * Parameters:
 * - `read`: the device read of `readDevice`.
 * - `config`: the flat form fields of the profile, with the secrets that the operator entered again.
 * - `section`: the section path of the mapping table, for example `config.lora` or `channels.1`.
 * - `context`: `{protobuf, tzTable}`, as for `diffSections`.
 */
export function sectionMessage(read, config, section, context) {
  const profile = withAdminKeys(config);
  const [root, key] = section.split('.');
  let value;
  if (root === 'channels') {
    value = channelMessage(read, Number(key), context);
  } else {
    value = plain(key === undefined ? read[root] : read[root][key]) ?? {};
  }
  FIELD_MAP.filter(row => row.section === section && filled(profile[row.form]) && applies(row, profile))
    .forEach(row => {
      const wanted = row.type.profile(String(profile[row.form]), context);
      if (wanted !== null) {
        setValue(value, row.field, row.type.wire ? row.type.wire(wanted, context) : wanted);
      }
    });
  if (section === 'config.security' && adminKeyCount(profile) > 0) {
    value.adminKey = value.adminKey.slice(0, adminKeyCount(profile));
  }
  if (root === 'channels') {
    return { method: 'setChannel', args: [value] };
  }
  if (root === 'owner') {
    return { method: 'setOwner', args: [value] };
  }
  if (root === 'position') {
    // Core floors `degrees / 1e-7`, so the half step gives back the same integer.
    const degrees = [value.latitudeI ?? 0, value.longitudeI ?? 0].map(integer => (integer + 0.5) / 1e7);
    return { method: 'setFixedPosition', args: degrees };
  }
  const method = root === 'config' ? 'setConfig' : 'setModuleConfig';
  return { method, args: [{ payloadVariant: { case: key, value } }] };
}

/**
 * Return a MeshDevice of `core` on `transport`, with its logger off.
 *
 * The esm.sh build of core 2.6.7 replaces `util.types.isNativeError` with a function that throws. The tslog logger
 * of MeshDevice calls it for each log line, so `configure()` and each set call throw. A level above fatal (6) ends
 * each log call before that check.
 */
export function createMeshDevice(core, transport) {
  const device = new core.MeshDevice(transport);
  device.log.settings.minLevel = LOG_OFF;
  return device;
}

/**
 * Return a connected MeshDevice for the transport `kind`, "usb" or "bluetooth".
 *
 * Test hook: when the template sets the test flag and `window.__pushConfigTestDevice` is set before the page loads,
 * the page uses that object as the device and opens no transport. The object needs the MeshDevice members that the page calls:
 * `configure()`, `disconnect()`, and `subscribe` on the `events` members `onConfigPacket`, `onModuleConfigPacket`,
 * `onChannelPacket`, `onMyNodeInfo`, `onNodeInfoPacket` and `onDeviceStatus`. Push also calls `beginEditSettings()`,
 * `setConfig`, `setModuleConfig`, `setChannel`, `setOwner`, `setFixedPosition` and `commitEditSettings()`. Each of them returns a promise
 * that resolves when the device acknowledges the message, and that rejects with `{id, error}` when the device rejects
 * it. After a restart, the page uses the same object again.
 *
 * Fails when the operator closes the browser picker (a `NotFoundError`) or when the transport does not open.
 */
async function openDevice(library, kind) {
  if (TEST_HOOKS && window.__pushConfigTestDevice) {
    return window.__pushConfigTestDevice;
  }
  // The transport opens the browser picker, so this call must come first in the click handler.
  const transport = kind === 'bluetooth'
    ? await library.bluetooth.TransportWebBluetooth.create()
    : await library.serial.TransportWebSerial.create();
  return createMeshDevice(library.core, transport);
}

/**
 * Return the device read: `{config, moduleConfig, channels, owner, position}` (FR-push-config-4).
 *
 * `config` and `moduleConfig` hold each section by its protobuf case name, `channels` holds each channel by its index,
 * and `owner` and `position` are the `User` and the `Position` of the device node.
 * Sends want_config, then collects the packets until the device ends its config.
 * Fails in these cases:
 * - The config has no security section.
 * - want_config does not send.
 * - The device does not end its config in `READ_MS`.
 * - The connection drops before the end of the config.
 */
function readDevice(device) {
  return new Promise((resolve, reject) => {
    const read = { config: {}, moduleConfig: {}, channels: {}, owner: undefined, position: undefined };
    const nodes = new Map();
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
      events.onNodeInfoPacket.subscribe(node => { nodes.set(node.num, node); }),
      events.onDeviceStatus.subscribe(status => {
        if (status === DEVICE_DISCONNECTED) {
          fail(new Error(READ_DROPPED));
        }
        if (status !== DEVICE_CONFIGURED) {
          return;
        }
        stop();
        read.owner = nodes.get(myNodeNum)?.user;
        read.position = nodes.get(myNodeNum)?.position;
        if (read.config.security) {
          resolve(read);
        } else {
          reject(new Error(NO_SECURITY));
        }
      }),
    ];
    const timer = setTimeout(() => fail(new Error(NO_CONFIG)), READ_MS);
    function stop() {
      clearTimeout(timer);
      stops.forEach(unsubscribe => unsubscribe());
    }
    function fail(error) {
      stop();
      reject(error);
    }
    device.configure().catch(fail);
  });
}

// The secrets that the operator enters again live only in this map, so no request and no storage holds them.
const state = { read: null, profile: null, secrets: new Map(), context: null, device: null, kind: null, pushing: false };

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
  input.addEventListener('change', updatePush);
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

function renderNotPushed(config) {
  const rows = config ? notPushed(config) : [];
  el('push-not-pushed-list').replaceChildren(...rows.map(row => {
    const item = document.createElement('li');
    item.dataset.notPushed = row.form;
    item.textContent = `${row.form}: ${row.reason}`;
    return item;
  }));
  el('push-not-pushed').hidden = rows.length === 0;
}

function renderDiff() {
  el('push-choose-profile').hidden = Boolean(state.profile);
  if (!state.read || !state.profile) {
    el('push-diff').hidden = true;
    el('push-no-changes').hidden = true;
    renderNotPushed(null);
    updatePush();
    return;
  }
  const config = { ...state.profile.config, ...Object.fromEntries(state.secrets) };
  renderNotPushed(config);
  const sections = diffSections(state.read, config, state.context);
  const container = el('push-diff-sections');
  // A new diff keeps each section that the operator cleared, so a secret entry does not select it again.
  const cleared = new Set([...container.querySelectorAll('[data-section]')]
    .filter(box => !box.querySelector('input').checked).map(box => box.dataset.section));
  container.replaceChildren(...sections.map(section => sectionElement(section, !cleared.has(section.section))));
  el('push-diff').hidden = sections.length === 0;
  el('push-no-changes').hidden = sections.length > 0 || secretsPending();
  updatePush();
}

function secretsPending() {
  return reenterRows(state.profile).some(row => !state.secrets.has(row.form));
}

function selectedSections() {
  return [...el('push-diff-sections').querySelectorAll('[data-section]')]
    .filter(box => box.querySelector('input').checked).map(box => box.dataset.section);
}

function updatePush() {
  const ready = state.device && state.read && state.profile && !secretsPending() && selectedSections().length > 0;
  el('push-send').disabled = state.pushing || !ready;
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
  el('push-profile-warning').hidden = true;
  if (id) {
    const response = await fetch(`/api/v1/profiles/${encodeURIComponent(id)}`);
    // The operator can choose another profile while this one loads, and the page shows only the last choice.
    if (el('push-profile').value !== id) {
      return;
    }
    if (response.ok) {
      state.profile = await response.json();
      // The server sets `warning` when the page gets no stored secrets, so the operator knows why Push asks for them.
      el('push-profile-warning').textContent = state.profile.warning ?? '';
      el('push-profile-warning').hidden = !state.profile.warning;
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
  state.device = null;
  hidePushNotices();
  showState('push-reading');
  try {
    await useDevice(device, kind, await readDevice(device));
  } catch (error) {
    // An open port makes the next Connect fail, so the page closes the connection before it shows the error.
    await close(device);
    showError(error);
  }
}

/** Show the diff of the device read `read`, or the managed message for a managed device. Connect and reconnect. */
async function useDevice(device, kind, read) {
  if (read.config.security.isManaged) {
    // A managed device accepts admin messages only with an admin key, so the page sends nothing (FR-push-config-11).
    showState('push-managed');
    await close(device);
    return;
  }
  Object.assign(state, { device, kind, read });
  showState('push-connected');
  renderDiff();
}

function hidePushNotices() {
  ['push-results', 'push-restart', 'push-dropped', 'push-unknown', 'push-reconnect-failed']
    .forEach(id => { el(id).hidden = true; });
}

function setConnectDisabled(disabled) {
  ['push-connect-usb', 'push-connect-bluetooth'].forEach(id => { el(id).disabled = disabled; });
}

function delay(ms) {
  return new Promise(resolve => { setTimeout(resolve, ms); });
}

// The library rejects with `{id, error}` for a routing error of the device, and with an Error for a local failure.
function deviceError(error) {
  if (error && typeof error.error === 'number') {
    return state.context.protobuf.Mesh.Routing_Error[error.error] ?? String(error.error);
  }
  return error && error.message ? error.message : String(error);
}

function showResult(section, text) {
  const item = document.createElement('li');
  item.dataset.result = section;
  item.textContent = `${sectionLabel(section)}: ${text}`;
  el('push-results').append(item);
}

/**
 * Send the selected sections in one transaction, and return "committed", "dropped", "commit-dropped" or
 * "commit-failed".
 *
 * The page sends begin_edit_settings, one set message for each section, then commit_edit_settings (FR-push-config-7).
 * Core 2.6.7 sends begin_edit_settings only inside `setConfig`, so the page sends it itself (ADR 0008).
 * A rejected set message gives a failed result, and the page sends the next one (FR-push-config-8).
 * A drop before the commit stops the transaction, so the device keeps its old config.
 *
 * Parameters:
 * - `device`: the connected MeshDevice.
 * - `messages`: the `{section, method, args}` admin calls, in the order of the mapping table.
 * - `drop`: a promise that resolves when the connection drops.
 */
async function sendTransaction(device, messages, drop) {
  const dropped = Symbol('dropped');
  // A lost port leaves the promise of the library open until its 60-second timeout, so the drop ends the wait.
  const send = call => Promise.race([call().then(() => null, error => error), drop.then(() => dropped)]);
  if (await send(() => device.beginEditSettings()) === dropped) {
    return 'dropped';
  }
  for (const message of messages) {
    // The page waits for each answer before the next message, so the results show the progress.
    const error = await send(() => device[message.method](...message.args));
    if (error === dropped) {
      return 'dropped';
    }
    showResult(message.section, error === null ? 'applied' : `failed, ${deviceError(error)}`);
  }
  const error = await send(() => device.commitEditSettings());
  if (error === dropped) {
    return 'commit-dropped';
  }
  if (error !== null) {
    showError(new Error(`The device did not save the config: ${deviceError(error)}`));
    return 'commit-failed';
  }
  return 'committed';
}

async function push(library) {
  const { device, kind } = state;
  const config = { ...state.profile.config, ...Object.fromEntries(state.secrets) };
  const messages = selectedSections().map(section => ({
    section,
    ...sectionMessage(state.read, config, section, state.context),
  }));
  state.pushing = true;
  updatePush();
  setConnectDisabled(true);
  hidePushNotices();
  el('push-results').replaceChildren();
  el('push-results').hidden = false;
  let onDrop;
  const drop = new Promise(resolve => { onDrop = resolve; });
  const stop = device.events.onDeviceStatus.subscribe(status => {
    if (status === DEVICE_DISCONNECTED) {
      onDrop();
    }
  });
  try {
    const outcome = await sendTransaction(device, messages, drop);
    state.device = null;
    if (outcome === 'commit-failed') {
      await close(device);
      return;
    }
    if (outcome === 'dropped') {
      el('push-results').hidden = true;
      el('push-dropped').hidden = false;
      await close(device);
      return;
    }
    // A device on Bluetooth turns its radio off at the commit, so a drop there is the restart.
    // A USB port stays open through the restart, so a drop there can come before or after the device saves.
    if (outcome === 'commit-dropped' && kind !== 'bluetooth') {
      el('push-unknown').hidden = false;
      await close(device);
      return;
    }
    el('push-restart').hidden = false;
    await Promise.race([drop, delay(RESTART_WAIT_MS)]);
    await reconnect(library, kind, device);
  } finally {
    stop();
    state.pushing = false;
    updatePush();
    setConnectDisabled(false);
  }
}

/**
 * Return a MeshDevice on the port or the Bluetooth device of `old`, after the restart.
 *
 * A USB device can return as a new SerialPort. Web Serial then fires `connect` with it, and `ports.latest` holds it.
 */
async function reopenDevice(library, kind, old, ports) {
  if (TEST_HOOKS && window.__pushConfigTestDevice) {
    return window.__pushConfigTestDevice;
  }
  const transport = kind === 'bluetooth'
    ? await library.bluetooth.TransportWebBluetooth.createFromDevice(old.transport.gattServer.device)
    : await library.serial.TransportWebSerial.createFromPort(ports.latest ?? old.transport.connection);
  return createMeshDevice(library.core, transport);
}

/** Connect to the device again after the restart, and show the new diff (FR-push-config-9). */
async function reconnect(library, kind, old) {
  await close(old);
  const ports = { latest: null };
  const onPort = event => { ports.latest = event.target; };
  const serial = window.navigator.serial;
  serial?.addEventListener('connect', onPort);
  try {
    const deadline = Date.now() + RECONNECT_MS;
    while (Date.now() < deadline) {
      let device = null;
      try {
        device = await reopenDevice(library, kind, old, ports);
        await useDevice(device, kind, await readDevice(device));
        return;
      } catch (error) {
        // The device is still starting, so the page tries again.
        if (device) {
          await close(device);
        }
        await delay(RETRY_MS);
      }
    }
    el('push-reconnect-failed').hidden = false;
  } finally {
    serial?.removeEventListener('connect', onPort);
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
  el('push-send').addEventListener('click', () => push(library).catch(showError));
  ['usb', 'bluetooth'].forEach(kind => {
    const button = el(kind === 'usb' ? 'push-connect-usb' : 'push-connect-bluetooth');
    button.addEventListener('click', async () => {
      // A second click while the picker is open would open a second connection.
      button.disabled = true;
      try {
        await connect(library, kind);
      } finally {
        button.disabled = state.pushing;
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

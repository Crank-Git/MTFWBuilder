// The Import and Export panels of the build page (feature channel-share, FR-channel-share-7 and -8).
// The page keeps an import in a variable only, because the import holds PSKs (FR-frontend-htmx-6).

const NO_FREE_INDEX_MESSAGE = 'No free channel index.';
const MASKED_PSK = '••••••••';
// The form value of a PSK of 0 bytes. A blank PSK field gives default key 1, so an empty PSK needs its own value.
const NO_ENCRYPTION_PSK = '{ }';
const UNREAD_REASON = 'this firmware version does not read it';

// The decoded import that the preview shows. Apply reads it, and Cancel or a new import replaces it.
let pendingImport = null;

/** Return the message of a failed response: the FastAPI `detail` text, or each field error message. */
async function errorMessage(response) {
    const fallback = `The server returned HTTP ${response.status}.`;
    let body;
    try {
        body = await response.json();
    } catch (e) {
        if (!(e instanceof SyntaxError)) {
            throw e;
        }
        return fallback;
    }
    const detail = body && body.detail;
    if (typeof detail === 'string') {
        return detail;
    }
    if (Array.isArray(detail) && detail.length) {
        return detail.map(item => item.msg).join(' ');
    }
    return fallback;
}

/** Return the number of channel slots that the config writes, from Channel 1. */
function writtenChannels() {
    return Number(document.getElementById('channelsToWrite').value);
}

/** Return the number of channel slots that the firmware version reads. A slot that it does not read has a disabled count. */
function readSlots() {
    const counts = Array.from(document.getElementById('channelsToWrite').options)
        .filter(option => !option.disabled)
        .map(option => Number(option.value));
    return Math.max(0, ...counts);
}

/**
 * Return the channels of a decoded URL with the slot that each one fills, and the "Not applied" lines.
 *
 * Upstream `Node.setURL` adds the channels of an `add=true` URL to the disabled slots, never to slot 0. It skips a
 * channel without a name or with a name that is in use. The function returns null when the free slots are too few.
 */
function urlPlan(result) {
    if (!result.add) {
        return {channels: result.channels, notApplied: []};
    }
    const names = new Set(Array.from({length: writtenChannels()}, (_, slot) => {
        return document.querySelector(`[name="channel_${slot}[name]"]`).value;
    }));
    const channels = [];
    const notApplied = [];
    let slot = Math.max(writtenChannels(), 1);
    for (const channel of result.channels) {
        if (!channel.name) {
            notApplied.push(`Channel ${channel.index} of the URL: the channel has no name`);
        } else if (names.has(channel.name)) {
            notApplied.push(`Channel "${channel.name}": the config has a channel with this name`);
        } else if (slot >= readSlots()) {
            return null;
        } else {
            channels.push({...channel, index: slot});
            names.add(channel.name);
            slot += 1;
        }
    }
    return {channels, notApplied};
}

/**
 * Return the form fields of the planned channels and the LoRa settings of a decoded channel URL.
 *
 * Upstream `Node.setURL` sets the LoRa settings for both URL forms. A field that the URL leaves out keeps its value.
 */
function channelUrlFields(plan, lora) {
    const share = document.getElementById('channelShare').dataset;
    // An `add=true` URL whose channels are all skipped keeps the channel count of the config.
    const count = plan.channels.length ? Math.max(...plan.channels.map(channel => channel.index + 1)) : writtenChannels();
    const fields = {channels_to_write: String(count)};
    plan.channels.forEach(channel => {
        const prefix = `channel_${channel.index}`;
        fields[`${prefix}[name]`] = channel.name;
        fields[`${prefix}[psk]`] = channel.psk || NO_ENCRYPTION_PSK;
        // The export writes the firmware default 13 for an empty precision, so the import fills 0 too.
        fields[`${prefix}[precision]`] = String(channel.position_precision);
        fields[`${prefix}[uplink_enabled]`] = String(channel.uplink_enabled);
        fields[`${prefix}[downlink_enabled]`] = String(channel.downlink_enabled);
    });
    fields.lora_enabled = 'true';
    // The form has no `UNSET` region, and an empty region field keeps the firmware default `UNSET`.
    if (lora.region) {
        fields.lora_region = lora.region === 'UNSET' ? '' : share.regionPrefix + lora.region;
    }
    if (lora.modem_preset) {
        fields.lora_modem_preset = share.modemPresetPrefix + lora.modem_preset;
    }
    fields.lora_channel_num = String(lora.channel_num);
    fields.lora_tx_power = String(lora.tx_power);
    fields.lora_tx_disabled = String(!lora.tx_enabled);
    fields.lora_ignore_mqtt = String(lora.ignore_mqtt);
    fields.lora_config_ok_to_mqtt = String(lora.config_ok_to_mqtt);
    return fields;
}

/** Return the preview rows of the channels in the form fields of a CLI YAML import. */
function yamlChannels(config) {
    const count = Number(config.channels_to_write || 0);
    return Array.from({length: count}, (_, index) => ({
        index,
        name: config[`channel_${index}[name]`] || '',
        psk: config[`channel_${index}[psk]`] || '',
        role: index === 0 ? 'PRIMARY' : 'SECONDARY',
    }));
}

/** Return the text of a PSK that the preview can show without the Show control, or null for a secret key. */
function plainPskText(psk, defaultKey, role) {
    if (!psk || /^\{\s*\}$/.test(psk)) {
        // Upstream `Channels::getKey` gives a secondary channel with an empty PSK the primary channel key.
        return role === 'SECONDARY' ? 'Uses the primary channel key' : 'No encryption';
    }
    if (defaultKey !== undefined && defaultKey !== null) {
        return `Default key ${defaultKey}`;
    }
    // A PSK of 1 byte is a default-key index, and its base64 text has 4 characters.
    if (psk.length === 4 && psk.endsWith('==')) {
        return `Default key ${atob(psk).charCodeAt(0)}`;
    }
    return null;
}

/** Return the table cell of a PSK: masked, with a Show control that switches the PSK text on and off. */
function pskCell(psk, defaultKey, role) {
    const cell = document.createElement('td');
    const plain = plainPskText(psk, defaultKey, role);
    if (plain !== null) {
        cell.textContent = plain;
        return cell;
    }
    const text = document.createElement('code');
    text.className = 'me-2 text-break';
    text.textContent = MASKED_PSK;
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'btn btn-sm btn-outline-secondary';
    toggle.textContent = 'Show';
    toggle.addEventListener('click', () => {
        const shown = toggle.textContent === 'Hide';
        text.textContent = shown ? MASKED_PSK : psk;
        toggle.textContent = shown ? 'Show' : 'Hide';
    });
    cell.append(text, toggle);
    return cell;
}

/** Return a table cell that holds `text` as text. */
function textCell(text) {
    const cell = document.createElement('td');
    cell.textContent = text;
    return cell;
}

/** Fill a list element of the preview with `items` as text, and hide its box when the list is empty. */
function showList(boxId, items) {
    const box = document.getElementById(boxId);
    box.querySelector('ul').replaceChildren(...items.map(item => {
        const line = document.createElement('li');
        line.textContent = item;
        return line;
    }));
    box.hidden = items.length === 0;
}

/** Show the error of an import, and remove the preview, so Apply cannot change the form. */
function showImportError(message) {
    pendingImport = null;
    document.getElementById('channelSharePreview').hidden = true;
    document.getElementById('channelShareError').textContent = message;
}

/**
 * Show the preview of an import (FR-channel-share-7). The form stays unchanged until Apply.
 *
 * `preview` holds the channels, the bare LoRa enum names, the "Not applied" list, the warnings, and a `fields`
 * function that returns the form fields at Apply time.
 */
function showPreview(preview) {
    pendingImport = preview;
    const unread = Object.entries(preview.fields() || {})
        .filter(([name, value]) => isUnread(formControl(name), value))
        .map(([name]) => `${name}: ${UNREAD_REASON}`);
    document.getElementById('channelShareError').textContent = '';
    const rows = preview.channels.map(channel => {
        const row = document.createElement('tr');
        row.append(
            textCell(String(channel.index)),
            textCell(channel.name),
            pskCell(channel.psk, channel.default_key, channel.role),
            textCell(channel.role === 'PRIMARY' ? 'Primary' : 'Secondary'),
        );
        return row;
    });
    if (!rows.length) {
        const row = document.createElement('tr');
        const cell = textCell('The import has no channel.');
        cell.colSpan = 4;
        row.append(cell);
        rows.push(row);
    }
    document.querySelector('#channelSharePreview tbody').replaceChildren(...rows);
    document.getElementById('channelShareLora').textContent =
        `LoRa: region ${preview.region || 'not set'}, modem preset ${preview.modemPreset || 'not set'}.`;
    showList('channelShareNotApplied', [...preview.notApplied, ...unread]);
    showList('channelShareWarnings', preview.warnings);
    document.getElementById('channelSharePreview').hidden = false;
}

async function decodeUrl() {
    const response = await fetch('/api/v1/import/channel-url', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url: document.getElementById('channelShareUrl').value}),
    });
    if (!response.ok) {
        showImportError(await errorMessage(response));
        return;
    }
    const result = await response.json();
    const plan = urlPlan(result);
    if (plan === null) {
        showImportError(NO_FREE_INDEX_MESSAGE);
        return;
    }
    showPreview({
        channels: plan.channels,
        region: result.lora.region,
        modemPreset: result.lora.modem_preset,
        notApplied: plan.notApplied,
        warnings: result.warnings,
        // The operator can change the channel count after the preview, so Apply finds the free slots again.
        fields: () => {
            const current = urlPlan(result);
            return current === null ? null : channelUrlFields(current, result.lora);
        },
    });
}

async function readYamlFile(input) {
    const file = input.files[0];
    if (!file) {
        return;
    }
    const body = new FormData();
    body.append('file', file);
    const response = await fetch('/api/v1/import/cli-yaml', {method: 'POST', body});
    // A second choice of the same file fires no change event, so the input forgets the file after each upload.
    input.value = '';
    if (!response.ok) {
        showImportError(await errorMessage(response));
        return;
    }
    const result = await response.json();
    const share = document.getElementById('channelShare').dataset;
    const strip = (value, prefix) => (value ? value.replace(prefix, '') : null);
    showPreview({
        channels: yamlChannels(result.config),
        region: strip(result.config.lora_region, share.regionPrefix),
        modemPreset: strip(result.config.lora_modem_preset, share.modemPresetPrefix),
        notApplied: result.not_applied,
        warnings: result.warnings,
        fields: () => result.config,
    });
}

/** Return the form control of the field `name`, or null when the form has no control for it. */
function formControl(name) {
    // A switch has a hidden field with the same name, which sends `false` when the switch is off.
    return document.getElementById('configForm').querySelector(`[name="${CSS.escape(name)}"]:not([type="hidden"])`);
}

/**
 * Return true when the firmware version does not read the key of `control`, or does not read the enum `value`.
 *
 * The page disables the fieldset of each such key and each such enum option, so the form sends no value for it.
 */
function isUnread(control, value) {
    if (!control) {
        return false;
    }
    const option = control.tagName === 'SELECT' && Array.from(control.options).find(item => item.value === value);
    return control.matches(':disabled') || Boolean(option && option.disabled);
}

/**
 * Put `value` into a form control, as a user change, so the section switches and the tab counts follow it.
 *
 * A select gets an extra option for a value that it does not offer, for example position precision 0.
 */
function setControl(control, value) {
    if (control.type === 'checkbox') {
        control.checked = value === 'true';
    } else {
        if (control.tagName === 'SELECT' && !Array.from(control.options).some(option => option.value === value)) {
            // An empty region is the firmware default `UNSET`, which the form has no option for.
            control.add(new Option(value || 'Not set', value));
        }
        control.value = value;
    }
    control.dispatchEvent(new Event('change', {bubbles: true}));
}

/** Fill the config form from the preview (FR-channel-share-8). */
function applyImport() {
    const fields = pendingImport && pendingImport.fields();
    if (!fields) {
        showImportError(NO_FREE_INDEX_MESSAGE);
        return;
    }
    for (const [name, value] of Object.entries(fields)) {
        const control = formControl(name);
        // The preview lists each value that the firmware version does not read as "Not applied".
        if (control && !isUnread(control, value)) {
            setControl(control, value);
        }
    }
    pendingImport = null;
    document.getElementById('channelSharePreview').hidden = true;
}

/** Show the channel URL and the QR code of the config (FR-channel-share-9 and -10). */
async function exportChannels() {
    const result = document.getElementById('channelShareExportResult');
    const error = document.getElementById('channelShareExportError');
    // A disabled control is for a key that the firmware version does not read, and FormData leaves it out.
    const config = Object.fromEntries(new FormData(document.getElementById('configForm')));
    const response = await fetch('/api/v1/export/channel-url', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(config),
    });
    if (!response.ok) {
        result.hidden = true;
        error.textContent = await errorMessage(response);
        return;
    }
    const body = await response.json();
    error.textContent = '';
    showList('channelShareExportWarnings', body.warnings);
    document.getElementById('channelShareExportUrl').value = body.url;
    document.getElementById('channelShareCopyStatus').textContent = '';
    // DOMParser makes an inert SVG element, so the page inserts no server text as HTML.
    const svg = new DOMParser().parseFromString(body.qr_svg, 'image/svg+xml').documentElement;
    document.getElementById('channelShareQr').replaceChildren(document.importNode(svg, true));
    result.hidden = false;
}

/** Copy the exported channel URL. A page on plain HTTP has no Clipboard API, so the copy then uses the selection. */
async function copyExportUrl() {
    const field = document.getElementById('channelShareExportUrl');
    const status = document.getElementById('channelShareCopyStatus');
    if (navigator.clipboard) {
        await navigator.clipboard.writeText(field.value);
        status.textContent = 'Copied.';
        return;
    }
    field.select();
    status.textContent = document.execCommand('copy') ? 'Copied.' : 'Select the URL and copy it.';
}

document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('channelShareDecode').addEventListener('click', decodeUrl);
    document.getElementById('channelShareUrl').addEventListener('keydown', evt => {
        if (evt.key === 'Enter') {
            evt.preventDefault();
            decodeUrl();
        }
    });
    document.getElementById('channelShareFile').addEventListener('change', evt => readYamlFile(evt.target));
    document.getElementById('channelShareApply').addEventListener('click', applyImport);
    document.getElementById('channelShareCancel').addEventListener('click', () => {
        pendingImport = null;
        document.getElementById('channelSharePreview').hidden = true;
    });
    document.getElementById('channelShareExport').addEventListener('click', exportChannels);
    document.getElementById('channelShareCopy').addEventListener('click', copyExportUrl);
});

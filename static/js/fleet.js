// Fleet editor (FR-fleet-builds-12 and -13): the fleet device table form and the CSV import, with row errors.
// The routes give JSON, so this script calls them with fetch and puts server text in as text only.
// The page never holds an admin key, so an empty admin key field sends null and keeps the saved admin key.

const FIELDS = ['label', 'variant', 'long_name', 'short_name', 'role', 'lat', 'lon', 'alt', 'admin_key'];

function fleetId() {
    return document.getElementById('fleet').dataset.fleetId;
}

function deviceRows() {
    return Array.from(document.querySelectorAll('#fleet-devices tbody tr.fleet-row'));
}

/** Show one message in the status region of the page. `kind` is a Bootstrap alert colour. */
function showFleetStatus(kind, text) {
    const alert = document.createElement('div');
    alert.className = `alert alert-${kind}`;
    alert.textContent = text;
    document.getElementById('fleet-status').replaceChildren(alert);
}

/** Write the row number in each row, and show the empty state when the table has no row. */
function renumberRows() {
    const rows = deviceRows();
    rows.forEach((row, index) => { row.querySelector('.row-number').textContent = String(index + 1); });
    document.getElementById('fleet-empty').hidden = rows.length > 0;
}

/** Add one empty row to the table, with the fields of `device` when it is set, and return the row. */
function addRow(device) {
    const fragment = document.getElementById('fleet-row-template').content.cloneNode(true);
    const row = fragment.querySelector('tr.fleet-row');
    if (device) {
        row.dataset.deviceId = String(device.id);
        for (const name of FIELDS) {
            if (name !== 'admin_key' && device[name] !== null && device[name] !== undefined) {
                row.querySelector(`[name="${name}"]`).value = String(device[name]);
            }
        }
    }
    // The template row has the controls of a saved admin key, so a row without one loses them.
    if (!device || !device.has_admin_key) {
        row.querySelector('[name="admin_key"]').removeAttribute('placeholder');
        row.querySelector('.clear-admin-key').remove();
    }
    document.querySelector('#fleet-devices tbody').append(fragment);
    renumberRows();
    return row;
}

/** Show the rows of the saved fleet, so the table holds what the server saved. */
function showDevices(devices) {
    document.querySelector('#fleet-devices tbody').replaceChildren();
    devices.forEach(addRow);
    renumberRows();
}

/**
 * Return the fields of each row. An empty field gives null, so it keeps the value of the profile.
 * The id of a saved row goes with it, so the server changes that fleet device also after a rename.
 * A checked Clear admin key control sends "", which removes the saved admin key.
 */
function readRows() {
    return deviceRows().map(row => {
        const device = { id: row.dataset.deviceId ? Number(row.dataset.deviceId) : null };
        for (const name of FIELDS) {
            const value = row.querySelector(`[name="${name}"]`).value.trim();
            device[name] = value === '' ? null : value;
        }
        if (row.querySelector('[name="clear_admin_key"]')?.checked) {
            device.admin_key = '';
        }
        return device;
    });
}

function clearRowErrors() {
    document.querySelectorAll('.fleet-row-errors').forEach(errors => {
        errors.hidden = true;
        errors.querySelector('.row-errors').replaceChildren();
    });
    document.getElementById('csv-errors').replaceChildren();
}

function errorItem(text) {
    const item = document.createElement('li');
    item.textContent = text;
    return item;
}

/** Return the row errors of a 422, or one error with the HTTP status of another failed request. */
async function rowErrors(response) {
    const fallback = [{ row: null, field: null, msg: `The server returned HTTP ${response.status}.` }];
    if (response.status !== 422) return fallback;
    const body = await response.json();
    return Array.isArray(body.detail) ? body.detail : fallback;
}

function errorText(error) {
    const parts = [error.row ? `Row ${error.row}` : null, error.field, error.msg].filter(Boolean);
    return parts.join(': ');
}

/** Show each row error next to its row of the table. An error of no row goes to the status region. */
function showTableErrors(errors) {
    const rows = deviceRows();
    const general = [];
    for (const error of errors) {
        const row = error.row ? rows[error.row - 1] : null;
        if (!row) {
            general.push(errorText(error));
            continue;
        }
        const holder = row.nextElementSibling;
        holder.hidden = false;
        holder.querySelector('.row-errors').append(errorItem(errorText(error)));
    }
    const count = errors.length === 1 ? '1 error' : `${errors.length} errors`;
    showFleetStatus('danger', [`The fleet devices were not saved. Fix ${count}.`, ...general].join(' '));
}

async function saveDevices() {
    clearRowErrors();
    const response = await fetch(`/api/v1/fleets/${fleetId()}/devices`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ devices: readRows() }),
    });
    if (!response.ok) {
        showTableErrors(await rowErrors(response));
        return;
    }
    const fleet = await response.json();
    showDevices(fleet.devices);
    showFleetStatus('success', `Saved ${fleet.devices.length} fleet devices.`);
}

async function importCsv() {
    clearRowErrors();
    const response = await fetch(`/api/v1/fleets/${fleetId()}/devices/csv`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ csv: document.getElementById('csvText').value }),
    });
    if (!response.ok) {
        const errors = await rowErrors(response);
        document.getElementById('csv-errors').replaceChildren(...errors.map(error => errorItem(errorText(error))));
        return;
    }
    const fleet = await response.json();
    showDevices(fleet.devices);
    showFleetStatus('success', `Imported the CSV. The fleet has ${fleet.devices.length} fleet devices.`);
}

document.addEventListener('click', function(evt) {
    const target = evt.target.closest('button');
    if (!target) return;
    if (target.id === 'addRowBtn') {
        addRow().querySelector('[name="label"]').focus();
    } else if (target.hasAttribute('data-remove-row')) {
        const row = target.closest('tr');
        row.nextElementSibling.remove();
        row.remove();
        renumberRows();
    } else if (target.id === 'importCsvBtn') {
        importCsv();
    }
});

document.addEventListener('submit', function(evt) {
    if (evt.target.id === 'fleet-devices-form') {
        evt.preventDefault();
        saveDevices();
    }
});

/** Put the text of the CSV file in the CSV box, or show an error when the file is not UTF-8. */
async function readCsvFile(file) {
    clearRowErrors();
    // `File.text()` replaces a bad byte with no error, so a strict decoder shows the operator the problem.
    const decoder = new TextDecoder('utf-8', { fatal: true });
    try {
        document.getElementById('csvText').value = decoder.decode(await file.arrayBuffer());
    } catch (error) {
        const message = 'The file is not UTF-8 text. Save the CSV as UTF-8.';
        document.getElementById('csv-errors').replaceChildren(errorItem(message));
    }
}

document.addEventListener('change', function(evt) {
    if (evt.target.id === 'csvFile' && evt.target.files.length) {
        readCsvFile(evt.target.files[0]);
    }
});

document.addEventListener('DOMContentLoaded', renumberRows);

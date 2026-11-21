// An old page version stored the whole config, secrets included, under this key (FR-frontend-htmx-6).
localStorage.removeItem('meshtastic_config');

// The Build control and the Retry control post the config form to the build route.
const BUILD_CONTROLS = ['buildBtn', 'build-retry'];

// htmx does not swap a 4xx or 5xx response by default.
// A 422 holds field errors, and a 503 holds the build queue full message.
// Any other failed build request shows its message in the build panel, as text.
document.addEventListener('htmx:beforeSwap', function(evt) {
    const xhr = evt.detail.xhr;
    if (xhr.status === 422 || xhr.status === 503) {
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
        return;
    }
    if (xhr.status < 400 || !BUILD_CONTROLS.includes(evt.detail.elt.id)) {
        return;
    }
    // FastAPI puts the message in `detail`, and slowapi puts it in `error`.
    // A response without a message, for example a plain text 500, gets a message with the HTTP status.
    let message = `The build did not start. The server returned HTTP ${xhr.status}.`;
    try {
        const body = JSON.parse(xhr.responseText);
        const text = body && (body.detail ?? body.error);
        if (typeof text === 'string') {
            message = text;
        }
    } catch (e) {
        if (!(e instanceof SyntaxError)) {
            throw e;
        }
    }
    const alert = document.createElement('div');
    alert.className = 'alert alert-danger';
    alert.setAttribute('role', 'alert');
    alert.textContent = message;
    evt.detail.target.replaceChildren(alert);
});

// A preview or a build checks the config again, so the messages of the last check no longer apply.
// A build status refresh and a cancel do not check the config, so the messages stay.
document.addEventListener('htmx:beforeRequest', function(evt) {
    const elt = evt.detail.elt;
    if (!BUILD_CONTROLS.includes(elt.id) && elt.closest('#configForm') === null) {
        return;
    }
    document.querySelectorAll('.field-error').forEach(slot => { slot.textContent = ''; });
    document.querySelectorAll('#configForm .is-invalid').forEach(field => field.classList.remove('is-invalid'));
});

// The validation error state marks each wrong field and moves the focus to the first one.
document.addEventListener('htmx:afterSettle', function() {
    document.querySelectorAll('.field-error[data-field]').forEach(slot => {
        const selector = `#configForm [name="${CSS.escape(slot.getAttribute('data-field'))}"]:not([type="hidden"])`;
        const field = slot.textContent && document.querySelector(selector);
        if (field) {
            field.classList.add('is-invalid');
        }
    });
    const first = document.querySelector('#configForm .is-invalid');
    if (first) {
        first.focus();
    }
});

// A log event holds JSON, so the line goes into the log as text and htmx does not swap it as HTML.
document.addEventListener('htmx:sseBeforeMessage', function(evt) {
    if (evt.detail.type === 'log') {
        evt.preventDefault();
        evt.target.append(JSON.parse(evt.detail.data).line + '\n');
    }
});

// The server closes the progress stream after an ended build status.
// The page closes it first, so the browser neither reports an error nor reconnects.
const ENDED_STATUSES = ['complete', 'failed', 'cancelled'];
document.addEventListener('htmx:sseMessage', function(evt) {
    if (evt.detail.type === 'status' && ENDED_STATUSES.includes(JSON.parse(evt.detail.data).status)) {
        evt.detail.target.close();
    }
});

document.addEventListener('DOMContentLoaded', function() {
    // The browser can keep the chosen variant after a reload, so the check also runs at load.
    const variant = document.getElementById('variant');
    const buildBtn = document.getElementById('buildBtn');
    if (variant && buildBtn) {
        const enableBuild = () => { buildBtn.disabled = !variant.value; };
        variant.addEventListener('change', enableBuild);
        enableBuild();
    }

    // Dark mode toggle
    const darkModeToggle = document.getElementById('darkModeToggle');
    const htmlElement = document.documentElement;

    // The script in <head> sets the theme before first paint, so only the icon follows it here.
    updateDarkModeIcon(htmlElement.getAttribute('data-bs-theme'));

    // Toggle dark/light mode
    if (darkModeToggle) {
        darkModeToggle.addEventListener('click', function() {
            const currentTheme = htmlElement.getAttribute('data-bs-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

            htmlElement.setAttribute('data-bs-theme', newTheme);
            localStorage.setItem('theme', newTheme);
            updateDarkModeIcon(newTheme);
        });
    }

    function updateDarkModeIcon(theme) {
        if (!darkModeToggle) return;

        const icon = darkModeToggle.querySelector('i');
        if (theme === 'dark') {
            icon.classList.remove('bi-moon-fill');
            icon.classList.add('bi-sun-fill');
        } else {
            icon.classList.remove('bi-sun-fill');
            icon.classList.add('bi-moon-fill');
        }
    }

    const channelsToWrite = document.getElementById('channelsToWrite');
    const loraEnabled = document.getElementById('loraEnabled');
    const loraConfig = document.getElementById('loraConfig');
    const gpsEnabled = document.getElementById('gpsEnabled');
    const gpsConfig = document.getElementById('gpsConfig');
    const fixedPositionEnabled = document.getElementById('fixedPositionEnabled');
    const fixedPositionFields = document.getElementById('fixedPositionFields');
    const networkEnabled = document.getElementById('networkEnabled');
    const networkConfig = document.getElementById('networkConfig');
    const mqttEnabled = document.getElementById('mqttEnabled');
    const mqttConfig = document.getElementById('mqttConfig');

    // Toggle sections based on checkboxes
    if (loraEnabled) {
        loraEnabled.addEventListener('change', function() {
            loraConfig.style.display = this.checked ? 'block' : 'none';
        });
    }

    if (gpsEnabled) {
        gpsEnabled.addEventListener('change', function() {
            gpsConfig.style.display = this.checked ? 'block' : 'none';
        });
    }

    if (fixedPositionEnabled) {
        fixedPositionEnabled.addEventListener('change', function() {
            fixedPositionFields.style.display = this.checked ? 'block' : 'none';
        });
    }

    if (networkEnabled) {
        networkEnabled.addEventListener('change', function() {
            networkConfig.style.display = this.checked ? 'block' : 'none';
        });
    }

    if (mqttEnabled) {
        mqttEnabled.addEventListener('change', function() {
            mqttConfig.style.display = this.checked ? 'block' : 'none';
        });
    }

    // Handle channel tabs visibility
    if (channelsToWrite) {
        channelsToWrite.addEventListener('change', function() {
            const numChannels = parseInt(this.value);
            const tabs = document.querySelectorAll('#channelTabsList .nav-item');

            tabs.forEach((tab, index) => {
                if (index < numChannels) {
                    tab.style.display = 'block';
                } else {
                    tab.style.display = 'none';
                }
            });

            // Make first tab active if it exists
            if (numChannels > 0 && !document.querySelector('#channelTabsList .nav-link.active')) {
                document.querySelector('#channelTabsList .nav-link').classList.add('active');
                document.querySelector('#channelTabsContent .tab-pane').classList.add('show', 'active');
            }
        });

        // Trigger change event to set initial state
        channelsToWrite.dispatchEvent(new Event('change'));
    }

    // The server renders the form for the default firmware version, so a change of version asks for its key scan.
    const versionSelect = document.getElementById('firmware_version');
    if (versionSelect && versionSelect.value) {
        const loadKeyScan = () => {
            fetch(`/api/v1/userprefs/keys?version=${encodeURIComponent(versionSelect.value)}`)
                .then(response => (response.ok ? response.json() : null))
                .then(scan => { if (scan) applyKeyScan(scan); });
        };
        versionSelect.addEventListener('change', loadKeyScan);
        // The browser can keep the chosen version after a reload, and then the rendered form is for another version.
        if (versionSelect.value !== versionSelect.dataset.renderedVersion) {
            loadKeyScan();
        } else {
            updateUnreadNotes(versionSelect.value);
        }
    }

    document.querySelectorAll('.generate-psk-btn').forEach(button => {
        button.addEventListener('click', function() {
            const targetInput = document.getElementById(this.getAttribute('data-target'));
            if (targetInput) {
                targetInput.value = generatePSK();
            }
        });
    });
});

/**
 * Show only the controls, enum values and channel slots that one firmware version reads (FR-config-coverage-16).
 *
 * A hidden part is also disabled, so the form sends no value for a key that the firmware version does not read.
 * `routers/pages.py` applies the same rules when it renders the page. A null list shows everything.
 */
function applyKeyScan(scan) {
    const keys = scan.keys === null ? null : new Set(scan.keys);
    const values = scan.enum_values === null ? null : new Set(scan.enum_values);
    const setAbsent = (element, absent) => { element.hidden = absent; element.disabled = absent; };
    const slotRead = slot => keys === null || [...keys].some(key => key.startsWith(`USERPREFS_CHANNEL_${slot}_`));

    document.querySelectorAll('#configForm fieldset[data-userprefs-key]').forEach(fieldset => {
        setAbsent(fieldset, keys !== null && !keys.has(fieldset.dataset.userprefsKey));
    });
    document.querySelectorAll('#configForm option[value^="meshtastic_"]').forEach(option => {
        setAbsent(option, values !== null && !values.has(option.value));
    });
    document.querySelectorAll('#channelTabsList [data-channel-slot]').forEach(tab => {
        tab.hidden = !slotRead(Number(tab.dataset.channelSlot));
    });
    document.querySelectorAll('#channelsToWrite option').forEach(option => {
        const count = Number(option.value);
        setAbsent(option, count > 0 && !slotRead(count - 1));
    });
    // A disabled option sends no value, so a select on a removed option moves to the last option that remains.
    document.querySelectorAll('#configForm select').forEach(select => {
        if (select.selectedOptions[0] && select.selectedOptions[0].disabled) {
            const remaining = Array.from(select.options).filter(option => !option.disabled);
            if (remaining.length) {
                select.value = remaining[select.id === 'channelsToWrite' ? remaining.length - 1 : 0].value;
            }
        }
    });
    document.getElementById('channelsToWrite').dispatchEvent(new Event('change'));
    updateUnreadNotes(scan.version);
    document.getElementById('configForm').dataset.firmwareVersion = scan.version;
}

/** Put a note at the bottom of each form section that counts its hidden controls. */
function updateUnreadNotes(version) {
    document.querySelectorAll('#configForm .config-section').forEach(section => {
        const count = section.querySelectorAll('fieldset[data-userprefs-key][hidden]').length;
        let note = section.querySelector(':scope > .unread-note');
        if (!note) {
            note = document.createElement('p');
            note.className = 'unread-note form-text text-muted mb-0';
            section.append(note);
        }
        note.hidden = count === 0;
        note.textContent = count === 1
            ? `1 setting is not available in ${version}.`
            : `${count} settings are not available in ${version}.`;
    });
}

/**
 * Return a new 32-byte PSK as base64 (FR-frontend-htmx-18).
 *
 * An admin key has no generator, because it is the public key of the admin device.
 */
function generatePSK() {
    const bytes = new Uint8Array(32);
    window.crypto.getRandomValues(bytes);
    return btoa(String.fromCharCode(...bytes));
}

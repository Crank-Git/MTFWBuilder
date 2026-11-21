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

// The variants route names the firmware version `version`, and the build route names it `firmware_version`.
// Without an installed version the selector value is empty, and the route then answers with the no version state.
document.addEventListener('htmx:configRequest', function(evt) {
    const version = document.getElementById('firmware_version');
    if (evt.detail.elt.id === 'device-picker' && version && version.value) {
        evt.detail.parameters.version = version.value;
    }
});

// The picker list reloads on each filter change, so the chosen row gets its pressed state again after each swap.
function markChosenVariant() {
    const chosen = document.getElementById('variant').value;
    document.querySelectorAll('#device-results [data-env]').forEach(row => {
        const pressed = row.dataset.env === chosen;
        row.setAttribute('aria-pressed', String(pressed));
        row.classList.toggle('active', pressed);
    });
}

document.addEventListener('htmx:afterSwap', function(evt) {
    if (evt.detail.target.id === 'device-results') {
        markChosenVariant();
    }
});

document.addEventListener('click', function(evt) {
    const row = evt.target.closest('#device-results [data-env]');
    if (row) {
        const variant = document.getElementById('variant');
        variant.value = row.dataset.env;
        // A script change of a value fires no change event, and the Build control listens for one.
        variant.dispatchEvent(new Event('change'));
        markChosenVariant();
        return;
    }
    if (evt.target.closest('#device-clear')) {
        document.getElementById('device-search').value = '';
        document.querySelectorAll('#device-results input[name="arch"]').forEach(chip => { chip.checked = false; });
        htmx.trigger('#device-picker', 'picker:clear');
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
 * Return a new 32-byte key as base64 (FR-frontend-htmx-18).
 *
 * The server accepts a PSK or an admin key in base64, so one generator serves both fields.
 */
function generatePSK() {
    const bytes = new Uint8Array(32);
    window.crypto.getRandomValues(bytes);
    return btoa(String.fromCharCode(...bytes));
}

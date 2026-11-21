// An old page version stored the whole config, secrets included, under this key (FR-frontend-htmx-6).
localStorage.removeItem('meshtastic_config');

// htmx does not swap a 4xx or 5xx response by default.
// A 422 holds field errors, and a 503 holds the build queue full message.
document.addEventListener('htmx:beforeSwap', function(evt) {
    if (evt.detail.xhr.status === 422 || evt.detail.xhr.status === 503) {
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
    }
});

// The messages of the last preview no longer apply when the operator previews again.
document.addEventListener('htmx:beforeRequest', function() {
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

// Profiles (FR-fleet-builds-1 and -4): Save as profile and Load on the build page, Rename and Delete on the profiles page.
// The profile routes take and give JSON, so this script calls them with fetch and puts server text in as text only.

const REENTER_HINT = 'Re-enter this value. The profile does not store it.';

/** Show one message in the status region of the page. `kind` is a Bootstrap alert colour. */
function showProfileStatus(kind, text) {
    const alert = document.createElement('div');
    alert.className = `alert alert-${kind} mb-0`;
    alert.textContent = text;
    document.getElementById('profile-status').replaceChildren(alert);
}

/** Return the message of a failed profile request: the `detail` text, each field error, or the HTTP status. */
async function profileError(response) {
    const fallback = `The server returned HTTP ${response.status}.`;
    let body;
    try {
        body = await response.json();
    } catch (e) {
        if (e instanceof SyntaxError) return fallback;
        throw e;
    }
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail)) {
        // The first part of `loc` is always `body`, so the message starts at the field name.
        return body.detail.map(error => `${error.loc.slice(1).join('.')}: ${error.msg}`).join(' ');
    }
    return fallback;
}

/** Return the config form fields as one flat object. A checkbox after its hidden `false` field gives `true`. */
function readConfigForm() {
    const config = {};
    for (const [name, value] of new FormData(document.getElementById('configForm'))) {
        config[name] = value;
    }
    return config;
}

async function saveProfile() {
    const name = document.getElementById('profileName').value.trim();
    if (!name) {
        showProfileStatus('danger', 'Enter a profile name.');
        document.getElementById('profileName').focus();
        return;
    }
    const body = { name, config: readConfigForm() };
    const headers = { 'Content-Type': 'application/json' };
    const storesSecrets = document.getElementById('profileStoresSecrets');
    if (storesSecrets) {
        body.stores_secrets = storesSecrets.checked;
        headers['X-CSRF-Token'] = document.getElementById('saveProfileBtn').dataset.csrfToken;
    }
    const response = await fetch('/api/v1/profiles', { method: 'POST', headers, body: JSON.stringify(body) });
    if (!response.ok) {
        showProfileStatus('danger', `The profile was not saved. ${await profileError(response)}`);
        return;
    }
    const profile = await response.json();
    addProfileOption(profile);
    showProfileStatus('success', `Saved the profile ${profile.name}.`);
}

/** Put the new profile in the Load list and select it. */
function addProfileOption(profile) {
    const select = document.getElementById('profileSelect');
    if (select.disabled) {
        select.replaceChildren();
    }
    const option = new Option(profile.name, String(profile.id));
    const after = Array.from(select.options).find(other => other.text.localeCompare(profile.name) > 0);
    select.insertBefore(option, after || null);
    select.value = option.value;
    select.disabled = false;
    document.getElementById('loadProfileBtn').disabled = false;
}

/** Set the form controls to the default values, then to each value of `config`. */
function fillConfigForm(config) {
    const form = document.getElementById('configForm');
    form.reset();
    for (const element of form.elements) {
        // A hidden field holds the `false` value of a checkbox, so it keeps its fixed value.
        if (element.name && element.type !== 'hidden' && element.name in config) {
            if (element.type === 'checkbox') {
                element.checked = config[element.name] === element.value;
            } else {
                element.value = config[element.name];
            }
        }
    }
    // The section switches and the tab labels update on a change event only.
    for (const element of form.elements) {
        if (element.name && element.type !== 'hidden') {
            element.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }
}

function clearReenterHints() {
    document.querySelectorAll('.must-reenter-hint').forEach(hint => hint.remove());
    document.querySelectorAll('#configForm .must-reenter').forEach(field => field.classList.remove('must-reenter', 'border-warning'));
}

/** Mark each field that the operator must re-enter, and return the marked fields in page order. */
function markReenterFields(names) {
    const form = document.getElementById('configForm');
    const fields = [];
    for (const name of names) {
        const field = form.querySelector(`[name="${CSS.escape(name)}"]:not([type="hidden"])`);
        if (!field) continue;
        const hint = document.createElement('div');
        hint.className = 'must-reenter-hint form-text text-warning';
        hint.id = `reenter-${field.id || name}`;
        hint.textContent = REENTER_HINT;
        (field.closest('.input-group') || field).after(hint);
        field.classList.add('must-reenter', 'border-warning');
        field.setAttribute('aria-describedby', [field.getAttribute('aria-describedby'), hint.id].filter(Boolean).join(' '));
        field.addEventListener('input', () => {
            hint.remove();
            field.classList.remove('must-reenter', 'border-warning');
        }, { once: true });
        fields.push(field);
    }
    return fields.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
}

async function loadProfile(profileId) {
    const response = await fetch(`/api/v1/profiles/${encodeURIComponent(profileId)}`);
    if (!response.ok) {
        showProfileStatus('danger', `The profile was not loaded. ${await profileError(response)}`);
        return;
    }
    const profile = await response.json();
    clearReenterHints();
    fillConfigForm(profile.config);
    const fields = markReenterFields(profile.must_reenter);
    document.getElementById('profileName').value = profile.name;
    const parts = [`Loaded the profile ${profile.name}.`];
    if (profile.must_reenter.length) {
        parts.push(`Re-enter ${profile.must_reenter.length === 1 ? '1 secret' : `${profile.must_reenter.length} secrets`}: ${profile.must_reenter.join(', ')}.`);
    }
    if (profile.warning) {
        parts.push(profile.warning);
    }
    showProfileStatus(profile.warning || profile.must_reenter.length ? 'warning' : 'success', parts.join(' '));
    if (fields.length) {
        // The first field to re-enter opens its tab, so the operator sees the hint at once.
        showField(fields[0]);
    }
}

/** Show the profile list of the server again, after a rename or a delete. */
function reloadProfileList() {
    return htmx.ajax('GET', '/profiles', { target: '#profile-list', swap: 'outerHTML' });
}

/** Show one part of a profile row: the rename form, the delete confirm, or neither. */
function showRowPart(row, part) {
    row.querySelector('.profile-rename').hidden = part !== 'rename';
    row.querySelector('.profile-delete-confirm').hidden = part !== 'delete';
}

async function deleteProfile(button) {
    const row = button.closest('[data-profile]');
    const name = row.querySelector('.profile-name').textContent;
    const response = await fetch(`/api/v1/profiles/${button.dataset.confirmDelete}`, { method: 'DELETE' });
    if (!response.ok) {
        showProfileStatus('danger', `The profile was not deleted. ${await profileError(response)}`);
        return;
    }
    await reloadProfileList();
    showProfileStatus('success', `Deleted the profile ${name}.`);
}

async function renameProfile(form) {
    const name = form.elements.name.value.trim();
    const response = await fetch(`/api/v1/profiles/${form.dataset.profileId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
    });
    if (!response.ok) {
        form.querySelector('.field-error').textContent = await profileError(response);
        return;
    }
    await reloadProfileList();
    showProfileStatus('success', `Renamed the profile to ${name}.`);
}

document.addEventListener('click', function(evt) {
    const target = evt.target.closest('button');
    if (!target) return;
    if (target.id === 'saveProfileBtn') {
        saveProfile();
    } else if (target.id === 'loadProfileBtn') {
        loadProfile(document.getElementById('profileSelect').value);
    } else if (target.dataset.rename) {
        const row = target.closest('[data-profile]');
        showRowPart(row, 'rename');
        row.querySelector('.profile-rename input').focus();
    } else if (target.dataset.delete) {
        const row = target.closest('[data-profile]');
        showRowPart(row, 'delete');
        row.querySelector('[data-confirm-delete]').focus();
    } else if (target.hasAttribute('data-cancel')) {
        showRowPart(target.closest('[data-profile]'), null);
    } else if (target.dataset.confirmDelete) {
        deleteProfile(target);
    }
});

document.addEventListener('submit', function(evt) {
    if (evt.target.classList.contains('profile-rename')) {
        evt.preventDefault();
        renameProfile(evt.target);
    }
});

// The Load control of the profiles page opens the build page with `?profile=<id>`.
document.addEventListener('DOMContentLoaded', function() {
    const profileId = new URLSearchParams(window.location.search).get('profile');
    const select = document.getElementById('profileSelect');
    if (profileId && select) {
        select.value = profileId;
        loadProfile(profileId);
    }
});

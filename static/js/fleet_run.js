// Start fleet run on the fleet editor and Retry failed on the fleet run page (FR-fleet-builds-15, -16 and -18).
// Each "must re-enter" secret goes in the request body only. The script never puts it in browser storage.
// The routes give JSON, so this script calls them with fetch and puts server text in as text only.

/** Return the secret of each filled secret input. An empty input is left out, so a retry uses the held secrets. */
function enteredSecrets() {
    const secrets = {};
    document.querySelectorAll('input[data-secret]').forEach(input => {
        if (input.value) secrets[input.dataset.secret] = input.value;
    });
    return secrets;
}

/** Return the text of one field error. A fleet device location gets its row number, which counts from 1. */
function runErrorText(error) {
    const loc = error.loc.slice(1);
    if (loc[0] === 'devices' && typeof loc[1] === 'number') {
        return [`Row ${loc[1] + 1}`, ...loc.slice(2), error.msg].join(': ');
    }
    return [loc.join('.'), error.msg].filter(Boolean).join(': ');
}

/** Return one line for each error of a failed request: the field errors of a 422, or the detail text. */
async function runErrorLines(response) {
    const fallback = [`The server returned HTTP ${response.status}.`];
    const body = await response.json().catch(() => null);
    if (body && Array.isArray(body.detail)) return body.detail.map(runErrorText);
    if (body && typeof body.detail === 'string') return [body.detail];
    return fallback;
}

/**
 * Send the secrets to `url`, and return the 202 body, or null after the errors show in `#run-errors`.
 * An admin page gives `button` a CSRF token, because a fleet run of an admin uses the stored secrets.
 */
async function postRun(url, button) {
    const errors = document.getElementById('run-errors');
    errors.replaceChildren();
    const headers = { 'Content-Type': 'application/json' };
    if (button.dataset.csrfToken) headers['X-CSRF-Token'] = button.dataset.csrfToken;
    const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({ secrets: enteredSecrets() }),
    });
    if (response.status === 202) return response.json();
    const lines = await runErrorLines(response);
    errors.replaceChildren(...lines.map(line => {
        const item = document.createElement('li');
        item.textContent = line;
        return item;
    }));
    return null;
}

document.addEventListener('click', async function(evt) {
    const target = evt.target.closest('button');
    if (!target) return;
    if (target.id === 'startRunBtn') {
        const fleetId = document.getElementById('fleet').dataset.fleetId;
        const accepted = await postRun(`/api/v1/fleets/${fleetId}/runs`, target);
        if (accepted) window.location.assign(`/fleet-runs/${accepted.run_id}`);
    } else if (target.id === 'retryBtn') {
        const accepted = await postRun(`/api/v1/fleet-runs/${target.dataset.runId}/retry`, target);
        // A reload shows the state that polls again, and it clears the secret inputs.
        if (accepted) window.location.reload();
    }
});

// Fleets page: the form that creates a fleet from a profile (FR-fleet-builds-10).
// The route takes JSON, so this script calls it with fetch and puts server text in as text only.

function errorItem(text) {
    const item = document.createElement('li');
    item.textContent = text;
    return item;
}

/** Return one line for each field error of a 422, or one line with the HTTP status of another failed request. */
async function errorLines(response) {
    const fallback = [`The server returned HTTP ${response.status}.`];
    if (response.status !== 422) return fallback;
    const body = await response.json();
    if (!Array.isArray(body.detail)) return fallback;
    return body.detail.map(error => `${error.loc[error.loc.length - 1]}: ${error.msg}`);
}

async function createFleet(form) {
    const errors = document.getElementById('create-fleet-errors');
    errors.replaceChildren();
    const response = await fetch('/api/v1/fleets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            name: form.elements.name.value,
            profile_id: Number(form.elements.profile_id.value),
            // An empty value selects the default firmware version when the fleet run starts.
            firmware_version: form.elements.firmware_version.value || null,
        }),
    });
    if (!response.ok) {
        errors.replaceChildren(...(await errorLines(response)).map(errorItem));
        return;
    }
    const fleet = await response.json();
    window.location.assign(`/fleets/${fleet.id}`);
}

document.addEventListener('submit', function(evt) {
    if (evt.target.id === 'create-fleet-form') {
        evt.preventDefault();
        createFleet(evt.target);
    }
});

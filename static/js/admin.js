// The firmware install route reads a JSON body, and htmx sends a form body without this extension.
// htmx sends the string that `encodeParameters` returns as the request body (https://htmx.org/extensions/building/).
htmx.defineExtension('json-body', {
    onEvent: function(name, evt) {
        if (name === 'htmx:configRequest') {
            evt.detail.headers['Content-Type'] = 'application/json';
        }
    },
    encodeParameters: function(xhr, parameters) {
        return JSON.stringify(Object.fromEntries(parameters));
    },
});

// htmx does not swap a 4xx or 5xx response by default.
// The firmware routes send an HTML fragment with the message only to htmx, so the page shows each one.
document.addEventListener('htmx:beforeSwap', function(evt) {
    const xhr = evt.detail.xhr;
    const type = xhr.getResponseHeader('Content-Type') || '';
    if (xhr.status >= 400 && type.startsWith('text/html')) {
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
    }
});

// The progress stream needs an admin session, and the browser reconnects a stream that closes with an error.
// A closed install stream asks the install status route, so a 401 sends the page to the sign-in form.
document.addEventListener('htmx:sseError', function(evt) {
    const panel = evt.target;
    if (panel.id.startsWith('install-') && evt.detail.source.readyState === EventSource.CLOSED) {
        htmx.trigger(panel.querySelector('[hx-get]'), 'sse:status');
    }
});

// The server closes the install progress stream after `ready`, and main.js closes it after `failed`.
// The page closes it first, so the browser neither reports an error nor reconnects.
document.addEventListener('htmx:sseMessage', function(evt) {
    if (evt.detail.type === 'status' && JSON.parse(evt.detail.data).status === 'ready') {
        evt.detail.target.close();
    }
});

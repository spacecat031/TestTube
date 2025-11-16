# serve.py
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import threading
import time
import sys
import json
import textwrap
import requests

# -------------------------
# DynamicDOM (exposed as ts.dynamic)
# -------------------------
class _ConsoleHelper:
    def __init__(self, dynamic_ref):
        self._dynamic = dynamic_ref

    def log(self, msg):
        # queue a console log action
        self._dynamic._updates.append({'action': 'console', 'message': str(msg)})


class DynamicDOM:
    def __init__(self):
        # handlers: mapping selector -> event_type -> function
        self.handlers = {}        # { selector: { event_type: fn } }
        # queued updates to send back to client after handler runs
        self._updates = []
        self._current_event_snapshot = None
        self.console = _ConsoleHelper(self)

    # decorator to register click handlers
    def on_click(self, selector):
        def decorator(fn):
            self.handlers.setdefault(selector, {})['click'] = fn
            return fn
        return decorator

    def change_content(self, selector, content):
        self._updates.append({'action': 'set', 'selector': selector, 'content': str(content)})

    # remove elements matching selector
    def remove(self, selector):
        """Queue an update that removes all elements matching selector on the client."""
        self._updates.append({'action': 'remove', 'selector': selector})

    def secret(self, url="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRPte_8tzXVbALCRVQkp7N2OYC3vrgF0XDZJA&s"):
        self._updates.append({'action': 'easteregg', 'url': url})

    # read a value from the snapshot (set during handle_event)
    def getcontent(self, selector):
        """
        Read the latest snapshot value for the given selector.
        Supported snapshot keys: '#id' or '.class'
        Returns string or None
        """
        if not self._current_event_snapshot:
            return None
        return self._current_event_snapshot.get(selector)

    def _clear_updates(self):
        self._updates = []

    def _pop_updates(self):
        u = list(self._updates)
        self._clear_updates()
        return u

    def handle_event(self, selector, event_type, snapshot):
        """
        Called by the HTTP handler when an event arrives.
        snapshot: dict produced by client JS mapping selectors -> string values
        Returns list of updates to send back to client.
        """
        # set current snapshot for getcontent(...) calls
        self._current_event_snapshot = snapshot or {}
        try:
            if selector in self.handlers and event_type in self.handlers[selector]:
                fn = self.handlers[selector][event_type]
                fn()
            # collect queued updates (if any)
            updates = self._pop_updates()
            return updates
        finally:
            # clear snapshot after handler finishes
            self._current_event_snapshot = None


dynamic = DynamicDOM()


def serve(port=8000, folder=None, db=False):
    if db:
        print("\033[91m############################\033[0m")
        print("\033[91m#        WARNING:          #\033[0m")
        print("\033[91m#  never use db=true for   #\033[0m")
        print("\033[91m#       production         #\033[0m")
        print("\033[91m############################\033[0m")

    if not isinstance(port, int) or not (0 <= port <= 65535):
        print(f"Invalid port {port}, using default port 8000.")
        port = 8000

    if folder is None:
        base_dir = os.getcwd()
    else:
        base_dir = os.path.abspath(folder)

    if not os.path.isdir(base_dir):
        print(f"Folder '{base_dir}' does not exist. Using current working directory instead.")
        base_dir = os.getcwd()

    last_mtimes = {}

    def scan_changes():
        changed = False
        for root, _, files in os.walk(base_dir):
            for f in files:
                path = os.path.join(root, f)
                try:
                    m = os.path.getmtime(path)
                    if path not in last_mtimes:
                        last_mtimes[path] = m
                    else:
                        if last_mtimes[path] != m:
                            last_mtimes[path] = m
                            changed = True
                except:
                    pass
        return changed

    def watch_reload(httpd):
        while True:
            time.sleep(1)
            if scan_changes():
                print("Detected change, reloading server...")
                os.execv(sys.executable, [sys.executable] + sys.argv)

    # JS bridge to inject into pages
    DYNAMIC_BRIDGE_JS = r"""
<script>
/* TestTube dynamic bridge version 1.0.0*/
function buildTsSnapshot() {
    const snap = {};
    // ids
    document.querySelectorAll('[id]').forEach(el => {
        const key = '#' + el.id;
        if (typeof el.value !== 'undefined') snap[key] = el.value;
        else snap[key] = (el.textContent || '').trim();
    });
    // classes (first element per class)
    document.querySelectorAll('[class]').forEach(el => {
        el.classList.forEach(c => {
            const key = '.' + c;
            if (!(key in snap)) {
                if (typeof el.value !== 'undefined') snap[key] = el.value;
                else snap[key] = (el.textContent || '').trim();
            }
        });
    });
    return snap;
}

async function tsTriggerEvent(selector, type) {
    try {
        const snapshot = buildTsSnapshot();
        const res = await fetch('/__ts_dynamic__', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({selector: selector, type: type, snapshot: snapshot})
        });
        if (!res.ok) return;
        const updates = await res.json();
        if (!Array.isArray(updates)) return;
        updates.forEach(u => {
            try {
                // backward compatibility: if object has selector+content treat as set
                if (u.selector && u.content && (!u.action)) {
                    const el = document.querySelector(u.selector);
                    if (!el) return;
                    if ('value' in el) el.value = u.content;
                    else el.textContent = u.content;
                    return;
                }

                switch (u.action) {
                    case 'set': {
                        const el = document.querySelector(u.selector);
                        if (!el) break;
                        if ('value' in el) el.value = u.content;
                        else el.textContent = u.content;
                        break;
                    }
                    case 'remove': {
                        document.querySelectorAll(u.selector).forEach(el => {
                            el.remove();
                        });
                        break;
                    }
                    case 'console': {
                        try { console.log(u.message); } catch(e) {}
                        break;
                    }
                    case 'easteregg': {
                        try {
                            const existing = document.getElementById('__ts_easteregg_overlay');
                            if (existing) existing.remove();

                            const overlay = document.createElement('div');
                            overlay.id = '__ts_easteregg_overlay';
                            overlay.style.position = 'fixed';
                            overlay.style.top = '0';
                            overlay.style.left = '0';
                            overlay.style.width = '100%';
                            overlay.style.height = '100%';
                            overlay.style.zIndex = '2147483647';
                            overlay.style.backgroundColor = '#000';
                            overlay.style.display = 'flex';
                            overlay.style.alignItems = 'center';
                            overlay.style.justifyContent = 'center';

                            const img = document.createElement('img');
                            img.src = u.url;
                            img.style.width = '100%';
                            img.style.height = '100%';
                            img.style.objectFit = 'cover';
                            img.style.display = 'block';

                            // close overlay on click
                            overlay.addEventListener('click', () => {
                                overlay.remove();
                            });

                            overlay.appendChild(img);
                            document.body.appendChild(overlay);
                        } catch (e) {}
                        break;
                    }
                    default:
                        // unknown action - ignore
                        break;
                }
            } catch (e) {
                console.warn('ts update failed', e);
            }
        });
    } catch (e) {
        console.warn('ts dynamic error', e);
    }
}

// listen for clicks and map them to id or class selector
document.addEventListener('click', e => {
    const el = e.target;
    if (!el) return;
    if (el.id) {
        tsTriggerEvent('#' + el.id, 'click');
        return;
    }
    if (el.classList && el.classList.length > 0) {
        tsTriggerEvent('.' + el.classList[0], 'click');
        return;
    }
});
</script>
"""

    class SingleFileHandler(BaseHTTPRequestHandler):
        def inject_dynamic_js(self, html_content):
            # Insert the dynamic bridge just before </body>
            if "</body>" in html_content:
                return html_content.replace("</body>", DYNAMIC_BRIDGE_JS + "\n</body>")
            else:
                # append if no body close tag
                return html_content + DYNAMIC_BRIDGE_JS

        def do_GET(self):
            if self.path == '/':
                requested_file = os.path.join(base_dir, 'index.html')
            elif self.path == '/__ts_dynamic__':
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps([]).encode('utf-8'))
                return
            else:
                requested_file = os.path.join(base_dir, self.path.lstrip('/'))

            if os.path.isfile(requested_file):
                try:
                    with open(requested_file, 'rb') as f:
                        content = f.read().decode('utf-8')
                    content = self.inject_dynamic_js(content)
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(content.encode('utf-8'))
                except Exception as e:
                    self.send_error(500, f"Server error: {e}")
            else:
                self.send_error(404, "File Not Found")

        def do_POST(self):
            # only endpoint for dynamic events
            if self.path != '/__ts_dynamic__':
                self.send_error(404)
                return

            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            try:
                data = json.loads(body)
                selector = data.get('selector')
                event_type = data.get('type')
                snapshot = data.get('snapshot') or {}
                # handle event via dynamic instance
                updates = dynamic.handle_event(selector, event_type, snapshot)
                # respond with updates array
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(updates).encode('utf-8'))
            except Exception as e:
                self.send_error(500, f"Dynamic handler error: {e}")

    server_address = ('', port)
    httpd = HTTPServer(server_address, SingleFileHandler)

    if db:
        threading.Thread(target=watch_reload, args=(httpd,), daemon=True).start()

    print(f"Serving files from '{base_dir}' on port {port}...")
    httpd.serve_forever()


# expose common names for package importers
__all__ = ["serve", "dynamic"]

"""Test LINE webhook relay. Terminate TLS at the hosting platform."""

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


MAX_BODY = 512 * 1024


def signature(key, payload):
    return base64.b64encode(hmac.digest(key, payload, 'sha256')).decode('ascii')


def make_handler(channel_secret, destination, worker_secret, forward):
    slots = threading.BoundedSemaphore(4)

    class Webhook(BaseHTTPRequestHandler):
        def log_message(self, *_):
            # Do not log query strings, connection metadata or request data.
            pass

        def respond(self, status):
            self.send_response(status)
            self.end_headers()

        def do_GET(self):
            self.respond(200 if self.path == '/healthz' else 404)

        def do_POST(self):
            if self.path != '/line/webhook':
                return self.respond(404)
            if not slots.acquire(blocking=False):
                return self.respond(503)
            try:
                self.handle_webhook()
            finally:
                slots.release()

        def handle_webhook(self):
            lengths = self.headers.get_all('Content-Length') or []
            supplied = self.headers.get_all('X-Line-Signature') or []
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                return self.respond(411)
            if len(lengths[0]) > 7 or int(lengths[0]) > MAX_BODY:
                return self.respond(413)
            if len(supplied) != 1 or not supplied[0].isascii():
                return self.respond(401)
            self.connection.settimeout(5)
            try:
                raw = self.rfile.read(int(lengths[0]))
            except OSError:
                return self.respond(408)
            if not hmac.compare_digest(signature(channel_secret, raw), supplied[0]):
                return self.respond(401)
            try:
                payload = json.loads(raw.decode('utf-8'))
            except (ValueError, RecursionError):
                return self.respond(400)
            if not isinstance(payload, dict) or payload.get('destination') != destination:
                return self.respond(403)
            if not isinstance(payload.get('events'), list):
                return self.respond(400)
            if not payload['events']:
                return self.respond(200)
            timestamp = str(int(time.time()))
            encoded = base64.b64encode(raw).decode('ascii')
            envelope = {
                'timestamp': timestamp,
                'payload': encoded,
                'signature': signature(worker_secret, f'{timestamp}.{encoded}'.encode('ascii')),
            }
            try:
                accepted = forward(envelope)
            except (OSError, ValueError):
                accepted = False
            return self.respond(200 if accepted else 503)

    return Webhook


def main():
    worker_url = os.environ['GAS_WORKER_URL']
    parsed = urlsplit(worker_url)
    if (parsed.scheme, parsed.netloc) != ('https', 'script.google.com') or not parsed.path.endswith('/exec') or parsed.query or parsed.fragment:
        raise SystemExit('GAS_WORKER_URL must be an Apps Script /exec URL')
    worker_secret = os.environ['GAS_WORKER_SECRET'].encode()
    channel_secret = os.environ['LINE_TEST_CHANNEL_SECRET'].encode()
    destination = os.environ['LINE_TEST_BOT_ID']
    if len(worker_secret) < 32 or not channel_secret or not destination:
        raise SystemExit('Invalid test credentials')

    def forward(envelope):
        data = json.dumps(envelope).encode('utf-8')
        request = Request(worker_url, data, {'Content-Type': 'application/json'})
        with urlopen(request, timeout=7) as response:
            result = json.loads(response.read(16_384))
        return isinstance(result, dict) and result.get('ok') is True

    handler = make_handler(channel_secret, destination, worker_secret, forward)
    with ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8080'))), handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()

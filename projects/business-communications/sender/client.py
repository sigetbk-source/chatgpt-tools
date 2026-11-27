"""Submit to the isolated LINE send queue, or query an app-owned queue row."""

import argparse
import base64
import hmac
import json
import os
import re
import time
from urllib.request import Request, urlopen


def make_envelope(client, secret, action, timestamp):
    payload = base64.b64encode(json.dumps(action, ensure_ascii=False).encode()).decode()
    message = f'{client}.{timestamp}.{payload}'.encode()
    signature = base64.b64encode(hmac.digest(secret.encode(), message, 'sha256')).decode()
    return {'client': client, 'timestamp': str(timestamp), 'payload': payload, 'signature': signature}


def call(action):
    url = os.environ['LINE_SEND_TEST_URL']
    if not re.fullmatch(r'https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec', url):
        raise ValueError('Expected an Apps Script /exec URL')
    envelope = make_envelope(os.environ['LINE_SEND_CLIENT_ID'], os.environ['LINE_SEND_CLIENT_SECRET'],
                             action, int(time.time()))
    request = Request(url, json.dumps(envelope).encode(), {'Content-Type': 'application/json'})
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read(16384))


def main():
    args = argparse.ArgumentParser()
    commands = args.add_subparsers(dest='command', required=True)
    enqueue = commands.add_parser('enqueue')
    enqueue.add_argument('idempotency_key')
    enqueue.add_argument('recipient_id')
    enqueue.add_argument('message_file', type=argparse.FileType('r', encoding='utf-8'))
    enqueue.add_argument('--drive-file')
    enqueue.add_argument('--not-before-ms', type=int, default=0)
    status = commands.add_parser('status')
    status.add_argument('send_id')
    options = args.parse_args()
    if options.command == 'enqueue':
        request = {'action': 'enqueue', 'environment': 'test', 'recipient': options.recipient_id,
                   'text': options.message_file.read(), 'notBefore': options.not_before_ms,
                   'type': 'drive_link' if options.drive_file else 'text', 'fileId': options.drive_file,
                   'idempotencyKey': options.idempotency_key}
        options.message_file.close()
    else:
        request = {'action': 'status', 'sendId': options.send_id}
    result = call(request)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())

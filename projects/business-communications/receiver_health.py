"""Run the test receiver health checks independently of the LINE account."""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


class RestrictedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (code in {302, 303} and req.get_method() == 'POST' and
                target.scheme == 'https' and target.netloc == 'script.googleusercontent.com' and
                target.path == '/macros/echo'):
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        return None


def check_receiver(worker_url, gas_url, secret, max_pending_age, opener=None):
    worker = urlsplit(worker_url)
    if (worker.scheme != 'https' or not worker.hostname or worker.username is not None or
            worker.path != '/healthz' or worker.query or worker.fragment or
            not re.fullmatch(r'https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec', gas_url) or
            len(secret) < 32):
        raise ValueError('test receiver health settings are incomplete')
    opener = opener or urllib.request.build_opener(RestrictedRedirects())
    try:
        with opener.open(worker_url, timeout=15) as response:
            if response.status != 200:
                return 'worker_unavailable'
    except OSError:
        return 'worker_unavailable'

    nonce = secrets.token_hex(16)
    timestamp = str(int(time.time()))
    body = {'kind': 'receiver_health', 'nonce': nonce}
    payload = base64.b64encode(json.dumps(body).encode()).decode()
    signature = base64.b64encode(hmac.new(secret.encode(), f'{timestamp}.{payload}'.encode(), hashlib.sha256).digest()).decode()
    envelope = {'kind': 'receiver_health', 'timestamp': timestamp, 'payload': payload, 'signature': signature}
    request = urllib.request.Request(gas_url, json.dumps(envelope).encode(), {'Content-Type': 'application/json'})
    try:
        with opener.open(request, timeout=25) as response:
            result = json.loads(response.read(4097))
        oldest = result.get('oldestPendingSeconds') if isinstance(result, dict) else None
        if (not isinstance(result, dict) or result.get('ok') is not True or result.get('nonce') != nonce or
                type(oldest) is not int or oldest < 0):
            return 'storage_unavailable'
    except (OSError, ValueError):
        return 'storage_unavailable'
    return 'backlog' if oldest >= max_pending_age else 'healthy'


def record_check(database, result, failures_before_alert):
    db = sqlite3.connect(database, timeout=10)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY, failures INTEGER, alert INTEGER)')
            failures, alerted = db.execute('SELECT failures, alert FROM probe WHERE id=1').fetchone() or (0, 0)
            failures = 0 if result == 'healthy' else failures + 1
            active = bool(failures and (alerted or failures >= failures_before_alert))
            notice = 'outage' if active and not alerted else 'recovered' if alerted and not active else None
            db.execute('INSERT OR REPLACE INTO probe VALUES (1,?,?)', (failures, active))
        return {'result': result, 'consecutive_failures': failures, 'notice': notice}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state_db', type=Path)
    parser.add_argument('--failures', type=int, default=3)
    parser.add_argument('--max-pending-age', type=int, default=600)
    args = parser.parse_args()
    if min(args.failures, args.max_pending_age) < 1:
        parser.error('thresholds must be positive')
    result = check_receiver(os.environ.get('LINE_RECEIVER_WORKER_HEALTH_URL', ''),
                            os.environ.get('LINE_RECEIVER_GAS_URL', ''),
                            os.environ.get('RECEIVER_HEALTH_SECRET', ''), args.max_pending_age)
    status = record_check(args.state_db, result, args.failures)
    print(json.dumps(status))
    return int(result != 'healthy')


if __name__ == '__main__':
    raise SystemExit(main())

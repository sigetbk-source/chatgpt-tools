import base64
import hashlib
import hmac
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

from receiver_health import RestrictedRedirects, check_receiver, record_check


SECRET = 'local-mock-key' * 3
WORKER = 'https://receiver.example.test/healthz'
GAS = 'https://script.google.com/macros/s/test/exec'


class Reply:
    def __init__(self, data=None):
        self.data = data
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount):
        return json.dumps(self.data).encode()[:amount]


class HealthTest(unittest.TestCase):
    def test_independent_worker_and_authenticated_storage_check(self):
        test = self

        class FakeHTTP:
            def open(self, request, **kwargs):
                if request == WORKER:
                    return Reply()
                test.assertEqual(request.full_url, GAS)
                signed = json.loads(request.data)
                proof = (signed['timestamp'] + '.' + signed['payload']).encode()
                test.assertEqual(base64.b64decode(signed['signature']), hmac.digest(SECRET.encode(), proof, hashlib.sha256))
                data = json.loads(base64.b64decode(signed['payload']))
                test.assertEqual(data['kind'], 'receiver_health')
                return Reply({'ok': True, 'nonce': data['nonce'], 'oldestPendingSeconds': 300})

        self.assertEqual(check_receiver(WORKER, GAS, SECRET, 600, FakeHTTP()), 'healthy')
        self.assertEqual(check_receiver(WORKER, GAS, SECRET, 300, FakeHTTP()), 'backlog')

    def test_alert_transition_and_recovery_are_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'health.db'
            states = ['worker_unavailable', 'backlog', 'storage_unavailable', 'healthy', 'healthy']
            notices = [record_check(path, status, 2)['notice'] for status in states]
            self.assertEqual(notices, [None, 'outage', None, 'recovered', None])

    def test_only_google_echo_redirect_may_follow_signed_post(self):
        handler = RestrictedRedirects()
        req = urllib.request.Request(GAS, b'signed', {'Content-Type': 'application/json'})
        self.assertIsNone(handler.redirect_request(req, None, 302, '', {}, 'https://elsewhere.test/macros/echo'))
        follow = handler.redirect_request(req, None, 302, '', {}, 'https://script.googleusercontent.com/macros/echo?q=1')
        self.assertEqual(follow.get_method(), 'GET')
        self.assertIsNone(follow.data)


if __name__ == '__main__':
    unittest.main()

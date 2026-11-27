import base64
import io
import json
import unittest

from relay import make_handler, signature


class BufferedConnection:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()

    def makefile(self, mode, _):
        assert mode == 'rb'
        return self.input

    def sendall(self, data):
        self.output.extend(data)

    def settimeout(self, _):
        pass


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.forwarded = []
        self.worker_ok = True

        def forward(envelope):
            self.forwarded.append(envelope)
            return self.worker_ok

        self.handler = make_handler(b'channel', 'test-bot', b'worker', forward)

    def post(self, raw, signed=None):
        signed = signature(b'channel', raw) if signed is None else signed
        head = (f'POST /line/webhook HTTP/1.1\r\nHost: localhost\r\nContent-Length: {len(raw)}\r\n'
                f'X-Line-Signature: {signed}\r\n\r\n')
        conn = BufferedConnection(head.encode() + raw)
        self.handler(conn, ('127.0.0.1', 0), None)
        return int(conn.output.split(b' ', 2)[1])

    def test_rejects_signature_or_destination_mismatch(self):
        raw = b'{"destination":"test-bot","events":[]}'
        self.assertEqual(self.post(raw, 'invalid'), 401)
        self.assertEqual(self.post(raw.replace(b'test-bot', b'other')), 403)
        self.assertEqual(self.forwarded, [])

    def test_line_verification_request(self):
        self.assertEqual(self.post(b'{"destination":"test-bot","events":[]}'), 200)
        self.assertEqual(self.forwarded, [])

    def test_preserves_exact_bytes_and_reports_worker_failure(self):
        raw = '{ "destination":"test-bot", "events":[{"message":"テスト"}]}'.encode()
        self.worker_ok = False
        self.assertEqual(self.post(raw), 503)
        self.worker_ok = True
        self.assertEqual(self.post(raw), 200)
        envelope = self.forwarded[-1]
        self.assertEqual(base64.b64decode(envelope['payload']), raw)
        canonical = (envelope['timestamp'] + '.' + envelope['payload']).encode()
        self.assertEqual(envelope['signature'], signature(b'worker', canonical))

    def test_invalid_json_with_valid_signature(self):
        self.assertEqual(self.post(b'{'), 400)
        self.assertEqual(self.post(json.dumps({'destination': 'test-bot', 'events': None}).encode()), 400)


if __name__ == '__main__':
    unittest.main()

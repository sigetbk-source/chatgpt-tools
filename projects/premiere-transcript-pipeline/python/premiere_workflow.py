"""File IPC client for the explicitly enabled Premiere 25 UXP workflow panel.

The client never edits a .prproj or a media file. A timeout is indeterminate:
the panel may have finished an in-flight transaction, so inspect its response
or take a fresh snapshot before retrying with a new nonce.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import time


OPERATIONS = frozenset({'capabilities', 'selected_target', 'snapshot', 'apply_transcript', 'import_srt', 'caption_track_snapshot', 'prepare_caption_test_sequence'})


class BridgeError(RuntimeError):
    def __init__(self, message: str, result: dict | None = None):
        self.result = result
        super().__init__(message)


class BridgeTimeout(BridgeError):
    def __init__(self, nonce: str):
        self.nonce = nonce
        super().__init__(f'Premiere response timed out; outcome unknown (nonce={nonce})')


class BridgeClient:
    def __init__(self, root: str | Path, *, poll_seconds: float = 0.1,
                 inbox_name: str = 'inbox', outbox_name: str = 'outbox'):
        self.root = Path(root).expanduser().resolve()
        self.poll_seconds = poll_seconds
        self.inbox_name = inbox_name
        self.outbox_name = outbox_name
        self.operations = OPERATIONS
        if poll_seconds <= 0:
            raise ValueError('poll_seconds must be positive')

    def request(self, operation: str, payload: dict | None = None, *, timeout: float = 30.0) -> dict:
        if operation not in self.operations:
            raise ValueError('Unsupported Premiere operation')
        if timeout <= 0 or timeout > 120:
            raise ValueError('timeout must be within 0–120 seconds')
        if payload is not None and not isinstance(payload, dict):
            raise ValueError('payload must be an object')
        inbox, outbox = self.root / self.inbox_name, self.root / self.outbox_name
        if not inbox.is_dir() or not outbox.is_dir():
            raise BridgeError('Premiere bridge directory is not initialized by the panel')
        nonce = secrets.token_hex(16)
        deadline = time.time() + timeout
        request = {'version': 1, 'nonce': nonce, 'operation': operation,
                   'payload': payload or {}, 'deadlineMs': int(deadline * 1000)}
        pending = inbox / f'{nonce}.tmp'
        ready = inbox / f'{nonce}.json'
        pending.write_text(json.dumps(request, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        os.replace(pending, ready)
        try:
            while time.time() < deadline:
                result = self.read_response(nonce, consume=True)
                if result is not None:
                    return result
                time.sleep(min(self.poll_seconds, max(0, deadline - time.time())))
            raise BridgeTimeout(nonce)
        finally:
            # Removing a queued request prevents a late transaction. An already
            # claimed request may still finish and leave its response in outbox.
            ready.unlink(missing_ok=True)
            pending.unlink(missing_ok=True)

    def read_response(self, nonce: str, *, consume: bool = False) -> dict | None:
        if not isinstance(nonce, str) or len(nonce) != 32 or any(c not in '0123456789abcdef' for c in nonce):
            raise ValueError('Invalid nonce')
        path = self.root / self.outbox_name / f'{nonce}.json'
        try:
            response = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        if response.get('nonce') != nonce:
            raise BridgeError('Response nonce mismatch')
        if consume:
            path.unlink(missing_ok=True)
        if not response.get('ok'):
            raise BridgeError(response.get('error', 'Premiere operation failed'), response.get('result'))
        return response['result']


class CaptionBridgeClient(BridgeClient):
    """CEP caption placement bracketed by Premiere 25 UXP track readbacks."""

    def __init__(self, root: str | Path, *, poll_seconds: float = 0.1):
        super().__init__(root, poll_seconds=poll_seconds,
                         inbox_name='caption-inbox', outbox_name='caption-outbox')
        self.operations = frozenset({'place_srt'})

    def place_srt(self, uxp: BridgeClient, *, project_path: str, srt_path: str | Path,
                  source_time_verified: bool, timeout: float = 120.0) -> dict:
        if source_time_verified is not True:
            raise ValueError('Source-time verification is required for caption placement')
        srt = Path(srt_path).expanduser().resolve()
        if srt.suffix.lower() != '.srt':
            raise ValueError('Expected an SRT file')
        text = srt.read_text(encoding='utf-8')
        if not text.strip():
            raise ValueError('SRT is empty')
        before = uxp.request('caption_track_snapshot', {'projectPath': project_path})
        if before['captionTrackCount'] != 0:
            raise BridgeError('Target sequence already has caption tracks')
        payload = {'projectPath': before['projectPath'],
                   'sequenceGuid': before['sequenceGuid'],
                   'sequenceName': before['sequenceName'],
                   'srtPath': str(srt), 'expectedText': text,
                   'sourceTimeVerified': True, 'expectedCaptionTrackCount': 0}
        placed = self.request('place_srt', payload, timeout=timeout)
        after = uxp.request('caption_track_snapshot', {'projectPath': project_path,
                     'sequenceGuid': before['sequenceGuid']})
        if (after['sequenceGuid'] != before['sequenceGuid'] or
                after['captionTrackCount'] != 1 or not placed.get('created') or
                not placed.get('saved')):
            raise BridgeError('Caption placement readback failed',
                              {'before': before, 'after': after, 'placement': placed})
        return {**placed, 'state': 'placement_created_pending_visual',
                'captionTrackCount': 1}

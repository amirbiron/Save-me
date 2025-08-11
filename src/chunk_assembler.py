import os
import time
import base64
import hashlib
import gzip
from typing import Dict, Any, Optional
from chunk_protocol import parse_protocol_message


class ChunkAssembler:
    def __init__(self, root_dir: str, session_ttl_sec: int = 900):
        if not root_dir:
            raise ValueError('root_dir is required')
        self.root_dir = os.path.abspath(root_dir)
        self.session_ttl_sec = session_ttl_sec
        self.sessions: Dict[str, Dict[str, Any]] = {}
        os.makedirs(self.root_dir, exist_ok=True)

    def _key(self, chat_key: str, id_: str) -> str:
        return f"{chat_key}::{id_}"

    def _gc(self) -> None:
        now = time.time()
        to_delete = []
        for k, s in self.sessions.items():
            if now - s['created_at'] > self.session_ttl_sec:
                to_delete.append(k)
        for k in to_delete:
            del self.sessions[k]

    def _safe_join(self, rel_path: str) -> str:
        target = os.path.abspath(os.path.join(self.root_dir, rel_path))
        if not target.startswith(self.root_dir + os.sep) and target != self.root_dir:
            raise ValueError('Path traversal detected')
        os.makedirs(os.path.dirname(target), exist_ok=True)
        return target

    def _sha256(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def on_message(self, raw_text: str, chat_key: str) -> Optional[Dict[str, Any]]:
        self._gc()
        msg = parse_protocol_message(raw_text)
        if not msg:
            return None
        if msg['type'] == 'error':
            return { 'ok': False, 'error': msg['error'] }

        if msg['type'] == 'start':
            meta = msg['meta']
            if not meta.get('path'):
                return { 'ok': False, 'error': 'missing path' }
            if meta.get('encoding') != 'base64':
                return { 'ok': False, 'error': 'unsupported encoding' }
            if meta.get('compression') not in ('none', 'gzip'):
                return { 'ok': False, 'error': 'unsupported compression' }
            session = {
                'id': msg['id'],
                'path': meta['path'],
                'total': meta['total_parts'],
                'encoding': meta['encoding'],
                'compression': meta['compression'],
                'sha256': meta.get('sha256'),
                'created_at': time.time(),
                'received': {},  # index -> base64 string
            }
            self.sessions[self._key(chat_key, msg['id'])] = session
            return { 'ok': True, 'stage': 'start', 'id': msg['id'], 'total': session['total'] }

        if msg['type'] == 'chunk':
            key = self._key(chat_key, msg['id'])
            session = self.sessions.get(key)
            if not session:
                return { 'ok': False, 'error': 'no active session for id' }
            if msg['total'] != session['total']:
                return { 'ok': False, 'error': 'total mismatch' }
            if msg['index'] < 1 or msg['index'] > session['total']:
                return { 'ok': False, 'error': 'index out of range' }
            session['received'][msg['index']] = msg['dataB64']
            return { 'ok': True, 'stage': 'chunk', 'id': msg['id'], 'index': msg['index'], 'receivedCount': len(session['received']), 'total': session['total'] }

        if msg['type'] == 'end':
            key = self._key(chat_key, msg['id'])
            session = self.sessions.get(key)
            if not session:
                return { 'ok': False, 'error': 'no active session for id' }
            if len(session['received']) != session['total']:
                missing = [i for i in range(1, session['total'] + 1) if i not in session['received']]
                return { 'ok': False, 'error': 'missing chunks', 'missing': missing }
            ordered = [session['received'][i] for i in range(1, session['total'] + 1)]
            joined_b64 = ''.join(ordered)
            try:
                data = base64.b64decode(joined_b64)
            except Exception:
                return { 'ok': False, 'error': 'base64 decode failed' }
            if session['compression'] == 'gzip':
                try:
                    data = gzip.decompress(data)
                except Exception:
                    return { 'ok': False, 'error': 'gzip decompress failed' }
            if session.get('sha256'):
                actual = self._sha256(data)
                if actual != session['sha256']:
                    return { 'ok': False, 'error': 'sha256 mismatch', 'expected': session['sha256'], 'actual': actual }
            try:
                target_path = self._safe_join(session['path'])
                with open(target_path, 'wb') as f:
                    f.write(data)
            except Exception as e:
                return { 'ok': False, 'error': f'write failed: {e}' }
            del self.sessions[key]
            return { 'ok': True, 'stage': 'end', 'id': session['id'], 'path': target_path, 'bytes': len(data) }

        if msg['type'] == 'cancel':
            key = self._key(chat_key, msg['id'])
            existed = key in self.sessions
            if existed:
                del self.sessions[key]
            return { 'ok': True, 'stage': 'cancel', 'id': msg['id'], 'existed': existed }

        return { 'ok': False, 'error': 'unsupported message type' }
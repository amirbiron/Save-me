import re
from typing import Optional, Dict, Any

START_HEADER = '[FILE START]'

_CHUNK_RE = re.compile(r"^\[FILE CHUNK\s+(\d+)\s*\/\s*(\d+)\s+id=([^\]]+)\]\s*\n([\s\S]*)$", re.MULTILINE)
_END_RE = re.compile(r"^\[FILE END\s+id=([^\]]+)\]\s*$", re.MULTILINE)
_CANCEL_RE = re.compile(r"^\[FILE CANCEL\s+id=([^\]]+)\]\s*$", re.MULTILINE)


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        inner = text.split('\n', 1)
        if len(inner) == 2:
            body = inner[1]
            if body.endswith('```'):
                body = body[:-3]
            return body.strip()
    return text


def parse_protocol_message(raw_text: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_text, str) or not raw_text:
        return None
    text = _strip_code_fences(raw_text).strip()

    if text.startswith(START_HEADER):
        body = text[len(START_HEADER):].strip()
        meta: Dict[str, str] = {}
        for line in body.split('\n'):
            line = line.strip()
            if not line or '=' not in line:
                continue
            key, value = line.split('=', 1)
            meta[key.strip()] = value.strip()
        if 'id' not in meta:
            return {"type": "error", "error": "missing id in START"}
        try:
            total = int(meta.get('total_parts', '0'))
        except ValueError:
            total = 0
        if total <= 0:
            return {"type": "error", "error": "invalid total_parts"}
        encoding = (meta.get('encoding') or 'base64').lower()
        compression = (meta.get('compression') or 'none').lower()
        return {
            'type': 'start',
            'id': meta['id'],
            'meta': {
                'path': meta.get('path'),
                'total_parts': total,
                'encoding': encoding,
                'compression': compression,
                'sha256': meta.get('sha256') or None,
            }
        }

    m = _CHUNK_RE.match(text)
    if m:
        index = int(m.group(1))
        total = int(m.group(2))
        id_ = m.group(3).strip()
        data = m.group(4).strip()
        return { 'type': 'chunk', 'id': id_, 'index': index, 'total': total, 'dataB64': data }

    m = _END_RE.match(text)
    if m:
        return { 'type': 'end', 'id': m.group(1).strip() }

    m = _CANCEL_RE.match(text)
    if m:
        return { 'type': 'cancel', 'id': m.group(1).strip() }

    return None
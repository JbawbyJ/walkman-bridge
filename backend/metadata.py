"""Bounded music tags shared by local and link imports.

This module only handles text. Media decoding and source-clearance leases belong
to the caller; a filename must be the original display name, never a cache path.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import PurePosixPath
import re
import unicodedata


TAG_LIMIT = 512
METADATA_LIMIT = 256 * 1024
TRANSFER_TAGS = ('title', 'artist', 'album', 'genre', 'date', 'year', 'track')


def _text(value) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return ''
    # Bound before processing and remove controls, including bidi overrides.
    value = str(value)[:4096]
    value = ''.join(' ' if char.isspace() else char
                    for char in value if char.isspace() or not unicodedata.category(char).startswith('C'))
    return ' '.join(value.split())[:TAG_LIMIT]


def _valid_date(value) -> str | None:
    value = _text(value)
    if re.fullmatch(r'[0-9]{4}', value) and 1 <= int(value) <= 9999:
        return value
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    return None


def normalize_metadata(tags: Mapping | None, original_name: str) -> dict:
    """Keep useful bounded tags and use honest fallbacks for missing details."""
    fields = {str(key).casefold(): value for key, value in (tags or {}).items()}
    name = PurePosixPath(str(original_name).replace('\\', '/')).stem
    result = {
        'title': _text(fields.get('title')) or _text(name) or 'Untitled track',
        'artist': _text(fields.get('artist')) or 'Unknown artist',
        'album': _text(fields.get('album')) or 'Unknown album',
    }
    genre = _text(fields.get('genre'))
    if genre:
        result['genre'] = genre
    recorded = _valid_date(fields.get('date')) or _valid_date(fields.get('year'))
    if recorded:
        result['date'], result['year'] = recorded, int(recorded[:4])
    track = _text(fields.get('track', fields.get('tracknumber')))
    matched = re.fullmatch(r'([0-9]{1,4})(?:\s*/\s*([0-9]{1,4}))?', track)
    if matched:
        number = int(matched[1])
        total = int(matched[2]) if matched[2] is not None else None
        if number > 0 and (total is None or total >= number):
            result['track'] = str(number) + (f'/{total}' if total is not None else '')
    return result


def parse_ffmetadata(text: str) -> dict:
    """Parse only global tags from FFmpeg's escaped FFMETADATA1 format.

    Diagnostics cannot be parsed reliably as tags (a title can contain a newline
    or a fake field label). This parser consumes a separate bounded output pipe.
    Stream/chapter tags must not replace the recording's global title or artist.
    """
    if len(text.encode('utf-8')) > METADATA_LIMIT:
        raise ValueError('Audio metadata exceeds the processing limit')
    if not text.startswith(';FFMETADATA1\n'):
        raise ValueError('Invalid audio metadata output')
    lines, pending, escaped = [], [], False
    for char in text[len(';FFMETADATA1\n'):]:
        if char == '\n' and not escaped:
            lines.append(''.join(pending))
            pending = []
        else:
            pending.append(char)
        if escaped:
            escaped = False
        elif char == '\\':
            escaped = True
    if pending:
        lines.append(''.join(pending))

    def unescape(value):
        return re.sub(r'\\(.)', r'\1', value, flags=re.DOTALL)

    result = {}
    for line in lines:
        if line.startswith('['):
            break
        if not line or line.startswith((';', '#')):
            continue
        escaped = False
        for index, char in enumerate(line):
            if char == '=' and not escaped:
                key = unescape(line[:index]).casefold()
                if key in TRANSFER_TAGS or key == 'tracknumber':
                    result.setdefault(key, unescape(line[index + 1:]))
                break
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
    return result

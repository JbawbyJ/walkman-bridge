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
TRANSFER_TAGS = ('title', 'artist', 'album', 'album_artist', 'genre', 'date', 'year', 'track', 'disc')
TAG_ALIASES = {'albumartist': 'album_artist', 'album artist': 'album_artist',
               'tracknumber': 'track', 'discnumber': 'disc'}


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
    for alias, canonical in TAG_ALIASES.items():
        if canonical not in fields and alias in fields:
            fields[canonical] = fields[alias]
    name = PurePosixPath(str(original_name).replace('\\', '/')).stem
    result = {
        'title': _text(fields.get('title')) or _text(name) or 'Untitled track',
        'artist': _text(fields.get('artist')) or 'Unknown artist',
        'album': _text(fields.get('album')) or 'Unknown album',
    }
    for key in ('genre', 'album_artist'):
        value = _text(fields.get(key))
        if value:
            result[key] = value
    recorded = _valid_date(fields.get('date')) or _valid_date(fields.get('year'))
    if recorded:
        result['date'], result['year'] = recorded, int(recorded[:4])
    for key in ('track', 'disc'):
        matched = re.fullmatch(r'([0-9]{1,4})(?:\s*/\s*([0-9]{1,4}))?', _text(fields.get(key)))
        if matched:
            number = int(matched[1])
            total = int(matched[2]) if matched[2] is not None else None
            if number > 0 and (total is None or total >= number):
                result[key] = str(number) + (f'/{total}' if total is not None else '')
    return result


def supplied_metadata(tags: Mapping | None) -> dict:
    """Normalize only actual supplied tags; never promote a display fallback."""
    raw = {TAG_ALIASES.get(str(k).casefold(), str(k).casefold()): v
           for k, v in (tags or {}).items() if _text(v)}
    normalized = normalize_metadata(raw, '')
    keys = set(raw)
    if 'date' in keys or 'year' in keys:
        keys.update(('date', 'year'))
    return {k: v for k, v in normalized.items() if k in keys}


def resolve_metadata(embedded, hints, overrides, original_name, hint_sources=None):
    """User edits > embedded tags > provider fields > honest display fallback."""
    embedded, hints = supplied_metadata(embedded), supplied_metadata(hints)
    overrides = {k: v for k, v in (overrides or {}).items() if k in TRANSFER_TAGS}
    values = normalize_metadata({**hints, **embedded, **overrides}, original_name)
    provenance = {}
    for key in set(values) | set(overrides):
        if key in overrides:
            provenance[key] = 'user_edit'
        elif key in embedded:
            provenance[key] = 'embedded'
        elif key in hints:
            label = (hint_sources or {}).get(key, 'provider_legacy')
            provenance[key] = label if isinstance(label, str) and re.fullmatch(r'[a-zA-Z0-9_.]{1,100}', label) else 'provider'
        else:
            provenance[key] = 'filename' if key == 'title' else 'missing'
    return values, provenance


def parse_ffmetadata(text: str, *, include_audio_stream: bool = False) -> dict:
    """Parse bounded global tags and optionally the explicitly mapped audio stream.

    Diagnostics cannot be parsed reliably as tags (a title can contain a newline
    or a fake field label). This parser consumes a separate bounded output pipe.
    With include_audio_stream, the caller must map only the first audio stream
    into this output. Its tags fill missing globals (Vorbis/Opus); chapters and
    subsequent streams are ignored. Cover-image tags cannot become song tags.
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

    result, audio = {}, {}
    section, seen_stream = result, False
    for line in lines:
        if line.startswith('['):
            if not include_audio_stream:
                break
            section = audio if line == '[STREAM]' and not seen_stream else None
            if line == '[STREAM]':
                seen_stream = True
            continue
        if section is None:
            continue
        if not line or line.startswith((';', '#')):
            continue
        escaped = False
        for index, char in enumerate(line):
            if char == '=' and not escaped:
                key = unescape(line[:index]).casefold()
                key = TAG_ALIASES.get(key, key)
                if key in TRANSFER_TAGS:
                    section.setdefault(key, unescape(line[index + 1:]))
                break
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
    return {**audio, **{key: value for key, value in result.items() if value.strip()}}

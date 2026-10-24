"""Read-only evidence of how a Walkman stores album jackets (cover art).

Run it against the Walkman's drive (for example E:\\) or a copied backup folder.
It opens every file read-only and writes nothing to the device. It reports:

- every 03GINF01.DAT album record, live and in OMGAUDIO/db_backup (the copy
  SonicStage left), with the PICP/PIC0 picture tags in hex and decoded;
- every 20Pxx jacket JPEG: size, hash, JPEG frame type and pixel size;
- where the jacket file names appear in the other database files;
- which tag frames the OMA audio headers carry (a picture inside the audio
  header would show up as GEOB or APIC).

Usage: python probe-walkman-jackets.py E:\\ --output walkman-jackets.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

RECORD = 0x310
TAG = 0x80
TAG_NAMES = ('TIT2', 'TPE1', 'TCON', 'TSOP', 'PICP', 'PIC0')
MAX_TABLE = 4 * 1024 * 1024
MAX_MATCHES = 50
MAX_OMA = 400


def u16(data, at):
    return int.from_bytes(data[at:at + 2], 'big')


def u32(data, at):
    return int.from_bytes(data[at:at + 4], 'big')


def child(folder, name):
    """Case-insensitive child lookup; FAT is case-insensitive but copies may not be."""
    for entry in folder.iterdir():
        if entry.name.lower() == name.lower():
            return entry
    return None


def find_omgaudio(path):
    path = Path(path)
    if path.name.lower() == 'omgaudio' and path.is_dir():
        return path
    found = child(path, 'OMGAUDIO')
    if found is None or not found.is_dir():
        raise SystemExit(f'No OMGAUDIO folder under {path}')
    return found


def decode(raw, encoding):
    text = raw.rstrip(b'\0')
    if encoding == 2:
        if len(text) % 2:
            text += b'\0'
        return text.decode('utf-16-be', 'replace').rstrip('\0')
    return text.decode('latin-1')


def album_records(path):
    data = path.read_bytes()[:MAX_TABLE]
    result = {'file': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
              'header_hex': data[:0x30].hex(), 'records': []}
    if len(data) < 0x30 or data[:4] != b'GPIF' or data[16:20] != b'GPFB':
        result['error'] = 'Not a GPIF/GPFB table'
        return result
    count, size = u16(data, 0x24), u16(data, 0x26)
    result.update(declared_records=count, record_size=hex(size))
    for index in range(count):
        start = 0x30 + index * RECORD
        if start + RECORD > len(data):
            result['error'] = f'Table ends inside record {index + 1}'
            break
        record = {'slot': index + 1, 'header_hex': data[start:start + 16].hex(), 'tags': []}
        for number in range(6):
            at = start + 16 + number * TAG
            raw = data[at:at + TAG]
            name = raw[:4].decode('latin-1')
            encoding = u16(raw, 4)
            tag = {'expected': TAG_NAMES[number], 'name': name, 'encoding': hex(encoding),
                   'text': decode(raw[6:], encoding)}
            if TAG_NAMES[number] in ('PICP', 'PIC0') or name not in TAG_NAMES:
                tag['hex'] = raw.hex()
            record['tags'].append(tag)
        result['records'].append(record)
    return result


def album_tree(path):
    data = path.read_bytes()[:MAX_TABLE]
    result = {'file': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'groups': []}
    if data[:4] != b'TREE':
        result['error'] = 'Not a TREE table'
        return result
    sections = {}
    for at in (16, 32):  # the table of contents has two entries, as JacketDatabase.section reads it
        name = data[at:at + 4]
        if name in (b'GPLB', b'TPLB'):
            sections[name] = (u32(data, at + 4), u32(data, at + 8))
    if b'GPLB' not in sections or b'TPLB' not in sections:
        result['error'] = 'Missing GPLB or TPLB'
        return result
    group_at, members_at = sections[b'GPLB'][0], sections[b'TPLB'][0]
    groups = [(u16(data, group_at + 16 + n * 8), u16(data, group_at + 16 + n * 8 + 2), u16(data, group_at + 16 + n * 8 + 4))
              for n in range(u16(data, group_at + 4))]
    members = [u16(data, members_at + 16 + n * 2) for n in range(u16(data, members_at + 4))]
    active = sorted((start, slot) for slot, flag, start in groups if flag or start)
    for position, (start, slot) in enumerate(active):
        end = active[position + 1][0] if position + 1 < len(active) else len(members) + 1
        result['groups'].append({'slot': slot, 'track_ids': members[start - 1:end - 1]})
    return result


def jpeg_frame(data):
    if data[:2] != b'\xff\xd8':
        return {'jpeg': False}
    at = 2
    names = {0xC0: 'SOF0 baseline', 0xC1: 'SOF1 extended', 0xC2: 'SOF2 progressive', 0xC3: 'SOF3 lossless'}
    while at + 4 <= len(data):
        if data[at] != 0xFF:
            return {'jpeg': True, 'frame': 'not found'}
        marker = data[at + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            at += 2
            continue
        length = u16(data, at + 2)
        if marker in names or 0xC5 <= marker <= 0xCF and marker not in (0xC8, 0xCC):
            return {'jpeg': True, 'frame': names.get(marker, hex(marker)),
                    'height': u16(data, at + 5), 'width': u16(data, at + 7)}
        if marker == 0xDA:
            break
        at += 2 + length
    return {'jpeg': True, 'frame': 'not found'}


def pictures(omgaudio):
    found = []
    for folder in [omgaudio, *(p for p in [child(omgaudio, 'db_backup')] if p)]:
        for sub in sorted(folder.iterdir()):
            if sub.is_dir() and sub.name.upper().startswith('20P'):
                for image in sorted(sub.iterdir()):
                    if image.is_file():
                        data = image.read_bytes()[:16 * 1024 * 1024]
                        found.append({'path': str(image.relative_to(omgaudio)), 'bytes': image.stat().st_size,
                                      'sha256': hashlib.sha256(data).hexdigest(), **jpeg_frame(data)})
    return found


def references(omgaudio, picture_rows):
    needles = set()
    for row in picture_rows:
        stem = Path(row['path']).stem.upper()
        folder = Path(row['path']).parent.name.upper()
        needles.update({stem, stem[:6], folder, Path(row['path']).name.upper()})
    needles.discard('')
    hits = []
    for table in sorted(omgaudio.rglob('*')):
        if not table.is_file() or table.suffix.upper() != '.DAT':
            continue
        data = table.read_bytes()[:MAX_TABLE]
        for needle in sorted(needles):
            for label, pattern in (('ascii', needle.encode('ascii')), ('utf-16be', needle.encode('utf-16-be'))):
                offsets, at = [], data.find(pattern)
                while at != -1 and len(offsets) < MAX_MATCHES:
                    offsets.append(hex(at))
                    at = data.find(pattern, at + 1)
                if offsets:
                    hits.append({'needle': needle, 'encoding': label,
                                 'file': str(table.relative_to(omgaudio)), 'offsets': offsets})
    return hits


def oma_headers(omgaudio):
    frames, sizes, pictures_found, sampled = Counter(), Counter(), [], 0
    for folder in sorted(omgaudio.iterdir()):
        if not (folder.is_dir() and folder.name.upper().startswith('10F')):
            continue
        for track in sorted(folder.iterdir()):
            if sampled >= MAX_OMA or track.suffix.upper() != '.OMA':
                continue
            sampled += 1
            with track.open('rb') as stream:
                head = stream.read(10)
                if head[:3] != b'ea3':
                    frames['no ea3 header'] += 1
                    continue
                size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
                sizes[hex(size)] += 1
                body = stream.read(min(size, 4 * 1024 * 1024))
            at = 0
            while at + 10 <= len(body) and body[at:at + 4].strip(b'\0'):
                name = body[at:at + 4].decode('latin-1', 'replace')
                length = u32(body, at + 4)
                frames[name] += 1
                if name in ('GEOB', 'APIC', 'PIC ') and len(pictures_found) < 20:
                    pictures_found.append({'file': str(track.relative_to(omgaudio)), 'frame': name,
                                           'bytes': length, 'start_hex': body[at + 10:at + 10 + 64].hex()})
                if length <= 0:
                    break
                at += 10 + length
    return {'sampled': sampled, 'ea3_sizes': dict(sizes), 'frames': dict(frames), 'picture_frames': pictures_found}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('device', type=Path, help='Walkman drive (e.g. E:\\), its parent folder, or OMGAUDIO itself')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    omgaudio = find_omgaudio(args.device)
    report = {'omgaudio': str(omgaudio), 'tables': [], 'trees': []}
    for label, folder in (('live', omgaudio), ('db_backup', child(omgaudio, 'db_backup'))):
        if folder is None:
            continue
        info, tree = child(folder, '03GINF01.DAT'), child(folder, '01TREE01.DAT')
        if info:
            report['tables'].append({'which': label, **album_records(info)})
        if tree:
            report['trees'].append({'which': label, **album_tree(tree)})
    report['pictures'] = pictures(omgaudio)
    report['references'] = references(omgaudio, report['pictures'])
    report['oma_headers'] = oma_headers(omgaudio)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text + '\n', encoding='utf-8')
        print(f'Wrote {args.output}')
    else:
        print(text)


if __name__ == '__main__':
    main()

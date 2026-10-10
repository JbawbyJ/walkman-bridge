"""Tag fidelity and cover extraction, using synthetic audio and generated artwork."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

import transcode
from metadata import normalize_metadata, parse_ffmetadata
from transcode import AudioError, analyze_cleared_audio, convert_managed, extract_cleared_artwork


def test_missing_tags_use_original_filename_and_never_managed_directory():
    assert normalize_metadata({}, r'C:\Music\Evening song.flac') == {
        'title': 'Evening song', 'artist': 'Unknown artist', 'album': 'Unknown album',
    }
    assert normalize_metadata({'title': '\x00 \n', 'artist': ''}, 'song.mp3')['title'] == 'song'


def test_tag_values_are_bounded_and_numeric_fields_validated():
    tags = normalize_metadata({
        'title': 'a' * 700, 'artist': 'Björk\n&\tFriends\x00', 'album': 'Live',
        'genre': 'Electronic', 'date': '2024-02-29', 'track': '03/12',
    }, 'ignored.mp3')
    assert len(tags['title']) == 512
    assert tags['artist'] == 'Björk & Friends'
    assert tags['date'] == '2024-02-29'
    assert tags['year'] == 2024
    assert tags['track'] == '3/12'
    invalid = normalize_metadata({'date': '2023-02-29', 'track': '../7', 'year': '-1'}, 'song.mp3')
    assert 'date' not in invalid and 'year' not in invalid and 'track' not in invalid


def test_ffmetadata_parser_respects_escapes_and_stops_before_chapter_tags():
    tags = parse_ffmetadata(';FFMETADATA1\ntitle=Night\\=Ops\\; first\\\nsecond\n'
                            'artist=Real artist\n[CHAPTER]\nartist=Chapter artist\n')
    assert tags['title'] == 'Night=Ops; first\nsecond'
    assert tags['artist'] == 'Real artist'


def test_metadata_output_has_a_hard_memory_bound():
    with pytest.raises(AudioError, match='processing limit'):
        transcode._run_bounded([sys.executable, '-c',
                               "import sys; sys.stdout.write('x'*1000000)"], stdout_limit=65536)
    with pytest.raises(ValueError, match='processing limit'):
        parse_ffmetadata(';FFMETADATA1\ntitle=' + 'x' * (256 * 1024))


def test_transfer_applies_normalized_tags_as_arguments(tmp_path, monkeypatch):
    source, output = tmp_path / 'opaque.flac', tmp_path / 'transfer.mp3'
    source.write_bytes(b'source')
    seen = []

    def run(cmd, **kwargs):
        seen.extend(cmd)
        output.write_bytes(b'converted')
        return subprocess.CompletedProcess(cmd, 0, stderr='')

    monkeypatch.setattr(transcode, '_ensure_ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(transcode, '_run_bounded', run)
    convert_managed(source, output, 'transfer', 1024 * 1024,
                    metadata=normalize_metadata({}, 'Evening song.flac'))
    assert 'title=Evening song' in seen
    assert 'artist=Unknown artist' in seen
    assert 'album=Unknown album' in seen
    assert 'track=' in seen
    assert 'date=' in seen


def test_artwork_process_is_bounded_and_failed_output_is_removed(tmp_path, monkeypatch):
    source, output = tmp_path / 'source.mp3', tmp_path / 'cover.jpg'
    source.write_bytes(b'source')
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, kwargs=kwargs)
        output.write_bytes(b'partial')
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(transcode, '_ensure_ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(transcode, '_run_bounded', run)
    with pytest.raises(AudioError):
        extract_cleared_artwork(source, output)
    assert not output.exists()
    assert seen['kwargs']['timeout'] <= 30
    assert 'file,pipe' in seen['cmd']
    assert '-map_metadata' in seen['cmd'] and '-1' in seen['cmd']
    assert '0:v:disp:attached_pic:0' in seen['cmd']
    assert '-max_pixels' in seen['cmd']


def test_artwork_over_limit_is_removed(tmp_path, monkeypatch):
    source, output = tmp_path / 'source.mp3', tmp_path / 'cover.jpg'
    source.write_bytes(b'source')

    def run(cmd, **kwargs):
        output.write_bytes(b'\xff\xd8' + b'x' * 1020 + b'\xff\xd9')
        return subprocess.CompletedProcess(cmd, 0, stderr='')

    monkeypatch.setattr(transcode, '_ensure_ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(transcode, '_run_bounded', run)
    with pytest.raises(AudioError, match='processing limit'):
        extract_cleared_artwork(source, output, max_bytes=1024)
    assert not output.exists()


def test_artwork_cannot_overwrite_source(tmp_path):
    source = tmp_path / 'source.mp3'
    source.write_bytes(b'original')
    with pytest.raises(AudioError):
        extract_cleared_artwork(source, source)
    assert source.read_bytes() == b'original'


@pytest.fixture
def real_ffmpeg():
    if os.environ.get('NIGHTOPS_REAL_MOCK') != '1':
        pytest.skip('opt-in real ffmpeg metadata and artwork round trips')
    return transcode._ensure_ffmpeg()


def _ffmpeg(binary, *args):
    result = subprocess.run([binary, '-nostdin', '-hide_banner', '-v', 'error', '-y', *map(str, args)],
                            capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')


def test_real_tagged_and_untagged_transfer_round_trip(tmp_path, real_ffmpeg):
    for tagged in (False, True):
        source = tmp_path / f'opaque-{tagged}.flac'
        output = tmp_path / f'transfer-{tagged}.mp3'
        args = ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.2']
        expected = {'title': 'Evening song', 'artist': 'Unknown artist', 'album': 'Unknown album'}
        if tagged:
            expected = {'title': '夕暮れ = Evening', 'artist': 'Björk', 'album': 'Night Ops',
                        'genre': 'Electronic', 'date': '2024-02-29', 'year': 2024, 'track': '3/12'}
            for key, value in expected.items():
                if key != 'year':
                    args += ['-metadata', f'{key}={value}']
        _ffmpeg(real_ffmpeg, *args, source)
        analyzed = analyze_cleared_audio(source)
        normalized = normalize_metadata(analyzed, 'Evening song.flac')
        assert {key: normalized[key] for key in expected} == expected
        convert_managed(source, output, 'transfer', 1024 * 1024, metadata=normalized)
        transferred = normalize_metadata(analyze_cleared_audio(output), 'transfer.mp3')
        assert {key: transferred[key] for key in expected} == expected


def test_real_artwork_is_resized_and_absence_is_optional(tmp_path, real_ffmpeg):
    cover = tmp_path / 'source-cover.png'
    source = tmp_path / 'with-cover.mp3'
    plain = tmp_path / 'without-cover.mp3'
    output = tmp_path / 'cover.jpg'
    _ffmpeg(real_ffmpeg, '-f', 'lavfi', '-i', 'color=c=red:size=1024x768', '-frames:v', 1, cover)
    _ffmpeg(real_ffmpeg, '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.2',
            '-i', cover, '-map', '0:a', '-map', '1:v', '-codec:a', 'libmp3lame',
            '-codec:v', 'copy', '-id3v2_version', '3', '-disposition:v:0', 'attached_pic', source)
    assert analyze_cleared_audio(source)['has_artwork'] is True
    assert extract_cleared_artwork(source, output) == output
    data = output.read_bytes()
    assert data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9')
    assert len(data) < 1024 * 1024
    # Independently let ffmpeg decode its derivative and report actual dimensions.
    decoded = subprocess.run([real_ffmpeg, '-hide_banner', '-i', str(output), '-f', 'null', '-'],
                             capture_output=True, timeout=30)
    assert decoded.returncode == 0
    assert b'512x384' in decoded.stderr
    _ffmpeg(real_ffmpeg, '-i', source, '-map', '0:a:0', '-vn', '-codec:a', 'copy', plain)
    assert analyze_cleared_audio(plain)['has_artwork'] is False
    absent = tmp_path / 'absent.jpg'
    assert extract_cleared_artwork(plain, absent) is None
    assert not absent.exists()


def test_real_multiline_tags_cannot_forge_artist_or_duration(tmp_path, real_ffmpeg):
    source = tmp_path / 'hostile-tags.flac'
    _ffmpeg(real_ffmpeg, '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.2',
            '-metadata', 'title=Hello\nartist: Forged\nDuration: 99:99:99.99',
            '-metadata', 'artist=Actual artist', source)
    analyzed = analyze_cleared_audio(source)
    assert analyzed['artist'] == 'Actual artist'
    assert analyzed['title'] == 'Hello\nartist: Forged\nDuration: 99:99:99.99'
    assert abs(analyzed['duration_seconds'] - 0.2) < 0.02

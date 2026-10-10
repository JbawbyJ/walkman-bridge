import math
import os
import struct
import sys
import wave
import pytest
from transcode import AudioError, _run_bounded, analyze_cleared_audio


def test_decoder_diagnostics_are_memory_bounded():
    with pytest.raises(AudioError, match='diagnostics'):
        _run_bounded([sys.executable, '-c', "import sys; sys.stderr.write('x'*1000000)"], output_limit=65536)


@pytest.mark.skipif(os.environ.get('NIGHTOPS_REAL_MOCK') != '1', reason='opt-in real ffmpeg analysis')
def test_real_loudness_measurement_tracks_signal_level(tmp_path):
    measured = []
    for level in (0.1, 0.2):
        path = tmp_path / f'{level}.wav'
        with wave.open(str(path), 'wb') as audio:
            audio.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
            audio.writeframes(b''.join(struct.pack('<hh', sample, sample) for sample in
                (int(32767 * level * math.sin(2 * math.pi * 1000 * i / 44100)) for i in range(88200))))
        analysis = analyze_cleared_audio(path)
        assert abs(analysis['duration_seconds'] - 2) < 0.02
        measured.append(analysis['integrated_lufs'])
    assert abs(measured[1] - measured[0] - 6.02) < 0.2

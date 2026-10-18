"""A done event alone is not evidence of a recovery result."""
import pytest

import jsymphonic
from test_jsymphonic import scripted


@pytest.mark.parametrize('call, event, mutating', [
    (jsymphonic.inspect_playlist_journal, None, False),
    (jsymphonic.inspect_playlist_journal, {'event': 'playlistJournal', 'state': 'future', 'files': []}, False),
    (jsymphonic.inspect_playlist_journal, {'event': 'playlistJournal', 'state': 'none', 'files': ['pending']}, False),
    (jsymphonic.recover_playlist_journal, None, True),
    (jsymphonic.recover_playlist_journal, {'event': 'playlistJournal', 'outcome': 'future'}, True),
    (jsymphonic.repair_playlists, None, True),
    (jsymphonic.repair_playlists, {'event': 'playlistRepair', 'prunedCount': True, 'prunedTrackIds': [], 'playlistIds': []}, True),
    (jsymphonic.repair_playlists, {'event': 'playlistRepair', 'prunedCount': 0, 'prunedTrackIds': [], 'playlistIds': ['bad']}, True),
])
def test_invalid_result_fails_closed(monkeypatch, call, event, mutating):
    scripted(monkeypatch, ([event] if event else []) + [{'event': 'done'}])
    with pytest.raises(jsymphonic.JSymphonicError) as error:
        call('fixture')
    assert error.value.needs_reconcile is mutating


def test_inspection_preserves_fingerprints_and_refuses_malformed_tokens(monkeypatch):
    event = {'event': 'playlistJournal', 'state': 'committed', 'files': ['01TREE22.DAT'],
             'fingerprint': 'a' * 64, 'library_fingerprint': 'b' * 64}
    scripted(monkeypatch, [event, {'event': 'done'}])
    result = jsymphonic.inspect_playlist_journal('fixture')
    assert result['guarded'] is True
    assert result['fingerprint'] == 'a' * 64
    assert result['library_fingerprint'] == 'b' * 64
    event['library_fingerprint'] = 'unknown'
    scripted(monkeypatch, [event, {'event': 'done'}])
    with pytest.raises(jsymphonic.JSymphonicError, match='fingerprint'):
        jsymphonic.inspect_playlist_journal('fixture')

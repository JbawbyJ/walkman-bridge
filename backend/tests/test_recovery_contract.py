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

import pytest

from test_api import context, imported


def test_queue_order_spans_multiple_import_batches(context):
    imported(context, tuple(f'track-{i}.wav' for i in range(200)))
    imported(context, ('last.wav',))
    before = context.client.get('/api/queue').json()['items']
    ids = [row['id'] for row in reversed(before)]
    response = context.client.patch('/api/queue/order', json={'ids': ids})
    assert response.status_code == 200, response.text
    assert [row['id'] for row in context.client.get('/api/queue').json()['items']] == ids
    assert context.client.patch('/api/queue/order', json={'ids': ids[:-1]}).status_code == 400
    assert context.client.patch('/api/queue/order', json={'ids': ids[:-1] + [ids[0]]}).status_code == 400


@pytest.mark.parametrize('suffix, mime', [('.opus', 'audio/ogg'), ('.wma', 'audio/x-ms-wma'), ('.aif', 'audio/aiff'), ('.aiff', 'audio/aiff')])
def test_advertised_formats_keep_extension_for_scanner(context, suffix, mime):
    job = imported(context, ('input' + suffix,))
    media_id = job['files'][0]['media_id']
    row = context.app.state.store.get(media_id)
    assert context.app.state.store.path(row).suffix == suffix
    assert row['mime'] == mime
    assert context.scanner.calls[0].suffix == suffix

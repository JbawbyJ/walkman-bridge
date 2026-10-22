"""Local fixtures exercise real codecs and a fresh synthetic Sony volume only."""
import io
import hashlib
import os
from pathlib import Path

import pytest

from media_store import MediaStore
from media_store import MediaError, BlockedBytesRemoved
from media_service import MediaService
from test_metadata import _ffmpeg, real_ffmpeg
from test_metadata_device_roundtrip import real_context, evidence, _import, _transfer, _text_frames
from transcode import analyze_cleared_audio
import link_import


def test_mp4_keeps_recognized_container_in_managed_storage(tmp_path):
    store = MediaStore(tmp_path/'db.sqlite', tmp_path/'cache')
    record = store.import_file('Track.mp4', io.BytesIO(b'opaque fixture'))
    assert store.path(record).suffix == '.mp4'
    assert record['mime'] == 'video/mp4'


def test_artwork_copy_collision_preserves_existing_bytes(tmp_path):
    store = MediaStore(tmp_path/'db.sqlite', tmp_path/'cache')
    record = store.import_file('Track.mp4', io.BytesIO(b'opaque fixture'))
    store.import_artwork_source(record['id'], '.png', io.BytesIO(b'original'))
    with pytest.raises(FileExistsError):
        store.import_artwork_source(record['id'], '.png', io.BytesIO(b'replacement'))
    assert store.path(store.get(record['id']), 'artwork_source').read_bytes() == b'original'


@pytest.mark.parametrize('artifact', ['artwork', 'artwork_source'])
def test_artwork_threat_refuses_all_embedded_derivatives(tmp_path, artifact):
    store = MediaStore(tmp_path/'db.sqlite', tmp_path/'cache')
    record = store.import_file('track.mp3', io.BytesIO(b'audio'))
    for name, filename in [('artwork', 'artwork.jpg'), ('artwork_source', 'artwork-source.png'),
                           ('playback', 'playback.flac'), ('transfer', 'transfer.mp3')]:
        (store.root/record['id']/filename).write_bytes(b'opaque')
        record['artifacts'][name] = dict(name=filename, size_bytes=6, sha256='0'*64, scan=None)
    store.update(record['id'], artifacts=record['artifacts'], artwork_status='ready', playback='playback')
    service = MediaService(store, None, None, None, None)
    with pytest.raises(MediaError):
        service.accept_scan(record['id'], artifact, dict(ok=False, state='THREAT', reason_code='defender_threat'))
    record = store.get(record['id'])
    assert record['playback'] == 'source'
    for name in (artifact, 'artwork', 'playback', 'transfer'):
        with pytest.raises(BlockedBytesRemoved):
            store.path(record, name)
    assert 'artwork_url' not in store.public(record)


@pytest.mark.skipif(os.environ.get('NIGHTOPS_REAL_MOCK') != '1', reason='real codecs and synthetic Sony volume')
def test_tagged_mp4_cover_survives_storage_playback_and_sony_text(real_context, evidence):
    c = real_context
    cover, source = c.tmp/'cover.png', c.tmp/'tagged.mp4'
    _ffmpeg(c.ffmpeg, '-f', 'lavfi', '-i', 'color=c=red:size=64x64', '-frames:v', 1, cover)
    tags = {'title': '\u591c\u306e\u6b4c', 'artist': '\u6f14\u594f\u8005', 'album': '\u65e5\u672c\u8a9e Album', 'genre': 'Ambient'}
    options = [v for key,value in tags.items() for v in ('-metadata',f'{key}={value}')]
    _ffmpeg(c.ffmpeg, '-f','lavfi','-i','sine=frequency=523:duration=0.3','-i',cover,
            '-map','0:a','-map','1:v','-c:a','aac','-c:v','copy','-disposition:v','attached_pic',*options,source)
    original = source.read_bytes()
    media_id, item = _import(c, 'Wrong filename.mp4', original)
    assert {k:item[k] for k in tags} == tags
    assert item['metadata_provenance'] == {k:'embedded' for k in tags}
    assert item['artwork_url'] == f'/api/media/{media_id}/artwork'
    art = c.client.get(item['artwork_url'])
    assert art.status_code == 200 and art.content.startswith(b'\xff\xd8')
    prepared = c.client.post(f'/api/media/{media_id}/prepare',json={'format':'flac'})
    assert c.client.get('/api/jobs/'+prepared.json()['job_id']).json()['status'] == 'done'
    store=c.app.state.store
    record=store.get(media_id)
    playback=store.path(record,'playback')
    assert analyze_cleared_audio(playback)['has_artwork'] is True
    track,transfer,oma=_transfer(c,media_id)
    assert {key:track[key] for key in tags} == tags
    assert analyze_cleared_audio(transfer)['has_artwork'] is True
    for frame,key in [('TIT2','title'),('TPE1','artist'),('TALB','album'),('TCON','genre')]:
        assert _text_frames(transfer)[frame] == _text_frames(oma)[frame] == [tags[key]]
        assert tags[key].encode('utf-16-be') in (c.mount/'OMGAUDIO/04CNTINF.DAT').read_bytes()
    assert store.path(record).read_bytes() == original
    evidence.append({'test':'mp4_cover_text_pipeline','passed':True,'hardware_artwork':'not verified; no native jacket write'})


@pytest.mark.skipif(os.environ.get('NIGHTOPS_REAL_MOCK') != '1', reason='real codecs and synthetic Sony volume')
def test_link_thumbnail_recovery_and_user_edits_survive_transfer(real_context, monkeypatch, evidence):
    c=real_context
    audio,image=c.tmp/'untagged.m4a',c.tmp/'thumb.png'
    _ffmpeg(c.ffmpeg,'-f','lavfi','-i','sine=frequency=523:duration=0.2','-c:a','aac',audio)
    _ffmpeg(c.ffmpeg,'-f','lavfi','-i','color=c=blue:size=80x60','-frames:v',1,image)
    tags={'title':'\u591c\u306e\u6b4c','artist':'Actual recording artist','album':'Source album','genre':'Ambient'}
    def download(url,folder,maximum,progress=None):
        path,picture=folder/'audio.m4a',folder/'thumbnail.png'
        path.write_bytes(audio.read_bytes());picture.write_bytes(image.read_bytes())
        return link_import.DownloadedMedia(path,'Provider title.m4a',
            {**tags,'metadata_provenance':{k:'provider.youtube.'+k for k in tags},
             'artwork_provenance':'provider.youtube.thumbnail'},artwork_path=picture)
    monkeypatch.setattr(link_import,'download_link',download)
    response=c.client.post('/api/media/import-link',json={'url':'https://youtu.be/dQw4w9WgXcQ'})
    job=c.client.get('/api/jobs/'+response.json()['job_id']).json()
    assert job['status']=='done',job
    media_id=job['files'][0]['media_id'];store=c.app.state.store
    item=c.client.get('/api/queue').json()['items'][0]
    assert {k:item[k] for k in tags}==tags
    assert item['artwork_provenance']=='provider.youtube.thumbnail'
    assert c.client.get(item['artwork_url']).status_code==200
    original_raw=store.path(store.get(media_id),'artwork_source').read_bytes()
    edit=c.client.patch(f'/api/media/{media_id}/metadata',json={'title':'\u624b\u52d5 title','artist':'My artist','genre':''})
    assert edit.status_code==200,edit.text
    record=store.get(media_id)
    # Simulate a previous policy plus a missing derivative: retain original raw thumbnail for recovery.
    store.path(record,'artwork').unlink()  # Recover even when the SQLite artifact entry remains.
    store.update(media_id,artifacts=record['artifacts'],metadata_policy=3,artwork_status='failed')
    restarted=MediaStore(c.tmp/'state'/'nightops.sqlite',store.root)
    assert restarted.get(media_id)['metadata_overrides']['artist']=='My artist'
    restarted._db.close()
    assert store.path(store.get(media_id),'artwork_source').read_bytes()==original_raw
    track,transfer,oma=_transfer(c,media_id)
    item=store.public(store.get(media_id))
    assert item['title']=='\u624b\u52d5 title' and item['artist']=='My artist' and item['genre'] is None
    assert item['metadata_provenance']['title']==item['metadata_provenance']['genre']=='user_edit'
    assert item['metadata_provenance']['album']=='provider.youtube.album'
    assert item['artwork_provenance']=='provider.youtube.thumbnail'
    assert c.client.get(item['artwork_url']).status_code==200
    assert analyze_cleared_audio(transfer)['has_artwork'] is True
    assert _text_frames(oma)['TIT2']==['\u624b\u52d5 title']
    assert _text_frames(oma)['TPE1']==['My artist']
    assert store.path(store.get(media_id),'artwork_source').read_bytes()==original_raw
    evidence.append({'test':'link_thumbnail_recovery_user_edits','passed':True,'provider':'fixture only','scanner':'injected'})


@pytest.mark.skipif(os.environ.get('NIGHTOPS_REAL_MOCK') != '1', reason='real codecs')
def test_mp4_video_frame_is_not_invented_album_art(real_context):
    c=real_context;source=c.tmp/'video.mp4'
    _ffmpeg(c.ffmpeg,'-f','lavfi','-i','color=c=red:size=32x32:duration=0.2',
            '-f','lavfi','-i','sine=frequency=440:duration=0.2','-c:v','mpeg4','-c:a','aac',source)
    _,item=_import(c,'Untitled video.mp4',source.read_bytes())
    assert item['title']=='Untitled video' and item['artist']=='Unknown artist' and item['album']=='Unknown album'
    assert item['genre'] is None and not item.get('artwork_url')
    assert item['metadata_provenance']=={'title':'filename','artist':'missing','album':'missing'}


@pytest.mark.skipif(os.environ.get('NIGHTOPS_REAL_MOCK') != '1', reason='real codecs and synthetic Sony volume')
def test_cleared_tags_remain_absent_after_restart_analysis_and_sony_transfer(real_context):
    c=real_context;source=c.tmp/'tagged.m4a'
    _ffmpeg(c.ffmpeg,'-f','lavfi','-i','sine=duration=0.2','-c:a','aac',
            '-metadata','artist=Original artist','-metadata','album=Original album',source)
    media_id,_=_import(c,'Cleared.m4a',source.read_bytes())
    assert c.client.patch(f'/api/media/{media_id}/metadata',json={'artist':'','album':''}).status_code == 200
    store=c.app.state.store
    assert store.get(media_id)['metadata_overrides'] == {'artist':None,'album':None}
    store.update(media_id,metadata_policy=3)
    reopened=MediaStore(c.tmp/'state'/'nightops.sqlite',store.root)
    assert reopened.get(media_id)['metadata_overrides'] == {'artist':None,'album':None}
    reopened._db.close()
    track,transfer,oma=_transfer(c,media_id)
    assert track['artist'] == track['album'] == track['genre'] == ''
    for frame in ('TPE1','TALB','TCON'):
        assert frame not in _text_frames(transfer)
        assert _text_frames(oma).get(frame,['']) == ['']


@pytest.mark.skipif(os.environ.get('NIGHTOPS_REAL_MOCK') != '1', reason='real codecs')
def test_recovered_provider_artwork_refreshes_existing_playback(real_context):
    c=real_context;source=c.tmp/'source.m4a';image=c.tmp/'cover.png'
    _ffmpeg(c.ffmpeg,'-f','lavfi','-i','sine=duration=0.2','-c:a','aac',source)
    _ffmpeg(c.ffmpeg,'-f','lavfi','-i','color=c=green:size=32x32','-frames:v',1,image)
    media_id,_=_import(c,'Song.m4a',source.read_bytes())
    def prepare():
        result=c.client.post(f'/api/media/{media_id}/prepare',json={'format':'flac'})
        job=c.client.get('/api/jobs/'+result.json()['job_id']).json()
        assert job['status']=='done',job
    prepare();store=c.app.state.store
    before=store.get(media_id)['artifacts']['playback']['sha256']
    with image.open('rb') as stream:
        store.import_artwork_source(media_id,'.png',stream)
    store.update(media_id,provider_artwork_provenance='provider.youtube.thumbnail')
    prepare();record=store.get(media_id)
    assert record['artifacts']['playback']['sha256'] != before
    assert record['artifacts']['playback']['artwork_sha256'] == record['artifacts']['artwork']['sha256']
    assert analyze_cleared_audio(store.path(record,'playback'))['has_artwork']
    # A missing file can leave a committed SQLite artifact entry behind.
    store.path(record,'playback').unlink()
    prepare()
    assert analyze_cleared_audio(store.path(store.get(media_id),'playback'))['has_artwork']

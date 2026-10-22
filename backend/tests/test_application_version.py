"""The UI health label follows the release's canonical package metadata."""
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import application


@pytest.mark.parametrize('product', ['bridge', 'player'])
def test_health_and_openapi_report_canonical_package_version(tmp_path, product):
    expected = json.loads((Path(__file__).resolve().parents[2] / 'package.json').read_text())['version']
    origin = 'http://127.0.0.1:8765'
    app = application.create_app(product=product, data_dir=tmp_path, token='version-test',
                                 origin=origin, scanner=SimpleNamespace(),
                                 device_api=SimpleNamespace(find_walkman=lambda: None))
    with TestClient(app, base_url=origin, headers={'X-NightOps-Token': 'version-test'}) as client:
        response = client.get('/api/health')
        assert response.status_code == 200
        assert response.json()['version'] == expected
        assert app.openapi()['info']['version'] == expected


def test_packaged_version_uses_staged_manifest(tmp_path):
    (tmp_path / 'product.json').write_text('{"product": "bridge"}')
    (tmp_path / 'package.json').write_text('{"version": "0.1.0"}')
    (tmp_path / 'runtime-manifest.json').write_text('{"version": "1.2.3-rc.4"}')
    assert application.application_version(tmp_path) == '1.2.3-rc.4'


def test_development_version_ignores_leftover_manifest(tmp_path):
    (tmp_path / 'package.json').write_text('{"version": "2.3.4"}')
    (tmp_path / 'runtime-manifest.json').write_text('{"version": "0.1.0"}')
    assert application.application_version(tmp_path) == '2.3.4'


@pytest.mark.parametrize('contents', [None, 'invalid json', '{}', '[]', '{"version": 42}', '{"version": ""}'])
def test_packaged_identity_fails_clearly_without_valid_manifest(tmp_path, contents):
    (tmp_path / 'product.json').write_text('{"product": "bridge"}')
    (tmp_path / 'package.json').write_text('{"version": "0.1.0"}')
    if contents is not None:
        (tmp_path / 'runtime-manifest.json').write_text(contents)
    with pytest.raises(RuntimeError, match='application version'):
        application.application_version(tmp_path)

"""WSC evidence must never invent Defender runtime settings or accept ambiguity."""
import importlib
import importlib.util

import pytest


DEFENDER = {
    'name': 'Microsoft Defender Antivirus', 'state': 0, 'signature_status': 1,
    'remediation': 'windowsdefender://', 'timestamp': 'Thu, 03 Sep 2026 06:18:48 GMT',
}


def reader():
    assert importlib.util.find_spec('defender_status') is not None, 'WSC status reader is missing'
    return importlib.import_module('defender_status')


def query(monkeypatch, products):
    module = reader()
    monkeypatch.setattr(module, '_read_products', lambda: products)
    return module.query_defender_status()


def test_on_current_defender_reports_only_wsc_evidence(monkeypatch):
    status = query(monkeypatch, [DEFENDER])
    assert status['available'] is True
    assert status['product_state'] == 'ON'
    assert status['signature_status'] == 'UP_TO_DATE'
    assert status['identity'] == {'kind': 'wsc_remediation_uri', 'value': 'windowsdefender://'}
    assert status['state_timestamp'] == DEFENDER['timestamp']
    assert not {'active', 'real_time_protection', 'running_mode'} & status.keys()


@pytest.mark.parametrize(('state', 'expected'), [(1, 'OFF'), (2, 'SNOOZED'), (3, 'EXPIRED')])
def test_inactive_defender_is_reported_honestly(monkeypatch, state, expected):
    status = query(monkeypatch, [{**DEFENDER, 'state': state}])
    assert status['available'] is True
    assert status['product_state'] == expected
    assert status['product_state'] != 'ON'


def test_outdated_signatures_are_not_current(monkeypatch):
    status = query(monkeypatch, [{**DEFENDER, 'signature_status': 0}])
    assert status['signature_status'] == 'OUT_OF_DATE'


@pytest.mark.parametrize(('field', 'value'), [
    ('state', -1), ('state', 4), ('state', 'Passive'), ('state', None), ('state', True),
    ('signature_status', 2), ('signature_status', None), ('signature_status', '1'),
    ('signature_status', True), ('name', ''), ('timestamp', None),
])
def test_unknown_or_malformed_evidence_fails_closed(monkeypatch, field, value):
    assert query(monkeypatch, [{**DEFENDER, field: value}])['available'] is False


@pytest.mark.parametrize('products', [
    [], [{**DEFENDER, 'remediation': 'https://example.invalid'}],
    [{**DEFENDER, 'remediation': 'windowsdefender://evil'}],
    [{**DEFENDER, 'name': 'Another AV', 'remediation': 'otherav://'}],
    [DEFENDER, dict(DEFENDER)], [DEFENDER, {**DEFENDER, 'state': 1}],
    [DEFENDER, {}], None,
])
def test_missing_ambiguous_or_conflicting_identity_fails_closed(monkeypatch, products):
    assert query(monkeypatch, products)['available'] is False


def test_localized_name_does_not_replace_provider_identity(monkeypatch):
    status = query(monkeypatch, [{**DEFENDER, 'name': 'Antivirus Microsoft Defender localise'}])
    assert status['available'] is True


def test_native_failure_returns_unavailable_without_partial_clearance(monkeypatch):
    module = reader()
    def denied():
        raise OSError('access denied')
    monkeypatch.setattr(module, '_read_products', denied)
    status = module.query_defender_status()
    assert status['available'] is False
    assert 'product_state' not in status


class FakeCom:
    """Native COM boundary; ownership errors are observable as live resources."""
    def __init__(self, failure=None, initialization=0):
        self.failure, self.initialization = failure, initialization
        self.live = set()
        self.uninitialized = 0

    def initialize(self):
        return self.initialization

    def uninitialize(self):
        self.uninitialized += 1

    def create_list(self):
        self.live.add('list')
        return 'list'

    def initialize_list(self, pointer):
        if self.failure == 'initialize_list':
            raise OSError('list initialization failed')

    def count(self, pointer):
        return 1

    def item(self, pointer, index):
        self.live.add('product')
        return 'product'

    def product(self, pointer):
        if self.failure == 'product':
            raise OSError('state query failed')
        return dict(DEFENDER)

    def release(self, pointer):
        self.live.remove(pointer)


@pytest.mark.parametrize('failure', [None, 'initialize_list', 'product'])
def test_com_resources_released_on_success_and_failure(monkeypatch, failure):
    module = reader()
    native = FakeCom(failure)
    monkeypatch.setattr(module, '_supported_platform', lambda: True)
    monkeypatch.setattr(module, '_ComApi', lambda: native)
    status = module.query_defender_status()
    assert status['available'] is (failure is None)
    assert native.live == set()
    assert native.uninitialized == 1


@pytest.mark.parametrize(('initialization', 'uninitializations'), [(1, 1), (-2147417850, 0)])
def test_existing_com_apartment_is_balanced(monkeypatch, initialization, uninitializations):
    module = reader()
    native = FakeCom(initialization=initialization)
    monkeypatch.setattr(module, '_supported_platform', lambda: True)
    monkeypatch.setattr(module, '_ComApi', lambda: native)
    assert module.query_defender_status()['available'] is True
    assert native.live == set()
    assert native.uninitialized == uninitializations


def test_unsupported_platform_does_not_load_native_code(monkeypatch):
    module = reader()
    monkeypatch.setattr(module, '_supported_platform', lambda: False)
    def forbidden():
        pytest.fail('Native reader was loaded on unsupported platform')
    monkeypatch.setattr(module, '_ComApi', forbidden)
    assert module.query_defender_status()['available'] is False

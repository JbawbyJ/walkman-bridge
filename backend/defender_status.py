"""Read Defender's reported Windows Security Center state without elevation.

WSC ON means the product reports that it is protecting the user. It does not
expose Defender's AMRunningMode or RealTimeProtectionEnabled; never invent those
fields from this evidence. Callers must require ON and UP_TO_DATE separately
from ``available`` and independently bind current engine/signature versions.

API contract: https://learn.microsoft.com/windows/win32/api/iwscapi/
ABI: Microsoft's win32metadata generation/WinSDK/RecompiledIdlHeaders/um/iwscapi.h
The SDK interfaces inherit IDispatch: their methods start at vtable slot 7.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
import uuid


SOURCE = 'windows_security_center'
_DEFENDER_URI = 'windowsdefender://'
_STATES = {0: 'ON', 1: 'OFF', 2: 'SNOOZED', 3: 'EXPIRED'}
_SIGNATURES = {0: 'OUT_OF_DATE', 1: 'UP_TO_DATE'}
_RPC_E_CHANGED_MODE = -2147417850


class WscError(OSError):
    pass


def query_defender_status() -> dict:
    """Return fresh WSC evidence, or an unavailable result on any ambiguity.

The exact built-in remediation URI identifies Defender without relying on a
localized display name or a third-party product's healthy aggregate status.
Never execute the returned URI. WSC's timestamp denotes the last state change,
not a heartbeat: an old timestamp does not itself make a fresh query stale.
"""
    try:
        products = _read_products()
        if not isinstance(products, list) or len(products) > 32:
            raise WscError('invalid product list')
        matches = []
        for product in products:
            if not isinstance(product, dict) or not all(
                isinstance(product.get(key), str) and 0 < len(product[key]) <= 4096
                for key in ('name', 'remediation', 'timestamp')
            ):
                raise WscError('incomplete product information')
            state, signature = product.get('state'), product.get('signature_status')
            if (type(state) is not int or state not in _STATES
                    or type(signature) is not int or signature not in _SIGNATURES):
                raise WscError('unknown product state')
            if product['remediation'].casefold() == _DEFENDER_URI:
                matches.append(product)
        if len(matches) != 1:
            return {'available': False, 'source': SOURCE,
                    'reason_code': 'wsc_defender_missing' if not matches else 'wsc_defender_ambiguous'}
        product = matches[0]
        return {
            'available': True, 'source': SOURCE,
            'product_name': product['name'],
            'product_state': _STATES[product['state']],
            'signature_status': _SIGNATURES[product['signature_status']],
            'identity': {'kind': 'wsc_remediation_uri', 'value': _DEFENDER_URI},
            'state_timestamp': product['timestamp'],
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return {'available': False, 'source': SOURCE, 'reason_code': 'wsc_status_unavailable'}


def _supported_platform() -> bool:
    # WSC is a Windows client API; Microsoft documents no Windows Server support.
    if sys.platform != 'win32' or ctypes.sizeof(ctypes.c_void_p) != 8:
        return False
    version = sys.getwindowsversion()
    return version.product_type == 1 and (version.major, version.minor) >= (6, 2)


def _read_products() -> list[dict]:
    if not _supported_platform():
        raise WscError('unsupported WSC platform')
    api = _ComApi()
    initialized = False
    product_list = None
    try:
        result = api.initialize()
        if result in (0, 1):
            initialized = True  # S_FALSE still increments COM's initialization count.
        elif result != _RPC_E_CHANGED_MODE:
            raise WscError('COM initialization failed')
        product_list = api.create_list()
        api.initialize_list(product_list)
        count = api.count(product_list)
        if not 0 <= count <= 32:
            raise WscError('invalid WSC product count')
        products = []
        for index in range(count):
            product = api.item(product_list, index)
            try:
                products.append(api.product(product))
            finally:
                api.release(product)
        return products
    finally:
        try:
            if product_list is not None:
                api.release(product_list)
        finally:
            if initialized:
                api.uninitialize()


class _Guid(ctypes.Structure):
    _fields_ = [('data', ctypes.c_ubyte * 16)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _ComApi:
    """Minimal typed native boundary; no PowerShell, WMI, or module search."""
    def __init__(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        kernel.GetSystemDirectoryW.restype = wintypes.UINT
        directory = ctypes.create_unicode_buffer(32768)
        length = kernel.GetSystemDirectoryW(directory, len(directory))
        if not length or length >= len(directory):
            raise WscError('system directory unavailable')
        self.ole = ctypes.WinDLL(os.path.join(directory.value, 'ole32.dll'))
        self.automation = ctypes.WinDLL(os.path.join(directory.value, 'oleaut32.dll'))
        self.ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        self.ole.CoInitializeEx.restype = ctypes.c_long
        self.ole.CoUninitialize.argtypes = []
        self.ole.CoUninitialize.restype = None
        self.ole.CoCreateInstance.argtypes = [ctypes.POINTER(_Guid), ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)]
        self.ole.CoCreateInstance.restype = ctypes.c_long
        self.automation.SysStringLen.argtypes = [ctypes.c_void_p]
        self.automation.SysStringLen.restype = wintypes.UINT
        self.automation.SysFreeString.argtypes = [ctypes.c_void_p]
        self.automation.SysFreeString.restype = None

    @staticmethod
    def _check(result):
        if result != 0:
            raise WscError(f'WSC failed with HRESULT 0x{result & 0xffffffff:08X}')

    @staticmethod
    def _call(pointer, index, *arguments):
        if not pointer or not pointer.value:
            raise WscError('empty COM interface')
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *arguments)(table[index])

    def initialize(self):
        return self.ole.CoInitializeEx(None, 2)

    def uninitialize(self):
        self.ole.CoUninitialize()

    def release(self, pointer):
        self._call(pointer, 2)(pointer)

    def create_list(self):
        pointer = ctypes.c_void_p()
        clsid = _Guid.parse('17072F7B-9ABE-4A74-A261-1EB76B55107A')
        iid = _Guid.parse('722A338C-6E8E-4E72-AC27-1417FB0C81C2')
        try:
            self._check(self.ole.CoCreateInstance(ctypes.byref(clsid), None, 1,
                ctypes.byref(iid), ctypes.byref(pointer)))
            if not pointer.value:
                raise WscError('empty WSC product list')
            return pointer
        except BaseException:
            if pointer.value:
                self.release(pointer)
            raise

    def initialize_list(self, pointer):
        self._check(self._call(pointer, 7, wintypes.ULONG)(pointer, 4))

    def _integer(self, pointer, index):
        value = wintypes.LONG()
        self._check(self._call(pointer, index, ctypes.POINTER(wintypes.LONG))(
            pointer, ctypes.byref(value)))
        return value.value

    def _string(self, pointer, index):
        value = ctypes.c_void_p()
        try:
            self._check(self._call(pointer, index, ctypes.POINTER(ctypes.c_void_p))(
                pointer, ctypes.byref(value)))
            length = self.automation.SysStringLen(value)
            if not value.value or not 0 < length <= 4096:
                raise WscError('invalid WSC string')
            return ctypes.wstring_at(value, length)
        finally:
            if value.value:
                self.automation.SysFreeString(value)

    def count(self, pointer):
        return self._integer(pointer, 8)

    def item(self, pointer, index):
        product = ctypes.c_void_p()
        try:
            self._check(self._call(pointer, 9, wintypes.ULONG, ctypes.POINTER(ctypes.c_void_p))(
                pointer, index, ctypes.byref(product)))
            if not product.value:
                raise WscError('empty WSC product')
            return product
        except BaseException:
            if product.value:
                self.release(product)
            raise

    def product(self, pointer):
        return {'name': self._string(pointer, 7), 'state': self._integer(pointer, 8),
                'signature_status': self._integer(pointer, 9),
                'remediation': self._string(pointer, 10), 'timestamp': self._string(pointer, 11)}

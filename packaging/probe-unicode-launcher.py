"""Record how Windows java.exe treats paths outside the ANSI code page.

Evidence for the launch rules in backend/jsymphonic.py. Each case runs the
real launcher on disposable folders and records its exit code and output.
Launcher outcomes are reported, never asserted: the backend tests assert
product behavior.
"""
import argparse
import base64
import ctypes
import json
import locale
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

MAIN = 'org.naurd.media.jsymphonic.headless.HeadlessCli'
# Cyrillic and CJK: outside cp1252, the code page of the hosted Windows runners.
NAME = 'Профиль 用户'


def short_path(path):
    get_short = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
    get_short.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
    get_short.restype = ctypes.c_uint32
    size = get_short(str(path), None, 0)
    buffer = ctypes.create_unicode_buffer(size or 1)
    return buffer.value if size and 0 < get_short(str(path), buffer, size) < size else None


def round_trips(text):
    try:
        return text.encode('mbcs', 'strict').decode('mbcs', 'strict') == text
    except UnicodeError:
        return False


def run(command, cwd=None):
    if any(part is None for part in command):
        return {'skipped': 'path unavailable (no 8.3 short name or no junction)'}
    try:
        result = subprocess.run([str(part) for part in command], cwd=cwd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=60,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return {'error': f'{type(exc).__name__}: {exc}'}
    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return {'exit': result.returncode,
            'stderr': [line for line in result.stderr.splitlines() if line.strip()][:3],
            'fatal': [event.get('message') for event in events if isinstance(event, dict) and event.get('event') == 'fatal']}


def probe(base, java, jar):
    root = Path(tempfile.mkdtemp(prefix='launcher-', dir=base)) / NAME
    root.mkdir()
    try:
        unicode_jar = root / 'jsymphonic.jar'
        shutil.copyfile(jar, unicode_jar)
        runtime = root / 'jre'
        import _winapi
        try:
            _winapi.CreateJunction(str(Path(java).resolve().parent.parent), str(runtime))
            runtime_java = runtime / 'bin' / 'java.exe'
        except OSError:
            runtime_java = None
        mount = root.parent / 'mount'
        (mount / 'OMGAUDIO').mkdir(parents=True)
        # Unsupported on purpose: the shim rejects it before it opens the device.
        unicode_input = root / 'input.txt'
        unicode_input.write_bytes(b'not audio')
        argfile = root.parent / 'classpath.txt'
        argfile.write_text('-cp "' + str(unicode_jar).replace('\\', '\\\\') + '"\n', encoding='utf-8')
        engine = [java, '-cp', jar, MAIN]
        cases = {
            # No command: a started engine prints usage and exits 2.
            'classpath_long': run([java, '-cp', unicode_jar, MAIN]),
            'classpath_short': run([java, '-cp', short_path(unicode_jar), MAIN]),
            'classpath_relative_cwd': run([java, '-cp', unicode_jar.name, MAIN], cwd=root),
            'classpath_argfile_utf8': run([java, '@' + str(argfile), MAIN]),
            # The engine class, not -version: from an 8.3 path -version succeeds
            # while loading the engine still fails (Windows CI, Temurin 21.0.12).
            'runtime_long': run([runtime_java, '-cp', jar, MAIN]),
            'runtime_short': run([runtime_java and short_path(runtime_java), '-cp', jar, MAIN]),
            'argument_long': run([*engine, 'add', '--device', mount, unicode_input]),
            'argument_base64': run([*engine, 'add', '--device', mount, '--file-base64',
                                    base64.b64encode(str(unicode_input).encode('utf-8')).decode('ascii')]),
        }
        return {'base': str(base), 'folder': str(root), 'folder_short': short_path(root), 'cases': cases}
    finally:
        runtime = root / 'jre'
        if runtime.exists():
            os.rmdir(runtime)  # Removes the junction only, never the JDK behind it.
        shutil.rmtree(root.parent, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--java', required=True, help='java.exe from a folder inside the code page')
    parser.add_argument('--jar', type=Path, required=True)
    parser.add_argument('--base', type=Path, action='append', help='parent folder to probe (repeatable)')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        sys.exit('This probe records Windows launcher behavior and runs only on Windows')
    report = {'acp': ctypes.windll.kernel32.GetACP(), 'encoding': locale.getencoding(),
              'name': NAME, 'name_round_trips': round_trips(NAME),
              'probes': [probe(base, args.java, args.jar.resolve()) for base in (args.base or [Path(tempfile.gettempdir())])]}
    # The console uses the ANSI code page, so the log copy escapes non-ASCII text.
    print(json.dumps(report, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

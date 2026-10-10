"""Verify installed Python imports, job schema and per-product data isolation.

Injects a no-device boundary in test code; never discovers physical hardware.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

CODE = r'''
import json, os, sqlite3, sys
from pathlib import Path
from types import SimpleNamespace
from main import create_app
import yt_dlp, yt_dlp_ejs
from link_import import _fixed_deno_path
assert _fixed_deno_path().parent.name == 'deno'
product = os.environ['NIGHTOPS_PRODUCT']
data = Path(os.environ['NIGHTOPS_DATA_DIR'])
app = create_app(product=product, data_dir=data,
    token='packaging-probe-' + 'x' * 48, origin='http://127.0.0.1:45678',
    device_api=SimpleNamespace(find_walkman=lambda: None) if product == 'bridge' else None)
assert Path(app.state.jobs._db_path).resolve() == (data / 'nightops.sqlite').resolve()
paths = {route.path for route in app.routes}
assert '/api/media/import-link' in paths
assert '/api/media/{media_id}/artwork' in paths
assert '/api/playlists' in paths and '/api/media/{media_id}/metadata' in paths
if product == 'player':
    assert 'device' not in sys.modules and 'jsymphonic' not in sys.modules
    assert '/api/transfers' not in paths and '/api/tracks/{track_id}' not in paths
    assert '/api/device/playlists' not in paths
else:
    assert '/api/device/playlists' in paths
job = app.state.jobs.create('packaged-probe', 1, kind='import')
job.set_files([{'file_id':'probe', 'name':'probe.wav', 'state':'queued'}], phase='queued')
result = job.to_dict()
assert {'job_id','status','total_files','progress','message','log_tail','kind','phase','files','needs_reconcile'} <= result.keys()
with sqlite3.connect(data / 'nightops.sqlite') as db:
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
assert 'jobs' in tables
app.state.jobs.close()
print(json.dumps({'product':product,'python':sys.version.split()[0], 'database':str(data / 'nightops.sqlite'), 'tables':tables, 'device_modules_loaded':'device' in sys.modules, 'passed':True}))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("resources", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    resources = args.resources.resolve()
    product = json.loads((resources / "product.json").read_text())["product"]
    with tempfile.TemporaryDirectory(prefix=f"nightops-packaged-{product}-") as temporary:
        env = dict(os.environ, NIGHTOPS_PRODUCT=product, NIGHTOPS_DATA_DIR=temporary,
                   WALKMAN_JOBS_DB=str(Path(temporary) / "nightops.sqlite"))
        result = subprocess.run([str(resources / "python" / "python.exe"), "-I", "-B", "-c", CODE],
            cwd=resources, env=env, text=True, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        report = json.loads(result.stdout)
    if list(resources.rglob("*.sqlite")):
        raise RuntimeError("Application state was written under the installed resources")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Verify native loading and the Streamlit landing page inside the built image.

Run with no host SDK mounts and no network. Scientific result equivalence and
browser-side visualization are separate checks.
"""
import json
from pathlib import Path
import platform
import subprocess
import tempfile
import time


def main():
    started = time.perf_counter()
    import pyopenms
    provenance = json.loads((Path(pyopenms.__file__).parent / '_build_provenance.json').read_text())
    runtime = json.loads(Path('/opt/openms4/share/openms4/runtime-provenance.json').read_text())
    receipt = json.loads(Path('/opt/openms4/share/openms4/assembly-receipt.json').read_text())
    assert provenance['core'] == runtime['core'], 'Runtime and Python Core identities differ'
    assert not any(Path(record['source']).exists() for record in receipt['files'].values()), 'Builder files are accessible'
    checks = {}
    with tempfile.TemporaryDirectory() as temporary:
        for name in runtime['executables']:
            tool_started = time.perf_counter()
            executable = '/opt/openms4/bin/' + name
            output = subprocess.check_output([executable, '--help'], text=True, stderr=subprocess.STDOUT)
            assert name in output
            ini = Path(temporary) / (name + '.ini')
            subprocess.run([executable, '-write_ini', str(ini)], check=True, stdout=subprocess.DEVNULL)
            assert ini.stat().st_size > 0
            libraries = subprocess.check_output(['ldd', executable], text=True)
            assert 'not found' not in libraries and '/scratch/' not in libraries
            checks[name] = round(time.perf_counter() - tool_started, 3)
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file('/app/app.py').run(timeout=90)
    assert not app.exception, str(app.exception)
    report = {'python': platform.python_version(), 'machine': platform.machine(), 'libc': platform.libc_ver(),
              'pyopenms_source': provenance['source_revision'], 'runtime_core_source': runtime['core']['source_revision'],
              'core_metadata_matches': True, 'builder_files_absent': True, 'help_and_ini_wall_seconds': checks,
              'streamlit_quickstart_exceptions': len(app.exception), 'wall_seconds': time.perf_counter() - started}
    print('IMAGE_SMOKE_JSON')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

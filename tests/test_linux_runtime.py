"""Reject incomplete ELF closures before assembling a native runtime."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experimental'))
from assemble_linux_runtime import add_needed_aliases, copy_licenses, dependency_paths


def test_native_dependency_closure_rejects_unresolved_libraries():
    assert dependency_paths('linux-vdso.so.1 (0x1)\nlibm.so.6 => /lib/libm.so.6 (0x2)\n'
                            'libOpenMS.so => /sdk/lib/libOpenMS.so (0x3)\n'
                            '/lib64/ld-linux-x86-64.so.2 (0x4)\n') == {
                                'libOpenMS.so': Path('/sdk/lib/libOpenMS.so')}
    with pytest.raises(ValueError, match='Unresolved ELF dependency'):
        dependency_paths('libOpenMS.so => not found\n')


@pytest.mark.parametrize('entry', ['/sdk/lib/libcustom.so (0x1)',
                                  '../libcustom.so => /sdk/lib/libcustom.so (0x1)'])
def test_absolute_or_relative_dependency_paths_cannot_be_silently_omitted(entry):
    with pytest.raises(ValueError, match='Expected a library basename'):
        dependency_paths(entry)


def test_missing_gcc_recipe_license_requires_installed_exception_and_gpl3(tmp_path):
    exception = tmp_path / 'deps/share/licenses/gcc/RUNTIME.LIBRARY.EXCEPTION'
    exception.parent.mkdir(parents=True)
    exception.write_text('Exception fixture')
    package = {'name': 'libgcc', 'license': 'GPL-3.0-only WITH GCC-exception-3.1',
               'link': {'source': str(tmp_path / 'absent-cache')},
               'files': ['share/licenses/gcc/RUNTIME.LIBRARY.EXCEPTION']}
    with pytest.raises(ValueError, match='complete GPL3'):
        copy_licenses(package, tmp_path / 'deps', tmp_path / 'licenses')
    license = tmp_path / 'COPYING3'
    license.write_text('License fixture: Version 3, 29 June 2007')
    copied = copy_licenses(package, tmp_path / 'deps', tmp_path / 'licenses', license)
    assert set(copied) == {str(exception), str(license)}
    assert (tmp_path / 'licenses/COPYING3').read_bytes() == license.read_bytes()


def test_needed_alias_is_retained_when_ldd_collapsed_the_same_library(tmp_path, monkeypatch):
    implementation = tmp_path / 'libopenblas.so.0'
    implementation.write_bytes(b'Library fixture')
    alias = tmp_path / 'libblas.so.3'
    alias.symlink_to(implementation.name)
    libraries = {implementation.name: implementation}
    monkeypatch.setattr('assemble_linux_runtime.subprocess.check_output',
                        lambda *args, **kwargs: '0x1 (NEEDED) Shared library: [libblas.so.3]')
    add_needed_aliases(libraries, [], [tmp_path])
    assert libraries['libblas.so.3'].resolve() == implementation
    alias.unlink()
    with pytest.raises(ValueError, match='Missing or conflicting DT_NEEDED alias'):
        add_needed_aliases({implementation.name: implementation}, [], [tmp_path])

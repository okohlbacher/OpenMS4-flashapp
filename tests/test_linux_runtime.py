"""Reject incomplete ELF closures before assembling a native runtime."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experimental'))
from assemble_linux_runtime import dependency_paths


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

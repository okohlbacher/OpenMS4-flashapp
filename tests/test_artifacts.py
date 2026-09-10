"""Small binary-header fixtures exercise the deployed verifier; no OpenMS import."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import struct
import tarfile
import tempfile
import unittest
import zipfile

_spec = importlib.util.spec_from_file_location('flashapp_artifacts', Path(__file__).resolve().parents[1] / 'experimental/verify_artifacts.py')
verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify)


def native(machine=62):
    header = bytearray(64)
    header[:6] = b'\x7fELF\x02\x01'
    header[18:20] = machine.to_bytes(2, 'little')
    return bytes(header)


class ArtifactValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / 'artifacts'
        self.directory.mkdir()
        self.dependencies = {'dependencies': {name: {'source_revision': char * 40, 'version': version}
            for name, char, version in [('OpenMS','a','4.0.0'), ('pyopenms','b','4.0.0.dev0'),
                                       ('OpenMSCLI','c','1.0.0'), ('OpenMSFLASH','d','1.0.0'), ('OpenMSTOPP','e','1.0.0')]},
            'external_tools': {'FLASHTnT': {'source_revision': 'f' * 40, 'version': 'fixture-only',
                                          'repository': 'https://example.invalid/fixture'}}}
        self.core = {'schema_version': 1, 'source_revision': 'a' * 40, 'source_dirty': False,
                     'version': '4.0.0', 'build_type': 'Release', 'system_name': 'Linux',
                     'system_processor': 'x86_64', 'cxx_compiler_id': 'GNU', 'cxx_compiler_version': '14.2',
                     'cxx_standard': 23, 'shared_libs': True, 'class_testing_enabled': False,
                     'stl_debug': False, 'standard_library': 'libstdc++',
                     'libstdcxx_cxx11_abi': 1, 'msvc_runtime_library': None,
                     'features': {name: False for name in verify.FEATURES},
                     'dependencies': {'boost': {'version': '1.90.0', 'linkage': 'shared'},
                                      'eigen': {'version': '5.0.0'},
                                      'arrow': {'version': '25.0.0', 'target': 'Arrow::arrow_shared'},
                                      'parquet': {'version': '25.0.0', 'target': 'Parquet::parquet_shared'},
                                      'curl': {'version': '8.16.0'}, 'openmp': {'version': ''}}}
        self.runtime = {'schema_version': 1, 'core': copy.deepcopy(self.core),
                        'packages': {k: v.copy() for k, v in self.dependencies['dependencies'].items() if k != 'pyopenms'},
                        'external_tools': {'FLASHTnT': dict(self.dependencies['external_tools']['FLASHTnT'], source_dirty=False)},
                        'executables': sorted(verify.APP_EXECUTABLES)}
        self.wheel = dict(self.dependencies['dependencies']['pyopenms'], schema_version=1,
                          source_dirty=False, core=copy.deepcopy(self.core))
        self.lock = {'schema_version': 2, 'core_source_revision': 'a' * 40, 'pyopenms_source_revision': 'b' * 40,
                     'build_identity': {k: copy.deepcopy(self.core[k]) for k in verify.CORE_FIELDS},
                     'required_executables': sorted(verify.APP_EXECUTABLES), 'artifacts': []}
        self.mode, self.machine, self.wheel_machine = 0o755, 62, 62
        self.extra_members = []
        self.omit = set()

    def write(self):
        runtime_path = self.directory / 'runtime.tar.gz'
        with tarfile.open(runtime_path, 'w:gz') as archive:
            contents = {'share/openms4/runtime-provenance.json': json.dumps(self.runtime).encode()}
            contents.update({'bin/' + name: native(self.machine) for name in verify.APP_EXECUTABLES})
            for name, data in contents.items():
                if name in self.omit:
                    continue
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = self.mode if name.startswith('bin/') else 0o644
                archive.addfile(member, io.BytesIO(data))
            for member in self.extra_members:
                archive.addfile(member, io.BytesIO(b''))
        wheel_path = self.directory / 'pyopenms.whl'
        with zipfile.ZipFile(wheel_path, 'w') as archive:
            archive.writestr('pyopenms/_build_provenance.json', json.dumps(self.wheel))
            archive.writestr('pyopenms/_core.so', native(self.wheel_machine))
            archive.writestr('pyopenms.dist-info/METADATA', 'Name: pyopenms\nVersion: 4.0.0.dev0\n')
        self.lock['artifacts'] = [{'path': path.name, 'kind': kind, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                                   for path, kind in [(runtime_path, 'runtime'), (wheel_path, 'wheel')]]
        self.lock_path = self.root / 'artifacts.lock.json'
        self.dep_path = self.root / 'dependencies.lock.json'
        self.save_locks()

    def save_locks(self):
        self.lock_path.write_text(json.dumps(self.lock))
        self.dep_path.write_text(json.dumps(self.dependencies))

    def check(self):
        return verify.verified_artifacts(self.lock_path, self.directory, self.dep_path)

    def rehash(self, path):
        for entry in self.lock['artifacts']:
            if entry['path'] == path.name:
                entry['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.save_locks()

    def test_native_header_formats_and_wrong_architectures(self):
        for endian, magic in [('<', b'\xcf\xfa\xed\xfe'), ('>', b'\xfe\xed\xfa\xcf')]:
            data = magic + struct.pack(endian + 'I', 0x100000c) + bytes(56)
            self.assertEqual(verify._architecture(data), ('Darwin', 'aarch64'))
        pe = bytearray(256); pe[:2] = b'MZ'; pe[60:64] = (128).to_bytes(4, 'little')
        pe[128:132] = b'PE\0\0'; pe[132:134] = (0x8664).to_bytes(2, 'little')
        self.assertEqual(verify._architecture(pe), ('Windows', 'x86_64'))
        for data in (b'plain text', b'MZ' + bytes(100), b'\x7fELF\x02\x00' + bytes(58)):
            with self.assertRaises(ValueError): verify._architecture(data)

    def test_wheel_missing_or_wrong_metadata_and_native_bindings(self):
        for removed, replacement in [('pyopenms/_build_provenance.json', None),
                                     ('pyopenms/_core.so', None), ('pyopenms.dist-info/METADATA', None),
                                     ('pyopenms.dist-info/METADATA', b'Name: unrelated\nVersion: 4.0.0.dev0\n')]:
            with self.subTest(member=removed, replacement=replacement):
                self.write(); path = self.directory / 'pyopenms.whl'
                with zipfile.ZipFile(path) as archive:
                    entries = {name: archive.read(name) for name in archive.namelist()}
                entries.pop(removed)
                if replacement is not None: entries[removed] = replacement
                with zipfile.ZipFile(path, 'w') as archive:
                    for name, data in entries.items(): archive.writestr(name, data)
                self.rehash(path)
                with self.assertRaises(ValueError): self.check()

    def test_runtime_provenance_schema_and_executable_declaration(self):
        self.omit.add('share/openms4/runtime-provenance.json'); self.write()
        with self.assertRaisesRegex(ValueError, 'provenance file'): self.check()
        self.omit.clear(); self.runtime['schema_version'] = 99; self.write()
        with self.assertRaisesRegex(ValueError, 'runtime provenance schema'): self.check()
        self.runtime['schema_version'] = 1; self.runtime['executables'] = []; self.write()
        with self.assertRaisesRegex(ValueError, 'runtime provenance'): self.check()

    def test_verifier_entry_point(self):
        self.write()
        from unittest.mock import patch
        with patch('builtins.print'):
            verify.main([str(self.lock_path), str(self.directory), '--dependencies', str(self.dep_path),
                         '--extract', str(self.root / 'prefix')])
        self.assertTrue((self.root / 'prefix/bin/FLASHDeconv').is_file())

    def test_valid_fixture_and_extraction(self):
        self.write()
        _, artifacts = self.check()
        destination = self.root / 'prefix'
        verify.extract_runtime(artifacts[0][0], destination)
        self.assertTrue((destination / 'bin/FLASHDeconv').stat().st_mode & stat.S_IXUSR)
        self.assertEqual(json.loads((destination / 'share/openms4/runtime-provenance.json').read_text()), self.runtime)

    def test_each_build_field_mismatch(self):
        for field in verify.CORE_FIELDS:
            with self.subTest(field=field):
                original = self.lock['build_identity'][field]
                self.lock['build_identity'][field] = 'mismatch'
                self.write()
                with self.assertRaisesRegex(ValueError, field): self.check()
                self.lock['build_identity'][field] = original

    def test_source_and_dirty_provenance(self):
        cases = [(self.lock, 'core_source_revision', '9'*40), (self.lock, 'pyopenms_source_revision', '9'*40),
                 (self.runtime['core'], 'source_revision', '9'*40), (self.wheel, 'source_revision', '9'*40),
                 (self.runtime['core'], 'source_dirty', True), (self.wheel, 'source_dirty', True),
                 (self.wheel['core'], 'source_dirty', True), (self.wheel, 'version', '3.6.0')]
        for obj, field, value in cases:
            with self.subTest(field=field, value=value):
                original = obj[field]; obj[field] = value
                self.write()
                with self.assertRaises(ValueError): self.check()
                obj[field] = original

    def test_runtime_wheel_full_metadata_agreement(self):
        self.wheel['core']['extra_recorded_fact'] = 'different build'
        self.write()
        with self.assertRaisesRegex(ValueError, 'identical Core'): self.check()

    def test_each_runtime_package_pin(self):
        for package in self.runtime['packages'].values():
            original = package['source_revision']; package['source_revision'] = '9'*40
            self.write()
            with self.assertRaisesRegex(ValueError, 'Runtime package'): self.check()
            package['source_revision'] = original

    def test_unresolved_or_wrong_external_tool(self):
        self.dependencies['external_tools'] = {}
        self.write()
        with self.assertRaisesRegex(ValueError, 'External FLASHTnT'): self.check()
        self.dependencies['external_tools'] = {'FLASHTnT': dict(self.runtime['external_tools']['FLASHTnT'])}
        self.runtime['external_tools']['FLASHTnT']['source_revision'] = '9'*40
        self.write()
        with self.assertRaisesRegex(ValueError, 'FLASHTnT provenance'): self.check()

    def test_native_architecture_and_execute_permission(self):
        for attribute, value in [('mode', 0o644), ('machine', 183), ('wheel_machine', 183)]:
            with self.subTest(attribute=attribute):
                original = getattr(self, attribute); setattr(self, attribute, value)
                self.write()
                with self.assertRaises(ValueError): self.check()
                setattr(self, attribute, original)

    def test_missing_required_executable(self):
        self.omit.add('bin/DecoyDatabase')
        self.write()
        with self.assertRaisesRegex(ValueError, 'executable absent'): self.check()
        self.omit.clear(); self.lock['required_executables'] = ['FLASHDeconv']
        self.write()
        with self.assertRaisesRegex(ValueError, 'omits'): self.check()

    def test_hash_and_unlisted_inputs(self):
        self.write()
        self.lock['artifacts'][0]['sha256'] = '0'*64; self.save_locks()
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'): self.check()
        self.write(); (self.directory / 'unlisted.whl').touch()
        with self.assertRaisesRegex(ValueError, 'Unlisted'): self.check()

    def test_duplicate_wrong_kind_and_path_inputs(self):
        self.write()
        for field, value in [('path', '../runtime.tar.gz'), ('kind', 'other'), ('sha256', 'placeholder')]:
            with self.subTest(field=field):
                original = self.lock['artifacts'][0][field]
                self.lock['artifacts'][0][field] = value; self.save_locks()
                with self.assertRaises(ValueError): self.check()
                self.lock['artifacts'][0][field] = original
        self.lock['artifacts'].append(self.lock['artifacts'][0]); self.save_locks()
        with self.assertRaises(ValueError): self.check()

    def test_archive_traversal_links_and_special_files_before_extraction(self):
        for name, kind, target in [('C:/outside', tarfile.REGTYPE, ''), ('../outside', tarfile.REGTYPE, ''), ('/outside', tarfile.REGTYPE, ''),
                                  ('bin/link', tarfile.SYMTYPE, '../../outside'),
                                  ('bin/link', tarfile.LNKTYPE, '../outside'),
                                  ('device', tarfile.CHRTYPE, '')]:
            with self.subTest(name=name, kind=kind):
                member = tarfile.TarInfo(name); member.type = kind; member.linkname = target
                self.extra_members = [member]; self.write()
                destination = self.root / 'reject'
                with self.assertRaises(ValueError): self.check()
                with self.assertRaises(ValueError): verify.extract_runtime(self.directory / 'runtime.tar.gz', destination)
                self.assertFalse(destination.exists())

    def test_archive_link_directory_chain_is_rejected_before_writes(self):
        alias = tarfile.TarInfo('alias'); alias.type = tarfile.SYMTYPE; alias.linkname = '.'
        link = tarfile.TarInfo('alias/link'); link.type = tarfile.SYMTYPE; link.linkname = '..'
        self.extra_members = [alias, link]; self.write()
        destination = self.root / 'reject'
        with self.assertRaises(ValueError): verify.extract_runtime(self.directory / 'runtime.tar.gz', destination)
        self.assertFalse(destination.exists())

    def test_safe_relative_library_link(self):
        member = tarfile.TarInfo('lib/runtime-info'); member.type = tarfile.SYMTYPE
        member.linkname = '../share/openms4/runtime-provenance.json'
        self.extra_members = [member]; self.write(); self.check()
        verify.extract_runtime(self.directory / 'runtime.tar.gz', self.root / 'prefix')
        self.assertTrue((self.root / 'prefix/lib/runtime-info').is_file())

    def test_existing_destination_symlink_cannot_escape(self):
        self.write(); self.check()
        destination = self.root / 'prefix'; destination.mkdir()
        outside = self.root / 'outside'; outside.mkdir()
        (destination / 'bin').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(tarfile.FilterError):
            verify.extract_runtime(self.directory / 'runtime.tar.gz', destination)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

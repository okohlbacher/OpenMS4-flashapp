#!/usr/bin/env python3
"""Validate pinned app inputs and embedded build identity before extraction."""
import argparse
from email.parser import Parser
import hashlib
import json
import platform
import re
import stat
import struct
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

DEFAULT_DEPENDENCIES = Path(__file__).resolve().parents[1] / 'dependencies.lock.json'
CORE_FIELDS = ('build_type', 'system_name', 'system_processor', 'cxx_compiler_id',
               'cxx_compiler_version', 'cxx_standard', 'shared_libs',
               'class_testing_enabled', 'stl_debug', 'standard_library',
               'libstdcxx_cxx11_abi', 'msvc_runtime_library', 'features', 'dependencies')
FEATURES = {'hdf5', 'opentims', 'thermo_raw', 'tdl', 'onnx', 'openmp', 'wnetalign', 'openswath'}
# These are used by the current app, irrespective of a supplied artifact lock.
APP_EXECUTABLES = {'FLASHDeconv', 'DecoyDatabase', 'FLASHTnT'}


def _sha(value, label, size=40):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{' + str(size) + '}', value):
        raise ValueError(f'{label}: full lowercase source/digest hash required')


def _relative(name):
    path = PurePosixPath(name)
    if not name or ':' in name or '\\' in name or path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Unsafe archive path: {name}')
    return path


def _architecture(data):
    """Inspect native headers, without running an untrusted binary."""
    if data.startswith(b'\xca\xfe\xba\xbe') and len(data) >= 28:
        # delocate can retain a one-slice fat container (e.g. libgcc_s).
        count, machine, _, offset, size, _ = struct.unpack('>6I', data[4:28])
        if count != 1 or offset < 28 or size < 8 or len(data) < offset + 8:
            raise ValueError('Expected one inspectable Mach-O architecture')
        nested = data[offset:]
        if nested[:4] not in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf'):
            raise ValueError('Expected a Mach-O slice')
        endian = '<' if nested[0] == 0xcf else '>'
        if struct.unpack(endian + 'I', nested[4:8])[0] != machine:
            raise ValueError('Mach-O slice architecture differs from container')
        return _architecture(nested)
    if data.startswith(b'\x7fELF') and len(data) >= 20:
        if data[5] not in (1, 2):
            raise ValueError('Invalid ELF byte order')
        machine = int.from_bytes(data[18:20], 'little' if data[5] == 1 else 'big')
        return 'Linux', {62: 'x86_64', 183: 'aarch64'}.get(machine, 'unsupported')
    if data[:4] in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf') and len(data) >= 8:
        endian = '<' if data[0] == 0xcf else '>'
        machine = struct.unpack(endian + 'I', data[4:8])[0]
        return 'Darwin', {0x1000007: 'x86_64', 0x100000c: 'aarch64'}.get(machine, 'unsupported')
    if data.startswith(b'MZ') and len(data) >= 64:
        offset = int.from_bytes(data[60:64], 'little')
        if data[offset:offset + 4] == b'PE\0\0':
            machine = int.from_bytes(data[offset + 4:offset + 6], 'little')
            return 'Windows', {0x8664: 'x86_64', 0xaa64: 'aarch64'}.get(machine, 'unsupported')
    raise ValueError('Expected a supported native executable/library header (ELF, Mach-O, PE)')


def _native(data, core, name):
    system, architecture = _architecture(data)
    expected = {'arm64': 'aarch64', 'AMD64': 'x86_64'}.get(core['system_processor'], core['system_processor'])
    if (system, architecture) != (core['system_name'], expected):
        raise ValueError(f'{name}: native platform/architecture mismatch')


def _core(info, lock, dependencies):
    if info.get('schema_version') != 1 or info.get('source_dirty') is not False:
        raise ValueError('Core provenance must use schema 1 and a clean source tree')
    pin = dependencies['OpenMS']
    if info.get('source_revision') != pin['source_revision'] or info.get('version') != pin['version']:
        raise ValueError('Embedded Core provenance differs from app dependency pin')
    if info['source_revision'] != lock['core_source_revision']:
        raise ValueError('Artifact Core source pin mismatch')
    expected = lock.get('build_identity', {})
    for field in CORE_FIELDS:
        if field not in info or field not in expected or info[field] != expected[field]:
            raise ValueError(f'Core build identity mismatch: {field}')
    if not isinstance(info['cxx_standard'], int) or isinstance(info['cxx_standard'], bool) or info['cxx_standard'] < 20:
        raise ValueError('A C++20 or newer Core is required')
    for field in ('shared_libs', 'class_testing_enabled', 'stl_debug'):
        if not isinstance(info[field], bool):
            raise ValueError(f'Invalid Core {field}')
    if set(info['features']) != FEATURES or any(type(v) is not bool for v in info['features'].values()):
        raise ValueError('Invalid Core feature set')
    if not isinstance(info['dependencies'], dict) or not info['dependencies']:
        raise ValueError('Core ABI dependency metadata is required')
    for name in ('boost', 'eigen', 'arrow', 'parquet', 'curl', 'openmp'):
        dependency = info['dependencies'].get(name, {})
        if not isinstance(dependency.get('version'), str):
            raise ValueError(f'Missing Core dependency metadata: {name}')
    if info['dependencies']['boost'].get('linkage') not in {'shared', 'static'}:
        raise ValueError('Invalid Boost linkage metadata')
    for name in ('arrow', 'parquet'):
        if not info['dependencies'][name].get('target'):
            raise ValueError(f'Missing Core dependency target: {name}')
    return info


def _members(archive):
    members = {}
    for member in archive.getmembers():
        path = _relative(member.name)
        name = str(path)
        if name in members:
            raise ValueError(f'Duplicate archive member: {name}')
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise ValueError(f'Unsupported archive member type: {name}')
        if member.issym() or member.islnk():
            target = PurePosixPath(member.linkname)
            if target.is_absolute() or ':' in member.linkname or '\\' in member.linkname:
                raise ValueError(f'Unsafe archive link: {name}')
            parts = list(path.parent.parts) if member.issym() else []
            for part in target.parts:
                if part == '..':
                    if not parts:
                        raise ValueError(f'Unsafe archive link: {name}')
                    parts.pop()
                elif part != '.':
                    parts.append(part)
        members[name] = member
    links = {name for name, member in members.items() if member.issym() or member.islnk()}
    for name, member in members.items():
        # Never write through an earlier archive link. File aliases themselves
        # remain supported, e.g. libOpenMS.so -> libOpenMS.so.4.
        if any(str(parent) in links for parent in PurePosixPath(name).parents):
            raise ValueError(f'Archive member traverses a link: {name}')
        if member.issym() or member.islnk():
            target = PurePosixPath(name).parent / member.linkname if member.issym() else PurePosixPath(member.linkname)
            if any(str(parent) in links for parent in target.parents):
                raise ValueError(f'Archive link target traverses another link: {name}')
    return members


def _runtime(path, lock, dependencies, required):
    with tarfile.open(path) as archive:
        members = _members(archive)
        metadata = members.get('share/openms4/runtime-provenance.json')
        if metadata is None or not metadata.isfile():
            raise ValueError('Runtime provenance file is required')
        provenance = json.load(archive.extractfile(metadata))
        if provenance.get('schema_version') != 1:
            raise ValueError('Unsupported runtime provenance schema')
        core = _core(provenance.get('core', {}), lock, dependencies)
        for name in ('OpenMS', 'OpenMSCLI', 'OpenMSFLASH', 'OpenMSTOPP'):
            pin = dependencies.get(name)
            package = provenance.get('packages', {}).get(name, {})
            if not pin or any(package.get(key) != pin[key] for key in ('source_revision', 'version')):
                raise ValueError(f'Runtime package does not match app dependency pin: {name}')
        external = provenance.get('external_tools', {}).get('FLASHTnT', {})
        if any(external.get(key) != dependencies['FLASHTnT'][key] for key in
               ('source_revision', 'version', 'repository')) or external.get('source_dirty') is not False:
            raise ValueError('Runtime FLASHTnT provenance differs from external tool pin')
        if not required.issubset(set(provenance.get('executables', []))):
            raise ValueError('Required executable absent from runtime provenance')
        for name in required:
            filename = 'bin/' + name + ('.exe' if core['system_name'] == 'Windows' else '')
            member = members.get(filename)
            if member is None or not member.isfile():
                raise ValueError(f'Required regular runtime executable absent: {filename}')
            if core['system_name'] != 'Windows' and not member.mode & stat.S_IXUSR:
                raise ValueError(f'Runtime executable lacks execute permission: {filename}')
            _native(archive.extractfile(member).read(65536), core, filename)
        for name, member in members.items():
            if member.isfile() and (name.endswith(('.so', '.dylib', '.dll')) or '.so.' in name):
                _native(archive.extractfile(member).read(65536), core, name)
        return core


def _wheel(path, lock, dependencies):
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate wheel member')
        for item in wheel.infolist():
            _relative(item.filename)
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('Wheel symlinks are unsupported')
        try:
            provenance = json.loads(wheel.read('pyopenms/_build_provenance.json'))
        except KeyError as error:
            raise ValueError('Wheel provenance file is required') from error
        pin = dependencies['pyopenms']
        metadata_names = [name for name in names if name.endswith('.dist-info/METADATA')]
        if len(metadata_names) != 1:
            raise ValueError('One wheel distribution metadata file is required')
        metadata = Parser().parsestr(wheel.read(metadata_names[0]).decode('utf-8'))
        if metadata.get('Name', '').lower() != 'pyopenms' or metadata.get('Version') != pin['version']:
            raise ValueError('Wheel distribution name/version differs from app dependency pin')
        if provenance.get('schema_version') != 1 or provenance.get('source_dirty') is not False:
            raise ValueError('Wheel provenance must use schema 1 and a clean source tree')
        if any(provenance.get(key) != pin[key] for key in ('source_revision', 'version')):
            raise ValueError('Wheel source/version differs from app dependency pin')
        if provenance['source_revision'] != lock['pyopenms_source_revision']:
            raise ValueError('Artifact Python source pin mismatch')
        core = _core(provenance.get('core', {}), lock, dependencies)
        native = [name for name in names if (name.endswith(('.so', '.pyd', '.dylib')) or '.so.' in name)]
        if not native:
            raise ValueError('pyOpenMS wheel contains no native bindings')
        for name in native:
            with wheel.open(name) as member:
                _native(member.read(65536), core, name)
        return core


def verified_artifacts(lock_path, directory, dependencies_path=None, *, wheel_only=False):
    """Return (lock, [(path, kind)]) only after all inputs pass validation."""
    lock = json.loads(Path(lock_path).read_text())
    app_lock = json.loads(Path(dependencies_path or DEFAULT_DEPENDENCIES).read_text())
    dependencies = app_lock['dependencies']
    if not wheel_only:
        external = app_lock.get('external_tools', {}).get('FLASHTnT', {})
        _sha(external.get('source_revision'), 'External FLASHTnT source')
        if not external.get('version') or not external.get('repository'):
            raise ValueError('Separately pinned FLASHTnT version and source repository required')
        dependencies = dict(dependencies, FLASHTnT=external)
    if lock.get('schema_version') != 2:
        raise ValueError('Unsupported artifact lock schema; expected 2')
    for key, dependency in (('core_source_revision', 'OpenMS'), ('pyopenms_source_revision', 'pyopenms')):
        _sha(lock.get(key), key)
        if lock[key] != dependencies[dependency]['source_revision']:
            raise ValueError(f'Artifact {key} differs from app dependency lock')
    required = set(lock.get('required_executables', []))
    if not wheel_only and not APP_EXECUTABLES.issubset(required):
        raise ValueError('Artifact lock omits an executable required by FLASHApp')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+', name) for name in required):
        raise ValueError('Invalid executable name')
    directory = Path(directory).resolve()
    artifacts, names, kinds, cores = [], set(), set(), []
    expected_kinds = {'wheel'} if wheel_only else {'wheel', 'runtime'}
    for entry in lock['artifacts']:
        name, kind = entry['path'], entry.get('kind')
        if not name or name in ('.', '..') or Path(name).name != name or '\\' in name or name in names:
            raise ValueError('Artifact paths must be unique file names')
        if kind not in expected_kinds or kind in kinds:
            raise ValueError(f'Exactly one artifact of each kind required: {sorted(expected_kinds)}')
        names.add(name)
        kinds.add(kind)
        _sha(entry.get('sha256'), name, 64)
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'{name}: regular local file required')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != entry['sha256']:
            raise ValueError(f'{name}: SHA-256 mismatch')
        cores.append(_wheel(path, lock, dependencies) if kind == 'wheel' else _runtime(path, lock, dependencies, required))
        artifacts.append((path, kind))
    if kinds != expected_kinds:
        raise ValueError(f'Exactly one artifact of each kind required: {sorted(expected_kinds)}')
    if not wheel_only and cores[0] != cores[1]:
        raise ValueError('Runtime and wheel must embed identical Core build provenance')
    extras = {p.name for p in directory.iterdir()} - names
    if extras:
        raise ValueError(f'Unlisted artifacts: {sorted(extras)}')
    return lock, artifacts


def extract_runtime(path, destination):
    """Safely extract an already verified runtime archive (no code execution)."""
    destination = Path(destination).resolve()
    with tarfile.open(path) as archive:
        _members(archive)  # Validate all names and links before writing anything.
        archive.extractall(destination, filter='data')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lock')
    parser.add_argument('directory')
    parser.add_argument('--dependencies', default=str(DEFAULT_DEPENDENCIES))
    parser.add_argument('--extract')
    parser.add_argument('--check-host', action='store_true')
    parser.add_argument('--wheel-only', action='store_true',
                        help='Verify one pinned wheel for app tests; does not qualify a runtime or image')
    args = parser.parse_args(argv)
    if args.wheel_only and args.extract:
        parser.error('--extract requires whole-runtime verification')
    lock, artifacts = verified_artifacts(args.lock, args.directory, args.dependencies, wheel_only=args.wheel_only)
    if args.check_host:
        identity = lock['build_identity']
        normalize = lambda value: {'arm64': 'aarch64', 'AMD64': 'x86_64'}.get(value, value)
        if (identity['system_name'], normalize(identity['system_processor'])) != (platform.system(), normalize(platform.machine())):
            raise ValueError('Runtime artifact does not target this host platform')
    if args.extract:
        for path, kind in artifacts:
            if kind == 'runtime':
                extract_runtime(path, args.extract)
    print(f'Verified {len(artifacts)} artifacts for core {lock["core_source_revision"]}')


if __name__ == '__main__':
    main()

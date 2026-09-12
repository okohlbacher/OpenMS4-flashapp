#!/usr/bin/env python3
"""Assemble trusted, already-tested Linux SDK products for isolated image testing.

Run on the Linux builder. This consumes its clean source checkouts, successful
build/install records and installed ELF files; it does not compile or certify
scientific equivalence. The image must still pass isolated native/workflow tests.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

from verify_artifacts import APP_EXECUTABLES, verified_artifacts

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {'cli': 'OpenMSCLI', 'topp': 'OpenMSTOPP', 'flash': 'OpenMSFLASH', 'flashtnt': 'FLASHTnT'}
SYSTEM_LIBRARIES = {'libc.so.6', 'libm.so.6', 'libdl.so.2', 'libpthread.so.0', 'librt.so.1', 'libresolv.so.2'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dependency_paths(output):
    """Read the complete ldd closure, rejecting missing or unexpected entries."""
    found = {}
    for line in output.splitlines():
        fields = line.split()
        if not fields or fields[0] in ('linux-vdso.so.1', '/lib64/ld-linux-x86-64.so.2',
                                       '/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'):
            continue  # Kernel helper and the base image's ELF interpreter.
        if '/' in fields[0] or '\\' in fields[0] or fields[0] in ('.', '..'):
            raise ValueError(f'Expected a library basename, not an absolute/path DT_NEEDED entry: {line}')
        if len(fields) < 4 or fields[1] != '=>' or not fields[2].startswith('/'):
            raise ValueError(f'Unresolved ELF dependency: {line}')
        if fields[0] not in SYSTEM_LIBRARIES:
            found[fields[0]] = Path(fields[2])
    return found


def assemble(args):
    work, source, deps, output = [p.resolve() for p in (args.work, args.source, args.dependencies, args.output)]
    sdk, results = work / 'sdk', work / 'results'
    app = json.loads((ROOT / 'dependencies.lock.json').read_text())['dependencies']
    graph = json.loads((results / 'packages.json').read_text())
    core = json.loads((sdk / 'lib/cmake/OpenMS/OpenMSBuildInfo.json').read_text())
    if core['system_name'] != 'Linux' or core['system_processor'] != 'x86_64':
        raise ValueError('This experimental assembler supports Linux x86_64 only')
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / 'runtime'
    (runtime / 'bin').mkdir(parents=True)
    (runtime / 'lib').mkdir()
    metadata = runtime / 'share/openms4'
    metadata.mkdir(parents=True)
    receipt = {'trust': 'Clean source and successful guarded build/install records from the trusted builder',
               'records': {}, 'files': {}, 'system_libraries': sorted(SYSTEM_LIBRARIES)}
    installed = set()
    for name, package in PACKAGES.items():
        pin = app[package]
        checkout = source / graph[name]['path']
        head = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
        dirty = subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain'], text=True)
        if dirty or head != pin['source_revision'] or head != graph[name]['source_revision']:
            raise ValueError(f'{package}: clean checkout, build graph and app pin must match')
        cache = (work / 'build' / name / 'CMakeCache.txt').read_text()
        if f'CMAKE_HOME_DIRECTORY:INTERNAL={checkout}\n' not in cache or 'OPENMS4_REQUIRE_CLEAN_SOURCE:BOOL=ON\n' not in cache:
            raise ValueError(f'{package}: wrong build source or missing clean-source guard')
        for stage in ('configure', 'build', 'tests', 'install'):
            path = results / f'{name}-{stage}.json'
            if json.loads(path.read_text())['returncode']:
                raise ValueError(f'{name} {stage} did not pass')
            receipt['records'][path.name] = digest(path)
        manifest = work / 'build' / name / 'install_manifest.txt'
        installed.update(manifest.read_text().splitlines())
        receipt['records'][name + '-install_manifest.txt'] = digest(manifest)
        shutil.copy2(checkout / 'License.txt', metadata / f'{name}-LICENSE')
    shutil.copy2(source / graph['core']['path'] / 'LICENSE', metadata / 'core-LICENSE')
    libraries = {}
    environment = dict(os.environ, LD_LIBRARY_PATH=os.pathsep.join([str(sdk / 'lib'), str(deps / 'lib')]))
    for name in sorted(APP_EXECUTABLES):
        executable = sdk / 'bin' / name
        if str(executable) not in installed:
            raise ValueError(f'{name} is absent from verified install manifests')
        shutil.copy2(executable, runtime / 'bin' / name)
        receipt['files']['bin/' + name] = {'source': str(executable), 'sha256': digest(executable)}
        listing = subprocess.check_output(['ldd', str(executable)], env=environment, text=True)
        for soname, library in dependency_paths(listing).items():
            if soname in libraries and digest(libraries[soname]) != digest(library):
                raise ValueError(f'Conflicting library: {soname}')
            libraries[soname] = library
    for soname, library in libraries.items():
        resolved = library.resolve()
        if not resolved.is_relative_to(sdk) and not resolved.is_relative_to(deps):
            raise ValueError(f'Library outside the trusted SDK/dependency prefixes: {resolved}')
        shutil.copy2(resolved, runtime / 'lib' / soname)
        receipt['files']['lib/' + soname] = {'source': str(resolved), 'sha256': digest(resolved)}
    shutil.copytree(sdk / 'share/OpenMS', runtime / 'share/OpenMS',
                    ignore=shutil.ignore_patterns('examples', 'test-data', 'doc', 'docs'))
    (metadata / 'tools').mkdir()
    for name in ('topp', 'flash', 'flashtnt'):
        manifest = sdk / f'share/openms4/tools/{name}.tools.tsv'
        selected = [line for line in manifest.read_text().splitlines()
                    if line and line.split('\t')[0] in APP_EXECUTABLES]
        (metadata / 'tools' / manifest.name).write_text('\n'.join(selected) + '\n')
    # Keep the actual conda recipe/license texts for every copied dependency.
    needed = {str(path.resolve().relative_to(deps)) for path in libraries.values() if path.resolve().is_relative_to(deps)}
    covered = set()
    for path in sorted((deps / 'conda-meta').glob('*.json')):
        package = json.loads(path.read_text())
        provided = needed.intersection(package.get('files', []))
        if not provided:
            continue
        license_dir = Path(package['link']['source']) / 'info/licenses'
        if not license_dir.is_dir():
            raise ValueError(f'Original dependency license directory unavailable: {license_dir}')
        shutil.copytree(license_dir, metadata / 'licenses' / path.stem)
        covered.update(provided)
    if needed - covered:
        raise ValueError(f'Dependency license metadata missing: {sorted(needed - covered)}')
    provenance = {'schema_version': 1, 'core': core,
                  'packages': {name: app[name] for name in ('OpenMS', 'OpenMSCLI', 'OpenMSTOPP', 'OpenMSFLASH')},
                  'external_tools': {'FLASHTnT': dict(app['FLASHTnT'], source_dirty=False)},
                  'executables': sorted(APP_EXECUTABLES)}
    (metadata / 'runtime-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    (metadata / 'assembly-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    artifacts = output / 'artifacts'
    artifacts.mkdir()
    archive = artifacts / 'openms4-flashapp-runtime-linux-x86_64.tar.gz'
    with tarfile.open(archive, 'w:gz') as stream:
        for path in sorted(runtime.rglob('*')):
            stream.add(path, arcname=str(path.relative_to(runtime)), recursive=False)
    shutil.copy2(args.wheel, artifacts / args.wheel.name)
    lock = json.loads(args.wheel_lock.read_text())
    lock['required_executables'] = sorted(APP_EXECUTABLES)
    lock['artifacts'].append({'path': archive.name, 'kind': 'runtime', 'sha256': digest(archive)})
    lock_path = output / 'artifacts.lock.json'
    lock_path.write_text(json.dumps(lock, indent=2) + '\n')
    verified_artifacts(lock_path, artifacts)
    print(json.dumps({'verified': True, 'library_count': len(libraries), 'archive_bytes': archive.stat().st_size,
                      'artifacts_lock': str(lock_path)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('work', 'source', 'dependencies', 'output', 'wheel', 'wheel-lock'):
        parser.add_argument('--' + name, required=True, type=Path)
    assemble(parser.parse_args())

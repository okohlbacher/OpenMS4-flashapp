#!/usr/bin/env python3
"""Verify exact local build products before consuming or extracting them."""
import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath


def verified_artifacts(lock_path, directory):
    lock = json.loads(Path(lock_path).read_text())
    if lock.get('schema_version') != 1:
        raise ValueError('Unsupported artifact lock schema')
    if not re.fullmatch(r'[0-9a-f]{40}', lock.get('core_source_revision', '')):
        raise ValueError('An actual full core source commit is required')
    directory = Path(directory).resolve()
    artifacts = []
    names = set()
    for entry in lock['artifacts']:
        name = entry['path']
        if Path(name).name != name or name in names:
            raise ValueError('Artifact paths must be unique file names')
        names.add(name)
        if not re.fullmatch(r'[0-9a-f]{64}', entry.get('sha256', '')):
            raise ValueError(f'{name}: actual SHA-256 required')
        if entry.get('kind') not in {'wheel', 'runtime'}:
            raise ValueError(f'{name}: unknown artifact kind')
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'{name}: regular local file required')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != entry['sha256']:
            raise ValueError(f'{name}: SHA-256 mismatch')
        artifacts.append((path, entry['kind']))
    extras = {p.name for p in directory.iterdir() if p.is_file()} - names
    if extras:
        raise ValueError(f'Unlisted artifacts: {sorted(extras)}')
    if not artifacts:
        raise ValueError('No artifacts supplied')
    return lock, artifacts


def extract_runtime(path, destination):
    destination = Path(destination).resolve()
    with tarfile.open(path) as archive:
        # Validate all members before writing. Python's data filter additionally
        # handles link targets, file types, and permissions during extraction.
        for member in archive.getmembers():
            parts = PurePosixPath(member.name)
            if parts.is_absolute() or '..' in parts.parts:
                raise ValueError(f'Unsafe archive member: {member.name}')
        archive.extractall(destination, filter='data')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lock')
    parser.add_argument('directory')
    parser.add_argument('--extract')
    args = parser.parse_args()
    lock, artifacts = verified_artifacts(args.lock, args.directory)
    if args.extract:
        for path, kind in artifacts:
            if kind == 'runtime':
                extract_runtime(path, args.extract)
        for name in lock.get('required_executables', []):
            if Path(name).name != name or not (Path(args.extract) / 'bin' / name).is_file():
                raise ValueError(f'Required runtime executable absent: {name}')
    print(f'Verified {len(artifacts)} artifacts for core {lock["core_source_revision"]}')


if __name__ == '__main__':
    main()

"""Deployment input validation; never starts Docker or builds an image."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DependencyLock(unittest.TestCase):
    def test_runtime_requirements_are_exact_and_hashed(self):
        blocks = (ROOT / 'requirements.txt').read_text().split('\n\n')
        packages = {}
        for block in blocks:
            if not block.strip() or block.startswith('#'): continue
            lines = block.splitlines()
            match = re.fullmatch(r'([a-z0-9_-]+)==([^ ]+) \\', lines[0])
            self.assertIsNotNone(match, lines[0])
            name, version = match.groups()
            self.assertNotIn(name, packages)
            self.assertNotEqual(name, 'pyopenms')
            packages[name] = version
            self.assertTrue(lines[1:])
            for line in lines[1:]:
                self.assertRegex(line, r'^    --hash=sha256:[0-9a-f]{64}( \\)?$')
        for line in (ROOT / 'requirements.in').read_text().splitlines():
            if not line or line.startswith('#'): continue
            name = re.split(r'[<>=]', line)[0]
            self.assertIn(name, packages)


@unittest.skipUnless(shutil.which('docker'), 'Docker CLI needed for Compose config validation')
class ComposeInputs(unittest.TestCase):
    def config(self, image):
        environment = dict(os.environ)
        environment.pop('PYTHON_IMAGE', None)
        if image is not None: environment['PYTHON_IMAGE'] = image
        return subprocess.run(['docker', 'compose', '--env-file', os.devnull, 'config', '--format', 'json'],
                              cwd=ROOT, env=environment, text=True, capture_output=True, timeout=15)

    def test_missing_image_input_rejected(self):
        result = self.config(None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PYTHON_IMAGE', result.stderr)

    def test_explicit_image_and_shared_workspace(self):
        # Syntax-only fixture digest; no image is pulled, built, or run.
        image = 'python:3.12-slim@sha256:' + 'a'*64
        result = self.config(image)
        self.assertEqual(result.returncode, 0, result.stderr)
        app = json.loads(result.stdout)['services']['flashapp']
        self.assertEqual(app['build']['args'], {'PYTHON_IMAGE': image})
        self.assertIn('/workspaces', [volume['target'] for volume in app['volumes']])


if __name__ == '__main__': unittest.main()

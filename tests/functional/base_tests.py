# Copyright (c) 2018, Red Hat, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import functools
import os
import subprocess

import unittest2
import yaml

import cinderlib


def set_backend(func, new_name, backend_name):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        self.backend = cinderlib.Backend.backends[backend_name]
        return func(self, *args, **kwargs)
    wrapper.__name__ = new_name
    wrapper.__wrapped__ = func
    return wrapper


def test_all_backends(cls):
    config = BaseFunctTestCase.ensure_config_loaded()
    for fname, func in cls.__dict__.items():
        if fname.startswith('test_'):
            for backend in config['backends']:
                bname = backend['volume_backend_name']
                test_name = '%s_on_%s' % (fname, bname)
                setattr(cls, test_name, set_backend(func, test_name, bname))
            delattr(cls, fname)
    return cls


class BaseFunctTestCase(unittest2.TestCase):
    DEFAULTS = {'logs': False, 'venv_sudo': False}
    FNULL = open(os.devnull, 'w')
    CONFIG_FILE = os.environ.get('CL_FTEST_CFG', 'tests/functional/lvm.yaml')
    tests_config = None

    @classmethod
    def ensure_config_loaded(cls):
        if not cls.tests_config:
            # Read backend configuration file
            with open(cls.CONFIG_FILE, 'r') as f:
                cls.tests_config = yaml.load(f)
            # Set configuration default values
            for k, v in cls.DEFAULTS.items():
                cls.tests_config.setdefault(k, v)
        return cls.tests_config

    @classmethod
    def setUpClass(cls):
        config = cls.ensure_config_loaded()

        if config['venv_sudo']:
            # NOTE(geguileo): For some drivers need to use a custom sudo script
            # to find virtualenv commands (ie: cinder-rtstool).
            path = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
            cls.root_helper = os.path.join(path, 'virtualenv-sudo.sh')
        else:
            cls.root_helper = 'sudo'
        cinderlib.setup(root_helper=cls.root_helper,
                        disable_logs=not config['logs'])

        # Initialize backends
        cls.backends = [cinderlib.Backend(**cfg) for cfg in
                        config['backends']]

        # Set current backend, by default is the first
        cls.backend = cls.backends[0]

    @classmethod
    def tearDownClass(cls):
        errors = []
        # Do the cleanup of the resources the tests haven't cleaned up already
        for backend in cls.backends:
            # For each of the volumes that haven't been deleted delete the
            # snapshots that are still there and then the volume.
            # NOTE(geguileo): Don't use volumes and snapshots iterables since
            # they are modified when deleting.
            for vol in list(backend.volumes):
                for snap in list(vol.snapshots):
                    try:
                        snap.delete()
                    except Exception as exc:
                        errors.append('Error deleting snapshot %s from volume '
                                      '%s: %s' % (snap.id, vol.id, exc))
                # Detach if locally attached
                if vol.local_attach:
                    try:
                        vol.detach()
                    except Exception as exc:
                        errors.append('Error detaching %s for volume %s %s: '
                                      '%s' % (vol.local_attach.path, vol.id,
                                              exc))

                # Disconnect any existing connections
                for conn in vol.connections:
                    try:
                        conn.disconnect()
                    except Exception as exc:
                        errors.append('Error disconnecting volume %s: %s' %
                                      (vol.id, exc))

                try:
                    vol.delete()
                except Exception as exc:
                    errors.append('Error deleting volume %s: %s' %
                                  (vol.id, exc))
        if errors:
            raise Exception('Errors on test cleanup: %s' % '\n\t'.join(errors))

    def _root_execute(self, *args, **kwargs):
        cmd = [self.root_helper]
        cmd.extend(args)
        cmd.extend("%s=%s" % (k, v) for k, v in kwargs.items())
        return subprocess.check_output(cmd, stderr=self.FNULL)

    def _create_vol(self, backend=None, **kwargs):
        if not backend:
            backend = self.backend

        vol_size = kwargs.setdefault('size', 1)
        name = kwargs.setdefault('name', backend.id)

        vol = backend.create_volume(**kwargs)

        self.assertEqual('available', vol.status)
        self.assertEqual(vol_size, vol.size)
        self.assertEqual(name, vol.display_name)
        self.assertIn(vol, backend.volumes)
        return vol

    def _create_snap(self, vol, **kwargs):
        name = kwargs.setdefault('name', vol.id)

        snap = vol.create_snapshot(name=vol.id)

        self.assertEqual('available', snap.status)
        self.assertEqual(vol.size, snap.volume_size)
        self.assertEqual(name, snap.display_name)

        self.assertIn(snap, vol.snapshots)
        return snap
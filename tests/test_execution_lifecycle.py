"""Controlled children and isolated workflow/worker tests, without scientific SDKs."""
import ast
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.workflow.CommandExecutor import CommandExecutor
from src.workflow.Logger import Logger
from src.workflow import _processes
from src.workflow import tasks


class Parameters:
    def __init__(self, root): self.ini_dir = Path(root) / 'ini'
    def get_parameters_from_json(self): return {'max_threads': 2}


def workflow_manager_class():
    """Replace UI/data constructors only; execute the real manager methods."""
    source = Path(__file__).resolve().parents[1] / 'src/workflow/WorkflowManager.py'
    replacements = {'streamlit': types.SimpleNamespace(session_state={})}
    for name, cls in [('ParameterManager', Parameters), ('FileManager', Mock), ('StreamlitUI', Mock)]:
        replacements['src.workflow.' + name] = types.SimpleNamespace(**{name: cls})
    spec = importlib.util.spec_from_file_location('src.workflow._manager_test', source)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, replacements): spec.loader.exec_module(module)
    return module.WorkflowManager


class ExecutionLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.logger = Logger(self.root)
        self.executor = CommandExecutor(self.root, self.logger, Parameters(self.root), settings={})

    def test_spawn_failure_and_batch_exception_are_observed(self):
        with self.assertRaises(FileNotFoundError):
            self.executor.run_multiple_commands([['/definitely/missing/openms-tool']])
        with patch.object(self.executor, 'run_command', return_value=False):
            with self.assertRaises(RuntimeError): self.executor.run_multiple_commands([['tool']])

    def test_nonzero_failure_and_pipe_tail_are_preserved(self):
        script = "import sys; [print('line'+str(i)) for i in range(1000)]; [print('err'+str(i), file=sys.stderr) for i in range(1000)]; sys.exit(7)"
        with self.assertRaises(subprocess.CalledProcessError) as error:
            self.executor.run_command([sys.executable, '-c', script])
        self.assertEqual(error.exception.returncode, 7)
        self.assertIn('err999', error.exception.stderr)
        self.assertNotIn('err0\n', error.exception.stderr)
        self.assertLessEqual(len(error.exception.stderr.splitlines()), 200)
        log = (self.root / 'logs/all.log').read_text()
        self.assertIn('line999', log); self.assertIn('err999', log)
        self.assertEqual(list(self.executor.pid_dir.iterdir()), [])

    def test_registration_failure_reaps_owned_process(self):
        children = []
        original = subprocess.Popen
        def spawn(*args, **kwargs):
            child = original(*args, **kwargs); children.append(child); return child
        with patch('src.workflow.CommandExecutor.subprocess.Popen', spawn), \
             patch('src.workflow.CommandExecutor.record_process', side_effect=OSError('read-only record storage')):
            with self.assertRaises(OSError):
                self.executor.run_command([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed); self.assertTrue(children[0].stderr.closed)

    def test_controlled_tool_and_descendant_cancel(self):
        child_file = self.root / 'child.json'
        script = ("import json, subprocess, sys, time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                  "open(sys.argv[1],'w').write(json.dumps({'pid':p.pid})); time.sleep(30)")
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(self.executor.run_command, [sys.executable, '-c', script, str(child_file)])
            deadline = time.monotonic() + 5
            while not child_file.exists() and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(child_file.exists())
            descendant = psutil.Process(json.loads(child_file.read_text())['pid'])
            try:
                self.assertTrue(self.manager().stop_workflow())
                with self.assertRaisesRegex(RuntimeError, 'cancelled'): result.result(timeout=5)
                self.assertTrue(not descendant.is_running() or descendant.status() == psutil.STATUS_ZOMBIE)
                with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                    self.executor.run_command([sys.executable, '-c', 'print(1)'])
            finally:
                # Only the child created in this test can be cleaned up here.
                if descendant.is_running():
                    try: descendant.kill()
                    except psutil.NoSuchProcess: pass

    def test_reader_exception_stops_sleeping_child(self):
        original = self.logger.log
        def fail_on_output(message, level=0):
            if level == 2: raise OSError("log storage failed")
            original(message, level)
        self.logger.log = fail_on_output
        for destination in ('sys.stdout', 'sys.stderr'):
            with self.subTest(destination=destination):
                started = time.monotonic()
                with self.assertRaises(OSError):
                    self.executor.run_command([sys.executable, '-c',
                        f'import time,sys; print("ready", file={destination}, flush=True); time.sleep(30)'])
                self.assertLess(time.monotonic() - started, 8)
                self.assertEqual(list(self.executor.pid_dir.iterdir()), [])

    def test_reused_identity_is_never_signalled(self):
        self.executor.pid_dir.mkdir()
        path = self.executor.pid_dir / '12345'
        path.write_text(json.dumps({'pid': 12345, 'create_time': 1.0}))
        replacement = Mock(pid=12345); replacement.create_time.return_value = 2.0
        with patch.object(_processes.psutil, 'Process', return_value=replacement):
            self.assertTrue(_processes.stop_processes(self.executor.pid_dir, self.logger))
        replacement.terminate.assert_not_called(); replacement.kill.assert_not_called()

    def test_unverifiable_legacy_record_is_retained(self):
        self.executor.pid_dir.mkdir(); path = self.executor.pid_dir / '12345'; path.touch()
        self.assertFalse(_processes.stop_processes(self.executor.pid_dir, self.logger))
        self.assertTrue(path.exists())

    def test_run_python_missing_file_and_current_interpreter(self):
        with self.assertRaises(FileNotFoundError): self.executor.run_python('no_such_script')
        script = self.root / 'tool.py'; script.write_text('DEFAULTS = []\n')
        with patch.object(self.executor, 'run_command', side_effect=RuntimeError('tool failure')) as run:
            with self.assertRaises(RuntimeError): self.executor.run_python(str(script))
        self.assertEqual(run.call_args.args[0][0], sys.executable)
        self.assertFalse((self.root / 'tool.json').exists())

    def test_online_settings_do_not_require_session_state(self):
        executor = CommandExecutor(self.root, self.logger, Parameters(self.root),
                                   settings={'online_deployment': True, 'max_threads': {'online': 3, 'local': 7}})
        self.assertEqual(executor._get_max_threads(), 3)
        with patch.dict(os.environ, {'REDIS_URL': 'redis://example.invalid'}):
            self.assertTrue(workflow_manager_class()._is_online_mode(types.SimpleNamespace(execution_settings={})))

    def manager(self):
        cls = workflow_manager_class(); manager = cls.__new__(cls)
        manager.workflow_dir = self.root; manager.logger = self.logger; manager.executor = self.executor
        manager._queue_manager = None
        return manager

    def test_queue_stop_needs_acknowledgement_and_keeps_uncertain_records(self):
        manager = self.manager(); queue = Mock()
        queue.load_job_id.return_value = 'saved-job'; queue.cancel_job.return_value = False
        manager._queue_manager = queue
        self.executor.pid_dir.mkdir(); record = self.executor.pid_dir / '12345'; record.touch()
        self.assertFalse(manager.stop_workflow())
        self.assertTrue(record.exists()); queue.clear_job_id.assert_not_called()
        self.assertNotIn('WORKFLOW CANCELLED', (self.root / 'logs/minimal.log').read_text())

    def test_acknowledged_queue_stop_retains_job_identity(self):
        from src.workflow.QueueManager import JobInfo, JobStatus
        manager = self.manager(); queue = Mock()
        queue.load_job_id.return_value = 'saved-job'; queue.cancel_job.return_value = True
        queue.get_job_info.return_value = JobInfo('saved-job', JobStatus.CANCELED, 0., '')
        manager._queue_manager = queue
        self.assertTrue(manager.stop_workflow())
        self.assertEqual(manager.get_workflow_status()['status'], 'canceled')
        queue.clear_job_id.assert_not_called()
        self.assertIn('WORKFLOW CANCELLED', (self.root / 'logs/minimal.log').read_text())

    def test_settings_fallback_reads_disk_without_streamlit(self):
        from src.workflow._settings import load_settings
        settings_path = self.root / 'settings.json'
        with patch('src.workflow._settings.Path', return_value=settings_path):
            self.assertEqual(load_settings(), {})
            settings_path.write_text('{"online_deployment": true, "max_threads": {"online": 3}}')
            self.assertEqual(load_settings()['max_threads']['online'], 3)

    def test_uncertain_submission_keeps_id_without_local_fallback(self):
        import fakeredis
        import src.workflow.QueueManager as queue_module
        from rq import Queue
        from rq.job import Job
        # The real enqueue can succeed before the client loses its reply.
        for lose_after_enqueue in (False, True):
            with self.subTest(lose_after_enqueue=lose_after_enqueue):
                manager = self.manager(); manager.name = 'Fixture'
                manager.execution_settings = {'online_deployment': True, 'max_threads': {'online': 2}}
                qm = queue_module.QueueManager.__new__(queue_module.QueueManager)
                qm._redis = fakeredis.FakeStrictRedis(); qm._queue = Queue(connection=qm._redis)
                qm._is_online = True; qm._default_timeout = 7200; qm._default_result_ttl = 86400
                manager._queue_manager = qm
                enqueue = qm._queue.enqueue
                def lost_reply(*args, **kwargs):
                    if lose_after_enqueue: enqueue(*args, **kwargs)
                    raise ConnectionError('acknowledgement lost')
                # The reconstructed test class module is intentionally isolated;
                # RQ only stores its name here and does not execute it.
                with patch.object(qm._queue, 'enqueue', side_effect=lost_reply), \
                     patch.object(manager, '_start_workflow_local') as local, \
                     patch.dict(manager._start_workflow_queued.__globals__, st=types.SimpleNamespace(warning=Mock())):
                    manager._start_workflow_queued()
                job_id = qm.load_job_id(self.root)
                self.assertTrue(job_id.startswith('workflow-'))
                local.assert_not_called()
                if lose_after_enqueue:
                    job = Job.fetch(job_id, connection=qm._redis)
                    self.assertEqual(job.id, job_id)
                    self.assertEqual(job.kwargs['settings']['max_threads']['online'], 2)
                    self.assertEqual(manager.get_workflow_status()['status'], 'queued')
                self.assertTrue((self.root / '.job_id').exists())
                with patch.object(qm, 'get_job_info', side_effect=ConnectionError('still offline')):
                    self.assertEqual(manager.get_workflow_status()['status'], 'unavailable')
                self.assertEqual(qm.load_job_id(self.root), job_id)

    def test_queue_transport_failure_preserves_job_id(self):
        manager = self.manager(); queue = Mock()
        queue.load_job_id.return_value = 'saved-job'
        queue.get_job_info.side_effect = ConnectionError('offline')
        manager._queue_manager = queue
        status = manager.get_workflow_status()
        self.assertEqual(status['status'], 'unavailable'); self.assertEqual(status['job_id'], 'saved-job')
        queue.clear_job_id.assert_not_called()

    @unittest.skipUnless('fork' in multiprocessing.get_all_start_methods(), 'controlled fork workflow test')
    def test_local_workflow_records_owner_before_execution_and_reaps_tool(self):
        manager = self.manager()
        marker = self.root / 'owner_seen'
        def execution():
            record = manager.executor.pid_dir / str(os.getpid())
            marker.write_text(record.read_text())
            return manager.executor.run_command([sys.executable, '-c', 'print("native child finished")'])
        manager.execution = execution
        before = set(multiprocessing.active_children())
        context = multiprocessing.get_context('fork')
        with patch('src.workflow.WorkflowManager.multiprocessing.Process', context.Process) if 'src.workflow.WorkflowManager' in sys.modules else patch.object(multiprocessing, 'Process', context.Process):
            manager._start_workflow_local()
        children = set(multiprocessing.active_children()) - before
        for child in children: child.join(timeout=8)
        self.assertTrue(marker.exists())
        self.assertIn('create_time', json.loads(marker.read_text()))
        self.assertFalse(manager.executor.pid_dir.exists())
        self.assertIn('WORKFLOW FINISHED', (self.root / 'logs/minimal.log').read_text())
        self.assertTrue(all(not child.is_alive() for child in children))

    def test_worker_false_none_and_exception_are_failures(self):
        replacements = {'src.workflow.ParameterManager': types.SimpleNamespace(ParameterManager=Parameters),
                        'src.workflow.FileManager': types.SimpleNamespace(FileManager=Mock)}
        for outcome in (False, None, RuntimeError('failure'), True):
            with self.subTest(outcome=outcome):
                class Workflow:
                    def execution(self):
                        if isinstance(outcome, Exception): raise outcome
                        return outcome
                replacements['workflow_fixture'] = types.SimpleNamespace(Workflow=Workflow)
                with patch.dict(sys.modules, replacements), patch('rq.get_current_job', return_value=None):
                    result = tasks.execute_workflow(str(self.root), 'Workflow', 'workflow_fixture',
                                                    settings={'online_deployment': True, 'max_threads': {'online': 1}})
                self.assertEqual(result['success'], outcome is True)
                self.assertEqual('WORKFLOW FINISHED' in (self.root / 'logs/minimal.log').read_text(), outcome is True)

    def test_real_workflows_reject_missing_inputs(self):
        source = Path(__file__).resolve().parents[1] / 'src/Workflow.py'
        tree = ast.parse(source.read_text())
        for name in ('TagWorkflow', 'DeconvWorkflow'):
            cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
            method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'execution')
            namespace = {}
            exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
            workflow = types.SimpleNamespace(params={'mzML-files': [], 'fasta-file': []}, logger=self.logger,
                                             file_manager=Mock())
            workflow.file_manager.get_files.side_effect = ValueError('No input selected')
            self.assertFalse(namespace['execution'](workflow))
            if name == 'TagWorkflow':
                workflow.file_manager.get_files.side_effect = [['input.mzML'], ValueError('No database')]
                self.assertFalse(namespace['execution'](workflow))

    def test_real_workflow_stops_after_failed_command(self):
        # Execute the actual method while avoiding imports of the scientific/UI stack.
        source = Path(__file__).resolve().parents[1] / 'src/Workflow.py'
        tree = ast.parse(source.read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'TagWorkflow')
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'execution')
        namespace = dict(vars(os.path), Path=Path, time=time, makedirs=os.makedirs,
                         rmtree=__import__('shutil').rmtree, copyfile=__import__('shutil').copyfile)
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
        parameters = {'FLASHTnT': {'prsm_fdr': .01}, 'few_proteins': False}
        executor = Mock(); executor.parameter_manager.get_parameters_from_json.return_value = parameters
        executor.run_topp.side_effect = subprocess.CalledProcessError(7, ['DecoyDatabase'])
        workflow = types.SimpleNamespace(workflow_dir=self.root / 'workflow', params={'mzML-files': ['x'], 'fasta-file': ['db']},
                                         logger=self.logger, file_manager=Mock(), executor=executor)
        workflow.file_manager.get_files.side_effect = [['input.mzML'], ['database.fasta']]
        with self.assertRaises(ExceptionGroup): namespace['execution'](workflow)
        self.assertEqual(executor.run_topp.call_count, 1)
        self.assertEqual(executor.run_topp.call_args.args[0], 'DecoyDatabase')
        workflow.file_manager.store_file.assert_not_called()


if __name__ == '__main__': unittest.main()

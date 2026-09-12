"""Submission ownership and lost-acknowledgement tests; no scientific inputs."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import fakeredis
import pytest
from redis.exceptions import LockNotOwnedError
from rq import Queue
from rq.job import Job

from test_execution_lifecycle import workflow_manager_class
from src.workflow.QueueManager import JobInfo, JobStatus, QueueManager


def manager_at(directory, queue):
    cls = workflow_manager_class()
    manager = cls.__new__(cls)
    manager.name = 'Fixture'
    manager.workflow_dir = directory
    manager.execution_settings = {'online_deployment': True}
    manager.parameter_manager = Mock()
    manager.executor = SimpleNamespace(cancel_file=directory / '.cancelled', pid_dir=directory / 'pids')
    manager._queue_manager = queue
    return manager


@pytest.fixture
def queue():
    manager = QueueManager.__new__(QueueManager)
    manager._redis = fakeredis.FakeStrictRedis()
    manager._queue = Queue(QueueManager.QUEUE_NAME, connection=manager._redis)
    manager._is_online = True
    manager._default_timeout = 7200
    manager._default_result_ttl = 86400
    # fakeredis without its optional Lua dependency cannot release Redis locks.
    # The real primitive is exercised separately against dedicated live Redis.
    manager.submission_lock = MagicMock(return_value=MagicMock())
    return manager


@pytest.mark.parametrize('status', [JobStatus.QUEUED, JobStatus.STARTED, JobStatus.DEFERRED])
def test_active_submission_preserves_identity_parameters_and_cancel_marker(tmp_path, queue, status):
    queue.store_job_id(tmp_path, 'active')
    manager = manager_at(tmp_path, queue)
    manager.executor.cancel_file.touch()
    with patch.object(queue, 'get_job_info', return_value=JobInfo('active', status, 0, '')), \
         patch.object(queue, 'submit_job') as submit:
        manager._start_workflow_queued()
    assert queue.load_job_id(tmp_path) == 'active'
    assert manager.executor.cancel_file.exists()
    manager.parameter_manager.save_parameters.assert_not_called()
    submit.assert_not_called()


@pytest.mark.parametrize('status', [JobStatus.FINISHED, JobStatus.FAILED, JobStatus.CANCELED])
def test_confirmed_terminal_job_allows_deliberate_rerun(tmp_path, queue, status):
    queue.store_job_id(tmp_path, 'old')
    manager = manager_at(tmp_path, queue)
    manager.executor.cancel_file.touch()
    with patch.object(queue, 'get_job_info', return_value=JobInfo('old', status, 1, '')):
        manager._start_workflow_queued()
    assert queue.load_job_id(tmp_path) != 'old'
    assert queue._queue.job_ids == [queue.load_job_id(tmp_path)]
    assert not manager.executor.cancel_file.exists()
    manager.parameter_manager.save_parameters.assert_called_once()


@pytest.mark.parametrize('lookup', [None, ConnectionError('Redis transport failure')])
def test_missing_or_unreachable_saved_job_requires_reconciliation(tmp_path, queue, lookup):
    queue.store_job_id(tmp_path, 'uncertain')
    manager = manager_at(tmp_path, queue)
    options = {'side_effect': lookup} if isinstance(lookup, Exception) else {'return_value': lookup}
    with patch.object(queue, 'get_job_info', **options), patch.object(queue, 'submit_job') as submit:
        manager._start_workflow_queued()
        status = manager.get_workflow_status()
    assert status['status'] == 'unavailable' and status['running']
    assert queue.load_job_id(tmp_path) == 'uncertain'
    manager.parameter_manager.save_parameters.assert_not_called()
    submit.assert_not_called()


@pytest.mark.parametrize('enqueue_happened', [False, True])
def test_lost_ack_never_resubmits_with_a_new_identity(tmp_path, queue, enqueue_happened):
    manager = manager_at(tmp_path, queue)
    enqueue = queue._queue.enqueue
    def lose_reply(*args, **kwargs):
        if enqueue_happened:
            enqueue(*args, **kwargs)
        raise ConnectionError('Enqueue reply lost')
    with patch.object(queue._queue, 'enqueue', side_effect=lose_reply) as call:
        manager._start_workflow_queued()
        saved = queue.load_job_id(tmp_path)
        manager._start_workflow_queued()
    assert saved and queue.load_job_id(tmp_path) == saved
    assert call.call_count == 1
    assert len(queue._queue.job_ids) == int(enqueue_happened)
    manager.parameter_manager.save_parameters.assert_called_once()


def test_expired_lookup_lease_prevents_all_submission_mutations(tmp_path, queue):
    queue.store_job_id(tmp_path, 'old')
    manager = manager_at(tmp_path, queue)
    manager.executor.cancel_file.touch()
    lock = queue.submission_lock.return_value.__enter__.return_value
    lock.reacquire.side_effect = LockNotOwnedError('Lease expired during status lookup')
    with patch.object(queue, 'get_job_info', return_value=JobInfo('old', JobStatus.FINISHED, 1, '')), \
         patch.object(queue, 'submit_job') as submit:
        manager._start_workflow_queued()
    assert queue.load_job_id(tmp_path) == 'old'
    assert manager.executor.cancel_file.exists()
    manager.parameter_manager.save_parameters.assert_not_called()
    submit.assert_not_called()


def test_terminal_job_with_remaining_owned_processes_cannot_restart(tmp_path, queue):
    queue.store_job_id(tmp_path, 'old')
    manager = manager_at(tmp_path, queue)
    manager.executor.pid_dir.mkdir()
    (manager.executor.pid_dir / 'uncertain-owner').touch()
    with patch.object(queue, 'get_job_info', return_value=JobInfo('old', JobStatus.FINISHED, 1, '')), \
         patch.object(queue, 'submit_job') as submit:
        manager._start_workflow_queued()
    assert queue.load_job_id(tmp_path) == 'old'
    submit.assert_not_called()


def test_online_queue_unavailable_never_falls_back_to_local(tmp_path):
    manager = manager_at(tmp_path, None)
    with patch.object(manager, '_start_workflow_local') as local:
        manager.start_workflow()
    local.assert_not_called()
    manager.parameter_manager.save_parameters.assert_not_called()


@pytest.mark.skipif(not os.environ.get('FLASHAPP_TEST_REDIS_URL'), reason='Dedicated live Redis not requested')
def test_two_live_redis_starts_enqueue_exactly_one_job(tmp_path, monkeypatch):
    monkeypatch.setenv('REDIS_URL', os.environ['FLASHAPP_TEST_REDIS_URL'])
    queue = QueueManager(settings={'online_deployment': True})
    assert queue.is_available and queue._redis.dbsize() == 0, 'Dedicated empty Redis database required'
    first = manager_at(tmp_path, queue)
    second = manager_at(tmp_path, queue)
    entered = threading.Event()
    release = threading.Event()
    submit = queue.submit_job
    def hold_submission(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return submit(*args, **kwargs)
    with patch.object(queue, 'submit_job', side_effect=hold_submission), ThreadPoolExecutor(2) as pool:
        running = pool.submit(first._start_workflow_queued)
        try:
            assert entered.wait(timeout=5)
            pool.submit(second._start_workflow_queued).result(timeout=3)
        finally:
            release.set()
        running.result(timeout=5)
    job_id = queue.load_job_id(tmp_path)
    try:
        assert queue._queue.job_ids == [job_id]
        first.parameter_manager.save_parameters.assert_called_once()
        second.parameter_manager.save_parameters.assert_not_called()
        assert queue.get_job_info(job_id).status == JobStatus.QUEUED
        assert not list(queue._redis.scan_iter(f'{QueueManager.QUEUE_NAME}:submission:*'))

        expired_dir = tmp_path / 'expired'
        expired_dir.mkdir()
        queue.store_job_id(expired_dir, 'terminal')
        expired = manager_at(expired_dir, queue)
        expired.executor.cancel_file.touch()
        lock = queue._redis.lock(f'{QueueManager.QUEUE_NAME}:submission:expiry-test',
                                 timeout=.01, blocking=False)
        def slow_lookup(_):
            time.sleep(.03)
            return JobInfo('terminal', JobStatus.FINISHED, 1, '')
        with patch.object(queue, 'submission_lock', return_value=lock), \
             patch.object(queue, 'get_job_info', side_effect=slow_lookup), \
             patch.object(queue, 'submit_job') as enqueue:
            expired._start_workflow_queued()
        assert queue.load_job_id(expired_dir) == 'terminal'
        assert expired.executor.cancel_file.exists()
        expired.parameter_manager.save_parameters.assert_not_called()
        enqueue.assert_not_called()
    finally:
        if job_id:
            Job.fetch(job_id, connection=queue._redis).delete()

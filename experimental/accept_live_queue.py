"""Exercise real Redis/RQ success, failure and owned-child cancellation.

Requires a dedicated Redis instance via REDIS_URL; never run against production.
Starts one temporary RQ SpawnWorker and removes it on exit. Redis is managed by the caller.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


class ControlledWorkflow:
    share_cache = False

    def execution(self):
        mode = self.params['mode']
        if mode == 'failure':
            return False
        if mode == 'cancel':
            script = (
                "import json,os,subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
                "open(sys.argv[1],'w').write(json.dumps({'parent':os.getpid(),'child':p.pid})); "
                "time.sleep(120)")
            self.executor.run_command([sys.executable, '-c', script, str(self.workflow_dir / 'children.json')])
        else:
            self.executor.run_command([sys.executable, '-c', 'print("controlled live queue success")'])
        return True


def wait_for(check, seconds=45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.05)
    raise TimeoutError('Live queue condition did not occur')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New directory for logs and acceptance results')
    args = parser.parse_args()
    app_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(app_root))
    from rq.job import Job
    import psutil
    from src.workflow.QueueManager import QueueManager
    from src.workflow.WorkflowManager import WorkflowManager
    from src.workflow.CommandExecutor import CommandExecutor
    from src.workflow.Logger import Logger
    from src.workflow.ParameterManager import ParameterManager
    from src.workflow.tasks import execute_workflow

    root = args.output.resolve()
    root.mkdir(exist_ok=False)
    settings = {'online_deployment': True, 'max_threads': {'online': 1}}
    queue = QueueManager(settings)
    if not queue.is_available or queue._redis.dbsize() != 0:
        raise ValueError('A reachable, empty, dedicated Redis database is required')
    worker_log = (root / 'worker.log').open('w')
    worker = subprocess.Popen([str(Path(sys.executable).parent / 'rq'), 'worker',
                               '--worker-class', 'rq.worker.SpawnWorker', '--url', os.environ['REDIS_URL'],
                               '--name', 'flashapp-live-acceptance', '--max-idle-time', '60',
                               QueueManager.QUEUE_NAME], stdout=worker_log, stderr=subprocess.STDOUT,
                              cwd=app_root, env=dict(os.environ, PYTHONPATH=str(app_root)))
    report = {'worker_class': 'rq.worker.SpawnWorker', 'redis_version': queue._redis.info()['redis_version']}
    try:
        wait_for(lambda: queue.get_queue_stats().get('workers') == 1)
        for mode in ('success', 'failure', 'cancel'):
            workflow_dir = root / mode
            workflow_dir.mkdir()
            (workflow_dir / 'params.json').write_text(json.dumps({'mode': mode}))
            started = time.perf_counter()
            job_id = queue.submit_job(execute_workflow,
                args=(str(workflow_dir), 'ControlledWorkflow', 'experimental.accept_live_queue', settings),
                job_id='flashapp-live-' + mode, timeout=180)
            assert job_id
            queue.store_job_id(workflow_dir, job_id)
            job = Job.fetch(job_id, connection=queue._redis)
            if mode == 'cancel':
                marker = workflow_dir / 'children.json'
                wait_for(lambda: marker.exists() and any((workflow_dir / 'pids').iterdir()))
                children = [psutil.Process(pid) for pid in json.loads(marker.read_text()).values()]
                manager = object.__new__(WorkflowManager)
                manager.workflow_dir = workflow_dir
                manager.logger = Logger(workflow_dir)
                manager.executor = CommandExecutor(workflow_dir, manager.logger, ParameterManager(workflow_dir), settings)
                manager._queue_manager = queue
                assert manager.stop_workflow()
                wait_for(lambda: all(not child.is_running() or child.status() == psutil.STATUS_ZOMBIE for child in children))
                assert manager.get_workflow_status()['status'] == 'canceled'
                assert not manager.get_workflow_status()['running']
                assert 'WORKFLOW CANCELLED' in (workflow_dir / 'logs/minimal.log').read_text()
            else:
                wait_for(lambda: job.get_status(refresh=True) in ('finished', 'failed'))
                assert job.result['success'] is (mode == 'success'), job.result
            report[mode] = {'wall_seconds': time.perf_counter() - started,
                            'job_status': job.get_status(refresh=True),
                            'app_status': queue.get_job_info(job_id).status.value, 'result': job.result}
        (root / 'acceptance.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
        worker_log.close()


if __name__ == '__main__':
    main()

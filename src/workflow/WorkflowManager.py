from pathlib import Path
from typing import Optional
from .Logger import Logger
from .ParameterManager import ParameterManager
from .CommandExecutor import CommandExecutor
from .StreamlitUI import StreamlitUI
from .FileManager import FileManager
import multiprocessing
import shutil
import uuid
import traceback
import streamlit as st
from ._processes import record_process, stop_processes
from ._settings import load_settings, online_mode

class WorkflowManager:
    # Core workflow logic using the above classes
    def __init__(self, name: str, workspace: str, share_cache: bool = False):
        self.name = name
        self.workflow_dir = Path(workspace, name.replace(" ", "-").lower())

        if share_cache:
            cache_path = Path(workspace, 'cache')
        else:
            cache_path = Path(self.workflow_dir, 'cache')
        
        self.file_manager = FileManager(self.workflow_dir, cache_path)
        self.logger = Logger(self.workflow_dir)
        self.parameter_manager = ParameterManager(self.workflow_dir, workflow_name=name)
        settings = st.session_state.get("settings")
        self.execution_settings = dict(load_settings() if settings is None else settings)
        self.executor = CommandExecutor(self.workflow_dir, self.logger, self.parameter_manager, self.execution_settings)
        self.ui = StreamlitUI(self.workflow_dir, self.logger, self.executor, self.parameter_manager)
        self.params = self.parameter_manager.get_parameters_from_json()

        # Initialize queue manager for online mode
        self._queue_manager: Optional['QueueManager'] = None
        if self._is_online_mode():
            self._init_queue_manager()

    def _is_online_mode(self) -> bool:
        """Check if running in online deployment mode"""
        return online_mode(self.execution_settings)

    def _init_queue_manager(self) -> None:
        """Initialize queue manager for online mode"""
        try:
            from .QueueManager import QueueManager
            self._queue_manager = QueueManager(settings=self.execution_settings)
        except ImportError:
            pass  # Queue not available, will use fallback

    def start_workflow(self) -> None:
        """
        Starts the workflow process.

        Online mode: Submits to Redis queue
        Local mode: Spawns multiprocessing.Process (existing behavior)
        """
        # Flush the latest session state to params.json so the worker
        # (queued) or the forked child (local) sees the values the user
        # just selected, not stale disk state from a previous fragment run.
        self.parameter_manager.save_parameters()
        if self._queue_manager and self._queue_manager.is_available:
            self._start_workflow_queued()
        else:
            self._start_workflow_local()

    def _start_workflow_queued(self) -> None:
        """Submit workflow to Redis queue (online mode)"""
        from .tasks import execute_workflow

        # Generate unique job ID based on workflow directory
        job_id = f"workflow-{self.workflow_dir.name}-{uuid.uuid4().hex}"

        # Clear cancellation only when starting a new run.
        self.executor.cancel_file.unlink(missing_ok=True)
        # Enqueue acknowledgement can be lost after Redis accepted the job.
        # Preserve the caller-generated identity before making that request.
        self._queue_manager.store_job_id(self.workflow_dir, job_id)
        submitted_id = self._queue_manager.submit_job(
            func=execute_workflow,
            kwargs={
                "workflow_dir": str(self.workflow_dir),
                "workflow_class": self.__class__.__name__,
                "workflow_module": self.__class__.__module__,
                "settings": {key: self.execution_settings[key] for key in
                             ("online_deployment", "max_threads") if key in self.execution_settings},
            },
            job_id=job_id,
            description=f"Workflow: {self.name}"
        )

        if submitted_id is None:
            st.warning("Queue submission could not be confirmed. The job ID is retained while its status is checked.")

    def _start_workflow_local(self) -> None:
        """Start workflow as local process (existing behavior for local mode)"""
        # Catch double presses of the button while app is in frozen state
        if self.executor.pid_dir.exists():
            return

        # Delete the log file if it already exists
        shutil.rmtree(Path(self.workflow_dir, "logs"), ignore_errors=True)
        # Establish ownership storage before the child can launch a tool.
        self.executor.pid_dir.mkdir(parents=True)
        self.executor.cancel_file.unlink(missing_ok=True)
        ready = multiprocessing.Event()
        workflow_process = multiprocessing.Process(target=self.workflow_process, args=(ready,))
        try:
            workflow_process.start()
            record_process(self.executor.pid_dir, workflow_process.pid)
            ready.set()
        except BaseException:
            if workflow_process.pid is not None and workflow_process.is_alive():
                workflow_process.terminate()
                workflow_process.join(timeout=3)
                if workflow_process.is_alive():
                    workflow_process.kill()
                    workflow_process.join()
            stop_processes(self.executor.pid_dir, self.logger)
            raise

    def workflow_process(self, ready=None) -> None:
        """
        Workflow process. Logs start and end of the workflow and calls the execution method where all steps are defined.
        """
        if ready is not None and not ready.wait(timeout=10):
            self.logger.log("ERROR: Workflow ownership registration timed out")
            return
        try:
            self.logger.log("STARTING WORKFLOW")
            results_dir = Path(self.workflow_dir, "results")
            if results_dir.exists():
                shutil.rmtree(results_dir)
            results_dir.mkdir(parents=True)
            success = self.execution()
            if success is not True:
                raise RuntimeError("Workflow did not complete successfully")
            self.logger.log("WORKFLOW FINISHED")
        except Exception as e:
            self.logger.log(f"ERROR: {e}")
            self.logger.log("".join(traceback.format_exception(e)))
        # Delete pid dir path to indicate workflow is done
        shutil.rmtree(self.executor.pid_dir, ignore_errors=True)

    def get_workflow_status(self) -> dict:
        """
        Get current workflow execution status.

        Returns:
            Dictionary with status information including:
            - running: bool indicating if workflow is running
            - status: string status (queued, started, finished, failed, idle)
            - progress: float 0-1 for queue jobs, None for local
            - current_step: string description of current step
            - job_id: job ID for queue jobs, None for local
            - queue_position: position in queue (1-indexed), None if not queued
            - queue_length: total jobs in queue, None if not queued
        """
        # Check queue status first (online mode)
        if self._queue_manager:
            job_id = self._queue_manager.load_job_id(self.workflow_dir)
            if job_id:
                try:
                    job_info = self._queue_manager.get_job_info(job_id)
                except Exception as error:
                    # A transport failure is not proof that the saved job vanished.
                    return {"running": True, "status": "unavailable", "job_id": job_id,
                            "progress": None, "current_step": "Queue connection unavailable",
                            "queue_position": None, "queue_length": None, "error": str(error)}
                if job_info:
                    is_running = job_info.status.value in ["queued", "started"]
                    return {
                        "running": is_running,
                        "status": job_info.status.value,
                        "progress": job_info.progress,
                        "current_step": job_info.current_step,
                        "job_id": job_id,
                        "queue_position": job_info.queue_position,
                        "queue_length": job_info.queue_length,
                        "enqueued_at": job_info.enqueued_at,
                        "started_at": job_info.started_at,
                        "result": job_info.result,
                        "error": job_info.error,
                    }
                else:
                    # Job not found, clear the stored job ID
                    self._queue_manager.clear_job_id(self.workflow_dir)

        # Fallback: check PID files (local mode)
        pid_dir = self.executor.pid_dir
        if pid_dir.exists() and list(pid_dir.iterdir()):
            return {
                "running": True,
                "status": "running",
                "progress": None,
                "current_step": None,
                "job_id": None,
                "queue_position": None,
                "queue_length": None,
            }

        return {
            "running": False,
            "status": "idle",
            "progress": None,
            "current_step": None,
            "job_id": None,
            "queue_position": None,
            "queue_length": None,
        }

    def stop_workflow(self) -> bool:
        """
        Stop a running workflow.

        Writes a "WORKFLOW CANCELLED" marker to the log so the static
        run-page display can render a "Workflow was cancelled" message
        instead of "Errors occurred". Cleans up the worker-side pid_dir
        left behind when the RQ worker is force-stopped, so a subsequent
        get_workflow_status does not flip running back to True via the
        local-mode fallback.

        .job_id is intentionally left in place: get_job_info will report
        the canceled status to the UI so _show_queue_status can render the
        Cancelled pill. Resubmission overwrites it; RQ's result_ttl
        eventually evicts the job and get_workflow_status self-heals.

        Returns:
            True when owned children are stopped and any queued job stop is acknowledged.
        """
        self.executor.cancel_file.touch()
        # Stop owned tools before interrupting an RQ worker: it may not run finally.
        children_stopped = stop_processes(self.executor.pid_dir, self.logger)
        if self._queue_manager:
            job_id = self._queue_manager.load_job_id(self.workflow_dir)
            if job_id:
                queue_stopped = self._queue_manager.cancel_job(job_id)
                if queue_stopped and children_stopped:
                    self.logger.log("WORKFLOW CANCELLED")
                    return True
                return False
        if children_stopped:
            self.logger.log("WORKFLOW CANCELLED")
        return children_stopped

    def _stop_local_workflow(self) -> bool:
        """Stop only verified process identities, retaining records on failure."""
        self.executor.cancel_file.touch()
        stopped = stop_processes(self.executor.pid_dir, self.logger)
        if stopped:
            self.logger.log("WORKFLOW CANCELLED")
        return stopped

    def show_file_upload_section(self) -> None:
        """
        Shows the file upload section of the UI with content defined in self.upload().
        """
        self.ui.file_upload_section(self.upload)
        
    def show_parameter_section(self) -> None:
        """
        Shows the parameter section of the UI with content defined in self.configure().
        """
        self.ui.parameter_section(self.configure)

    def show_execution_section(self) -> None:
        """
        Shows the execution section of the UI with content defined in self.execution().
        """
        self.ui.execution_section(
            start_workflow_function=self.start_workflow,
            get_status_function=self.get_workflow_status,
            stop_workflow_function=self.stop_workflow
        )
        
    def show_results_section(self) -> None:
        """
        Shows the results section of the UI with content defined in self.results().
        """
        self.ui.results_section(self.results)

    def upload(self) -> None:
        """
        Add your file upload widgets here
        """
        ###################################
        # Add your file upload widgets here
        ###################################
        pass

    def configure(self) -> None:
        """
        Add your input widgets here
        """
        ###################################
        # Add your input widgets here
        ###################################
        pass

    def execution(self) -> bool:
        """
        Add your workflow steps here.
        Returns True on success, False on error.
        """
        ###################################
        # Add your workflow steps here
        ###################################
        return True

    def results(self) -> None:
        """
        Display results here
        """
        ###################################
        # Display results here
        ###################################
        pass
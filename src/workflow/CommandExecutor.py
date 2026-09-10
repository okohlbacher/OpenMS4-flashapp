import time
import subprocess
import psutil
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION
from typing import TYPE_CHECKING
from pathlib import Path
from .Logger import Logger
from ._processes import record_process, stop_processes, stop_process_trees
from ._settings import load_settings, online_mode
if TYPE_CHECKING:
    from .ParameterManager import ParameterManager
import sys
import importlib.util
import json

class CommandExecutor:
    """
    Manages the execution of external shell commands such as OpenMS TOPP tools within a Streamlit application.

    This class provides a structured approach to executing shell commands, capturing
    their output, and handling errors. It is designed to facilitate running both single
    commands and batches of commands in parallel, leveraging Python's subprocess module
    for execution.
    """
    # Methods for running commands and logging
    def __init__(self, workflow_dir: Path, logger: Logger, parameter_manager: "ParameterManager", settings=None):
        self.pid_dir = Path(workflow_dir, "pids")
        self.logger = logger
        self.parameter_manager = parameter_manager
        self.settings = load_settings() if settings is None else dict(settings)
        self.cancel_file = Path(workflow_dir, ".cancelled")

    def _get_max_threads(self) -> int:
        """
        Get max threads for current deployment mode.

        In local mode, reads from parameter manager (persisted params.json).
        In online mode, uses the configured value directly from settings.

        Returns:
            int: Maximum number of threads to use for parallel processing (minimum 1).
        """
        settings = self.settings
        max_threads_config = settings.get("max_threads", {"local": 4, "online": 2})

        if online_mode(settings):
            value = max_threads_config.get("online", 2)
        else:
            default = max_threads_config.get("local", 4)
            params = self.parameter_manager.get_parameters_from_json()
            value = params.get("max_threads", default)

        return max(1, int(value))

    def run_multiple_commands(
        self, commands: list[str]
    ) -> bool:
        """
        Executes multiple shell commands concurrently in separate threads.

        This method leverages threading to run each command in parallel, improving
        efficiency for batch command execution. The number of concurrent commands
        is limited by the max_threads setting, which is distributed between
        parallel command execution and per-tool thread allocation.

        Args:
            commands (list[str]): A list where each element is a list representing
                                        a command and its arguments.

        Returns:
            bool: True if all commands succeeded; command failures raise an exception.
        """
        # Get thread settings and calculate distribution
        max_threads = self._get_max_threads()
        num_commands = len(commands)
        parallel_commands = min(num_commands, max_threads)

        # Log the start of command execution
        self.logger.log(f"Running {num_commands} commands (max {parallel_commands} parallel, {max_threads} total threads)...", 1)
        start_time = time.time()

        if not commands:
            return True
        with ThreadPoolExecutor(max_workers=parallel_commands) as pool:
            futures = [pool.submit(self.run_command, command) for command in commands]
            for future in futures:
                if not future.result():
                    raise RuntimeError("A workflow command failed")

        # Calculate and log the total execution time
        end_time = time.time()
        self.logger.log(f"Total time to run {num_commands} commands: {end_time - start_time:.2f} seconds", 1)

        return True

    def run_command(self, command: list[str]) -> bool:
        """
        Executes a specified shell command and logs its execution details.

        Args:
            command (list[str]): The shell command to execute, provided as a list of strings.

        Raises:
            Exception: If the command execution results in any errors.
        """
        # Ensure all command parts are strings
        command = [str(c) for c in command]
        if self.cancel_file.exists():
            raise RuntimeError("Workflow was cancelled")
        self.pid_dir.mkdir(parents=True, exist_ok=True)

        # Log the execution start
        self.logger.log(f"Running command:\n"+' '.join(command)+"\nWaiting for command to finish...", 1)   
        start_time = time.time()
        
        # Execute the command with real-time output capture
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
            universal_newlines=True
        )
        pid_file_path = None
        owned_process = None
        # The detailed log receives every line; retain only a bounded error tail.
        stderr_buffer = deque(maxlen=200)
        try:
            try:
                owned_process = psutil.Process(process.pid)
                pid_file_path = record_process(self.pid_dir, process.pid)
            except psutil.NoSuchProcess:
                if process.poll() is None:
                    raise
            if self.cancel_file.exists():
                raise RuntimeError("Workflow was cancelled")
            self._stream_output(process, stderr_buffer)
            process.wait()
        finally:
            try:
                if process.poll() is None:
                    self._terminate_process(process, owned_process)
            finally:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                if pid_file_path is not None:
                    pid_file_path.unlink(missing_ok=True)

        end_time = time.time()
        execution_time = end_time - start_time

        # Log completion
        self.logger.log(f"Process finished:\n"+' '.join(command)+f"\nTotal time to run command: {execution_time:.2f} seconds", 1)

        # Another thread may reap the child while cancelling it; never interpret
        # that Popen fallback returncode as successful workflow completion.
        if self.cancel_file.exists():
            raise RuntimeError("Workflow was cancelled")
        if process.returncode != 0:
            # Write buffered stderr to minimal log only on failure
            for line in stderr_buffer:
                self.logger.log(f"STDERR: {line}", 0)
            self.logger.log(f"ERROR: Command failed with exit code {process.returncode}: {command[0]}", 0)
            raise subprocess.CalledProcessError(process.returncode, command, stderr="\n".join(stderr_buffer))
        return True

    def _stream_output(self, process: subprocess.Popen, stderr_buffer: list[str]) -> None:
        """
        Streams stdout and stderr from a running process in real-time to the logger.
        This method runs in the workflow process, not the GUI thread, so it's safe to block.

        Stderr is buffered and only logged to the detailed log (level 2) during execution.
        The caller is responsible for writing buffered stderr to minimal log if the process fails.

        Args:
            process: The subprocess.Popen object to stream from
            stderr_buffer: A list to accumulate stderr lines for conditional logging
        """
        def read_output(stream, is_stderr):
            with stream:
                for line in iter(stream.readline, ''):
                    message = line.rstrip()
                    if is_stderr:
                        stderr_buffer.append(message[-4096:])
                        message = f"STDERR: {message}"
                    self.logger.log(message, 2)

        with ThreadPoolExecutor(max_workers=2) as readers:
            futures = [readers.submit(read_output, process.stdout, False),
                       readers.submit(read_output, process.stderr, True)]
            done, _ = wait(futures, return_when=FIRST_EXCEPTION)
            if any(future.exception() is not None for future in done):
                # Closing one failed reader is insufficient: the other may block
                # until a sleeping child exits. Terminate before joining readers.
                self._terminate_process(process)
            for future in futures:
                future.result()

    def _terminate_process(self, process, owned_process=None):
        try:
            try:
                owner = owned_process or psutil.Process(process.pid)
                stop_process_trees([owner], self.logger)
            except psutil.NoSuchProcess:
                pass
        finally:
            # Popen owns this direct child even when identity/bookkeeping failed.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def run_topp(self, tool: str, input_output: dict, custom_params: dict = {}, tool_instance_name: str = None) -> bool:
        """
        Constructs and executes commands for the specified tool OpenMS TOPP tool based on the given
        input and output configurations. Ensures that all input/output file lists
        are of the same length, or single strings, to maintain consistency in command
        execution.
        In many tools, a single input file is processed to produce a single output file.
        When dealing with lists of input or output files, the convention is that
        files are paired based on their order. For instance, the n-th input file is
        assumed to correspond to the n-th output file, maintaining a structured
        relationship between input and output data.
        Supports executing commands either as single or multiple processes
        based on the input size.

        Args:
            tool (str): The executable name or path of the tool.
            input_output (dict): A dictionary specifying the input/output parameter names (as key) and their corresponding file paths (as value).
            custom_params (dict): A dictionary of custom parameters to pass to the tool.
            tool_instance_name (str, optional): Key for ``params.json`` when it differs
                from ``tool`` (e.g. multiple instances). Defaults to ``tool``.
            Custom parameters whose keys appear in the tool's ParamXML ``type="bool"``
            entries are passed as valueless CLI flags (``-name`` only when enabled).

        Returns:
            bool: True if all commands succeeded; command failures raise an exception.

        Raises:
            ValueError: If the lengths of input/output file lists are inconsistent,
                        except for single string inputs.
        """
        from .ParameterManager import bool_param_paths_from_param_xml_ini
        # check input: any input lists must be same length, other items can be a single string
        # e.g. input_mzML : [list of n mzML files], output_featureXML : [list of n featureXML files], input_database : database.tsv
        io_lengths = [len(v) for v in input_output.values() if len(v) > 1]

        if len(set(io_lengths)) > 1:
            raise ValueError(f"ERROR in {tool} input/output.\nFile list lengths must be 1 and/or the same. They are {io_lengths}.")

        if len(io_lengths) == 0:  # all inputs/outputs are length == 1
            n_processes = 1
        else:
            n_processes = max(io_lengths)

        # Calculate threads per command based on max_threads setting
        max_threads = self._get_max_threads()
        parallel_commands = min(n_processes, max_threads)
        threads_per_command = max(1, max_threads // parallel_commands)

        commands = []

        params = self.parameter_manager.get_parameters_from_json()

        topp_tool_ini_path = Path(self.parameter_manager.ini_dir, f"{tool}.ini")
        # Keys of type="bool" in the .ini: TOPP treats these as on/off flags (omit value when off)
        topp_bool_flag_param_keys = (
            bool_param_paths_from_param_xml_ini(topp_tool_ini_path, tool)
            if topp_tool_ini_path.exists()
            else set()
        )
        # Construct commands for each process
        for i in range(n_processes):
            command = [tool]
            # Add input/output files
            for k in input_output.keys():
                # add key as parameter name
                command += [f"-{k}"]
                # get value from input_output dictionary
                value = input_output[k]
                # when multiple input/output files exist (e.g., multiple mzMLs and featureXMLs), but only one additional input file (e.g., one input database file)
                if len(value) == 1:
                    i = 0
                # when the entry is a list of collected files to be passed as one [["sample1", "sample2"]]
                if isinstance(value[i], list):
                    command += value[i]
                # standard case, files was a list of strings, take the file name at index
                else:
                    command += [value[i]]
            # Add non-default TOPP tool parameters
            if tool in params.keys():
                for k, v in params[tool].items():
                    # Boolean flag handling. A parameter is emitted as a
                    # valueless TOPP on/off flag when EITHER the tool's ParamXML
                    # .ini marks the key type="bool" (upstream key-based
                    # detection via topp_bool_flag_param_keys) OR the stored
                    # value is itself boolean. The value-based branch is the
                    # fallback FLASHApp relies on: checkbox widgets persist a
                    # Python bool (True/False) to params.json, and pyOpenMS /
                    # presets may surface the 'true'/'false' string form. This
                    # keeps flags correct even when the .ini bool set is empty
                    # (e.g. the .ini was not written).
                    is_bool_value = isinstance(v, bool) or (
                        isinstance(v, str) and v.lower() in ("true", "false")
                    )
                    if (k in topp_bool_flag_param_keys and v != "") or is_bool_value:
                        # CLI flag: include "-k" only when enabled, never a value.
                        if isinstance(v, str):
                            is_enabled = v.lower() == "true"
                        else:
                            is_enabled = bool(v)
                        if is_enabled:
                            command += [f"-{k}"]
                        continue
                    command += [f"-{k}"]
                    # Skip only empty strings (pass flag with no value)
                    # Note: 0 and 0.0 are valid values, so use explicit check
                    if v != "" and v is not None:
                        if isinstance(v, str) and "\n" in v:
                            command += v.split("\n")
                        else:
                            command += [str(v)]
            # Add custom parameters
            for k, v in custom_params.items():
                command += [f"-{k}"]
                
                # Skip only empty strings (pass flag with no value)
                # Note: 0 and 0.0 are valid values, so use explicit check
                if v != "" and v is not None:
                    if isinstance(v, list):
                        command += [str(x) for x in v]
                    else:
                        command += [str(v)]
            # Add threads parameter for TOPP tools
            command += ["-threads", str(threads_per_command)]
            commands.append(command)

            # check if a ini file has been written, if yes use it (contains custom defaults)
            ini_path = Path(self.parameter_manager.ini_dir, tool + ".ini")
            if ini_path.exists():
                command += ["-ini", str(ini_path)]

        # Run command(s)
        if len(commands) == 1:
            return self.run_command(commands[0])
        elif len(commands) > 1:
            return self.run_multiple_commands(commands)
        else:
            raise Exception("No commands to execute.")

    def stop(self) -> bool:
        """
        Terminates all processes initiated by this executor by killing them based on stored PIDs.
        """
        self.cancel_file.touch()
        return stop_processes(self.pid_dir, self.logger)

    def run_python(self, script_file: str, input_output: dict = {}) -> None:
        """
        Executes a specified Python script with dynamic input and output parameters,
        optionally logging the execution process. The method identifies and loads
        parameter defaults from the script, updates them with any user-specified
        parameters and file paths, and then executes the script via a subprocess
        call.

        This method facilitates the integration of standalone Python scripts into
        a larger application or workflow, allowing for the execution of these scripts
        with varying inputs and outputs without modifying the scripts themselves.

        Args:
            script_file (str):  The name or path of the Python script to be executed.
                                If the path is omitted, the method looks for the script in 'src/python-tools/'.
                                The '.py' extension is appended if not present.
            input_output (dict, optional): A dictionary specifying the input/output parameter names (as key) and their corresponding file paths (as value). Defaults to {}.
        """
        # Check if script file exists (can be specified without path and extension)
        # default location: src/python-tools/script_file
        if not script_file.endswith(".py"):
            script_file += ".py"
        path = Path(script_file)
        if not path.exists():
            path = Path("src", "python-tools", script_file)
            if not path.exists():
                raise FileNotFoundError(f"Script file not found: {script_file}")
                
        # load DEFAULTS
        if path.parent not in sys.path:
            sys.path.append(str(path.parent))
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        defaults = getattr(module, "DEFAULTS", None)
        if defaults is None:
            self.logger.log(f"WARNING: No DEFAULTS found in {path.name}")
            # run command without params
            self.run_command([sys.executable, str(path)])
        elif isinstance(defaults, list):
            defaults = {entry["key"]: entry["value"] for entry in defaults}
            # load paramters from JSON file
            params = {k: v for k, v in self.parameter_manager.get_parameters_from_json().items() if path.name in k}
            # update defaults
            for k, v in params.items():
                defaults[k.replace(f"{path.name}:", "")] = v
            for k, v in input_output.items():
                defaults[k] = v
            # save parameters to temporary JSON file
            tmp_params_file = Path(self.pid_dir.parent, f"{path.stem}.json")
            with open(tmp_params_file, "w", encoding="utf-8") as f:
                json.dump(defaults, f, indent=4)
            # run command
            try:
                self.run_command([sys.executable, str(path), str(tmp_params_file)])
            finally:
                tmp_params_file.unlink(missing_ok=True)
        else:
            raise ValueError(f"DEFAULTS in {path.name} must be a list")

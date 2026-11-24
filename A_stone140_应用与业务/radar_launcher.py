"""Launch the local acquisition/recognition bridge configured by the operator."""

from hmac import compare_digest
import os
from pathlib import Path
from secrets import token_urlsafe
from subprocess import DEVNULL, Popen, TimeoutExpired
import sys
from threading import Lock, Timer


class RadarLauncher:
    def __init__(self, monitor, config_path, load_session):
        self.monitor = monitor
        self.config_path = config_path.resolve()
        self.load_session = load_session
        self.lock = Lock()
        self.process = self.deadline = self.token = None
        self.status = "ready" if self.config_path.is_file() else "unconfigured"

    def _check_process(self):
        if self.process is not None and self.process.poll() is not None:
            self.deadline.cancel()
            self.process = self.token = None
            self.status = "exited"

    def _stop_process(self, status):
        self.status = status
        self.token = None
        if self.deadline is not None:
            self.deadline.cancel()
        if self.process is not None:
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
            except TimeoutExpired:
                self.status = "stop_failed"
                return
            except OSError as error:
                if self.process.poll() is None:
                    print(f"检测程序停止失败：{error}", file=sys.stderr, flush=True)
                    self.status = "stop_failed"
                    return
            self.process = None

    def _expire(self, process):
        with self.lock:
            if self.process is process and self.status == "starting":
                self._stop_process("no_frames")

    def start(self, port):
        with self.lock:
            self._check_process()
            if self.process is not None:
                return self.status
            if not self.config_path.is_file():
                self.status = "unconfigured"
                return self.status
            try:
                settings, monitor = self.load_session(self.config_path)
                command = settings["command"]
                if not isinstance(command, list) or not command or any(
                    not isinstance(arg, str) or not arg or "\0" in arg for arg in command
                ):
                    raise ValueError("command 须为非空字符串数组")
                program = (self.config_path.parent / command[0]).resolve(strict=True)
                if not program.is_file() or program.suffix.lower() not in (".py", ".exe"):
                    raise ValueError("command 须指向本机 Python 脚本或 EXE")
                argv = [str(program), *command[1:]]
                if program.suffix.lower() == ".py":
                    argv = [sys.executable, *argv]
                token = token_urlsafe(32)
                env = {
                    **os.environ,
                    "RADAR_A_FRAME_URL": f"http://127.0.0.1:{port}/api/frame",
                    "RADAR_A_FRAME_TOKEN": token,
                    "RADAR_A_META": str(self.config_path.parent / settings["meta"]),
                    "RADAR_A_ALGORITHM": str(self.config_path.parent / settings["algorithm"]),
                }
                process = Popen(argv, cwd=self.config_path.parent, env=env, stdin=DEVNULL, shell=False)
            except (OSError, ValueError, KeyError, TypeError) as error:
                print(f"雷达启动失败：{error}", file=sys.stderr, flush=True)
                self.status = "launch_failed"
                return self.status
            self.monitor.stop.set()
            self.monitor, self.process, self.token = monitor, process, token
            self.status = "starting"
            self.deadline = Timer(20, self._expire, args=(process,))
            self.deadline.daemon = True
            self.deadline.start()
            return self.status

    def stop(self):
        with self.lock:
            self._check_process()
            if self.process is not None:
                self._stop_process("stopped")
            return self.status

    def push(self, payload, authorization):
        with self.lock:
            self._check_process()
            if self.token is None or not compare_digest(authorization, f"Bearer {self.token}".encode()):
                raise PermissionError
            frame_id = self.monitor.push(payload)
            self.status = "running"
            self.deadline.cancel()
            return frame_id

    def snapshot(self):
        with self.lock:
            self._check_process()
            snapshot = self.monitor.snapshot()
            if snapshot["mode"] == "live" and self.token is None:
                snapshot.update(phase="waiting", available=False, state=None, evidence=None)
            return dict(snapshot, detector=self.status)

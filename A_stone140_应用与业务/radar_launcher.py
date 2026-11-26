"""Launch the local acquisition/recognition bridge configured by the operator."""

from hmac import compare_digest
from contextlib import suppress
import math
import os
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
        self.stop_file = None
        self.stop_timeout = 2.5
        self.status = "ready" if self.config_path.is_file() else "unconfigured"

    def _check_process(self):
        if self.status != "stopping" and self.process is not None and self.process.poll() is not None:
            self.deadline.cancel()
            self.process = self.token = None
            self._remove_stop_file()
            self.status = "exited"

    def _remove_stop_file(self):
        if self.stop_file is not None:
            with suppress(OSError):
                self.stop_file.unlink(missing_ok=True)
            self.stop_file = None

    def _stop_process(self, status, *, starting_process=None):
        with self.lock:
            self._check_process()
            if (starting_process is not None and
                    (self.process is not starting_process or self.status != "starting")):
                return self.status
            if self.process is None or self.status == "stopping":
                return self.status
            process, stop_file = self.process, self.stop_file
            self.status = "stopping"
            self.token = None
            self.deadline.cancel()
            if stop_file is not None:
                try:
                    stop_file.touch()
                except OSError as error:
                    print(f"无法通知采集端保存并退出：{error}", file=sys.stderr, flush=True)
                    stop_file = None
        try:
            if stop_file is not None:
                with suppress(TimeoutExpired):
                    process.wait(timeout=self.stop_timeout)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
        except (OSError, TimeoutExpired) as error:
            print(f"检测程序停止失败：{error}", file=sys.stderr, flush=True)
        with self.lock:
            if process.poll() is None:
                self.status = "stop_failed"
            else:
                self.process = None
                self._remove_stop_file()
                self.status = "exited" if process.returncode and stop_file is not None and status == "stopped" else status
            return self.status

    def _expire(self, process):
        self._stop_process("no_frames", starting_process=process)

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
                timeout_s = float(settings.get("first_frame_timeout_s", 20))
                if not math.isfinite(timeout_s) or timeout_s <= 0:
                    raise ValueError("first_frame_timeout_s 须为有限正数")
                stop_timeout = float(settings.get("graceful_stop_timeout_s", 2.5))
                if not math.isfinite(stop_timeout) or not 0 < stop_timeout <= 15:
                    raise ValueError("graceful_stop_timeout_s 须在 0 至 15 秒内")
                token = token_urlsafe(32)
                env = {
                    **os.environ,
                    "RADAR_A_FRAME_URL": f"http://127.0.0.1:{port}/api/frame",
                    "RADAR_A_FRAME_TOKEN": token,
                    "RADAR_A_META": str(self.config_path.parent / settings["meta"]),
                    "RADAR_A_ALGORITHM": str(self.config_path.parent / settings["algorithm"]),
                }
                stop_file = None
                if settings.get("managed_acquisition") is True:
                    rid = monitor.meta["recording_id"]
                    stop_file = self.config_path.parent / f".radar-stop-{rid}"
                    env.update(RADAR_A_RECORDING_ID=rid, RADAR_A_STOP_FILE=str(stop_file))
                process = Popen(argv, cwd=self.config_path.parent, env=env, stdin=DEVNULL, shell=False)
            except (OSError, ValueError, KeyError, TypeError) as error:
                print(f"雷达启动失败：{error}", file=sys.stderr, flush=True)
                self.status = "launch_failed"
                return self.status
            self.monitor.stop.set()
            self.monitor, self.process, self.token = monitor, process, token
            self.stop_file = stop_file
            self.stop_timeout = stop_timeout
            self.status = "starting"
            self.deadline = Timer(timeout_s, self._expire, args=(process,))
            self.deadline.daemon = True
            self.deadline.start()
            return self.status

    def stop(self):
        return self._stop_process("stopped")

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

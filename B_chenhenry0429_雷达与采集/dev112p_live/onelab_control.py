"""Invoke the configured recording controls in the local ONELAB window."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep


def control(action, config_path):
    if sys.platform != "win32":
        raise RuntimeError("ONELAB control requires Windows")
    try:
        from pywinauto import Desktop
        from pywinauto.application import process_from_module, ProcessNotFoundError
        from pywinauto.handleprops import processid
    except ImportError as error:
        raise RuntimeError("Install the Windows ONELAB dependency: python -m pip install pywinauto==0.6.9") from error

    settings = json.loads(config_path.read_text(encoding="utf-8-sig"))
    executable = Path(settings["executable"]).resolve(strict=True)
    if not executable.is_file() or executable.suffix.lower() != ".exe":
        raise ValueError("Select the installed ONELAB executable")
    title = settings["window_title"]
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Set the exact ONELAB window title")
    selector = {}
    if action != "inspect":
        selector = settings[action]
        if not isinstance(selector, dict) or not selector.keys() <= {"automation_id", "name"} or not selector:
            raise ValueError(f"Set the exact ONELAB {action} control selector")
        if any(not isinstance(value, str) or not value.strip() for value in selector.values()):
            raise ValueError("Control selectors must be nonempty strings")

    try:
        pid = process_from_module(str(executable))
    except ProcessNotFoundError:
        if action != "start":
            raise RuntimeError("The configured ONELAB application is not running")
        pid = subprocess.Popen([str(executable)], cwd=executable.parent).pid

    desktop = Desktop(backend="uia")
    deadline = monotonic() + 6
    while monotonic() < deadline:
        windows = [window for window in desktop.windows(title=title, process=pid)
                   if window.handle and processid(window.handle) == pid]
        if len(windows) > 1:
            raise RuntimeError("More than one ONELAB window matched")
        if windows:
            buttons = windows[0].descendants(control_type="Button")
            if action == "inspect":
                for button in buttons:
                    info = button.element_info
                    print(f"{info.name!r}  automation_id={info.automation_id!r}  enabled={button.is_enabled()}")
                return
            matches = [button for button in buttons if all(
                getattr(button.element_info, key) == value for key, value in selector.items()
            )]
            if len(matches) > 1:
                raise RuntimeError(f"More than one ONELAB {action} button matched")
            if matches and matches[0].is_enabled():
                matches[0].invoke()
                return
        sleep(.15)
    raise RuntimeError(f"ONELAB {action} control unavailable. Check the window title, recording setup and device connection.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "inspect"))
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    try:
        control(args.action, args.config)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

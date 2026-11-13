"""Run external programs for `execute_code` tools: fixed directory, clean environment, hard kill.

A thread timeout cannot stop a program (decision 0005), so tools never call `subprocess`
themselves. `run_command` starts the program in its own process group, kills the whole group
when the timeout expires, and passes only an allowlist of environment variables, so API keys and
other secrets in the operator's environment never reach generated or third-party code.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO

from pydantic import BaseModel

# What build tools need to find programs, caches, temp space, and certificates. Package-index
# URLs are excluded because they can embed credentials.
ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "UV_CACHE_DIR",
        "UV_PYTHON_INSTALL_DIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
)
OUTPUT_TAIL_CHARS = 4000
UTF8_MAX_BYTES = 4  # the most bytes one character takes


class CommandResult(BaseModel):
    """What one program run did. ``exit_code`` is None when it could not start or was killed."""

    command: list[str]
    cwd: str
    exit_code: int | None
    timed_out: bool = False
    duration_ms: float
    output_bytes: int
    stdout_tail: str = ""
    stderr_tail: str = ""

    @property
    def ok(self) -> bool:
        """Return whether the program ran to completion with exit code 0."""
        return self.exit_code == 0 and not self.timed_out


def scrubbed_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the allowlisted variables of this process's environment, plus ``extra``."""
    environment = {
        name: value for name, value in os.environ.items() if name.upper() in ENV_ALLOWLIST
    }
    environment.update(extra or {})
    return environment


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    extra_env: Mapping[str, str] | None = None,
    tail_chars: int = OUTPUT_TAIL_CHARS,
) -> CommandResult:
    """Run ``command`` in ``cwd`` and return its result; failures are results, never raised.

    The program gets `scrubbed_environment` and no stdin. When ``timeout`` (seconds) expires,
    its whole process tree is killed, and the call returns even if a descendant that left the
    tree lives on. Only the last ``tail_chars`` characters of each stream are kept (raise it
    for programs whose whole output is needed, such as JSON results); the rest stays on disk
    until the call returns, never in memory.
    """
    started = time.perf_counter()
    arguments = [str(part) for part in command]

    def result(
        exit_code: int | None, output_bytes: int, *, stdout: str = "", stderr: str = ""
    ) -> CommandResult:
        return CommandResult(
            command=arguments,
            cwd=str(cwd),
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            output_bytes=output_bytes,
            stdout_tail=stdout,
            stderr_tail=stderr,
        )

    timed_out = False
    if sys.platform == "win32":
        flags, new_session = subprocess.CREATE_NEW_PROCESS_GROUP, False
    else:
        flags, new_session = 0, True
    # Output goes to files, not pipes: a pipe would hold all of it in memory, and a descendant
    # that left the process group could keep one open long after the timeout.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(
                arguments,
                cwd=cwd,
                env=scrubbed_environment(extra_env),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                creationflags=flags,
                start_new_session=new_session,
            )
        except OSError as error:
            return result(None, 0, stderr=f"Cannot start: {error}")
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(process)
            process.wait()
        return result(
            None if timed_out else process.returncode,
            out.seek(0, os.SEEK_END) + err.seek(0, os.SEEK_END),
            stdout=_tail(out, tail_chars),
            stderr=_tail(err, tail_chars),
        )


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill ``process`` and everything it started."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
        process.kill()
        return
    with contextlib.suppress(ProcessLookupError):  # already gone
        os.killpg(process.pid, signal.SIGKILL)


def _tail(output: IO[bytes], chars: int) -> str:
    """Return the last ``chars`` characters of ``output``, reading no more than they can need."""
    size = output.seek(0, os.SEEK_END)
    # One character more, so a read that starts inside a character still holds ``chars`` whole.
    output.seek(max(0, size - (chars + 1) * UTF8_MAX_BYTES))
    return output.read().decode("utf-8", errors="replace")[-chars:] if chars > 0 else ""

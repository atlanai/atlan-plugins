"""Keep native connector sign-in visible without mixing prompts into JSON results."""
import codecs
import errno
import os
import selectors
import signal
import subprocess
import sys
import time


SIGN_IN_TIMEOUT = 900


def at_terminal():
    return sys.stdin.isatty() and sys.stderr.isatty()


class Transcript:
    """Capture setup diagnostics while retaining the real terminal's capabilities."""

    def __init__(self, capture, terminal):
        self.capture, self.terminal = capture, terminal

    def write(self, value):
        self.capture.write(value)
        self.terminal.write(value)
        self.terminal.flush()
        return len(value)

    def flush(self):
        self.terminal.flush()

    def isatty(self):
        return self.terminal.isatty()

    def fileno(self):
        return self.terminal.fileno()


def run(command, *, env, timeout=SIGN_IN_TIMEOUT):
    """Capture engine output; on a real terminal also relay it while the engine runs.

    Separate PTYs preserve native terminal checks and line buffering on both streams:
    callers still receive separate output and the worker reserves its own stdout for JSON.
    No terminal is fabricated for an unattended caller. Input is inherited, never captured.
    """
    if not at_terminal():
        return subprocess.run(command, env=env, capture_output=True, text=True)
    if os.name == 'nt':
        return _run_piped(command, env=env, timeout=timeout)
    import pty
    import termios

    terminals = {}
    output = {'stdout': [], 'stderr': []}
    decoders = {name: codecs.getincrementaldecoder('utf-8')(errors='replace') for name in output}
    proc = None
    try:
        for name in output:
            master, slave = pty.openpty()
            terminals[name] = (master, slave)
            attributes = termios.tcgetattr(slave)
            attributes[1] &= ~termios.OPOST
            termios.tcsetattr(slave, termios.TCSANOW, attributes)
        with selectors.DefaultSelector() as selector:
            proc = subprocess.Popen(command, env=env, stdout=terminals['stdout'][1],
                                    stderr=terminals['stderr'][1],
                                    start_new_session=True)
            for name, (master, slave) in list(terminals.items()):
                os.close(slave)
                terminals[name] = (master, None)
                selector.register(master, selectors.EVENT_READ, name)
            deadline = time.monotonic() + timeout
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Interactive connector operation timed out; retry sign-in.')
                for key, _ in selector.select(min(remaining, 0.2)):
                    try:
                        chunk = os.read(key.fd, 65536)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        chunk = b''  # A closed PTY reports EIO on Linux.
                    value = decoders[key.data].decode(chunk, final=not chunk)
                    output[key.data].append(value)
                    sys.stderr.write(value)
                    sys.stderr.flush()
                    if not chunk:
                        selector.unregister(key.fileobj)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Interactive connector operation timed out; retry sign-in.')
            try:
                code = proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise TimeoutError('Interactive connector operation timed out; retry sign-in.') from None
            return subprocess.CompletedProcess(command, code, ''.join(output['stdout']),
                                               ''.join(output['stderr']))
    finally:
        if proc is not None:
            # Include connector subprocesses on cancellation or timeout, even when the
            # direct child has exited but a descendant still holds a stream open.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        for master, slave in terminals.values():
            os.close(master)
            if slave is not None:
                os.close(slave)


def _run_piped(command, *, env, timeout):
    """Windows has no PTYs: relay each piped stream from a reader thread while capturing it.

    The engine sees pipes rather than a terminal, so sign-in prompts still reach the person
    through stderr. Input is inherited, never captured.
    """
    import threading

    output = {'stdout': [], 'stderr': []}
    relay = threading.Lock()
    proc = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

    def pump(name, stream):
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        while True:
            chunk = os.read(stream.fileno(), 65536)
            value = decoder.decode(chunk, final=not chunk)
            if value:
                output[name].append(value)
                with relay:
                    sys.stderr.write(value)
                    sys.stderr.flush()
            if not chunk:
                return

    readers = [threading.Thread(target=pump, args=(name, getattr(proc, name)), daemon=True)
               for name in output]
    try:
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        try:
            code = proc.wait(timeout=max(deadline - time.monotonic(), 0))
        except subprocess.TimeoutExpired:
            raise TimeoutError('Interactive connector operation timed out; retry sign-in.') from None
        for reader in readers:
            reader.join(max(deadline - time.monotonic(), 0))
            if reader.is_alive():
                raise TimeoutError('Interactive connector operation timed out; retry sign-in.')
        return subprocess.CompletedProcess(command, code, ''.join(output['stdout']),
                                           ''.join(output['stderr']))
    finally:
        # Include connector subprocesses on cancellation or timeout, as the PTY path does
        # with its process group. taskkill is part of every supported Windows install.
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait()
        for reader in readers:
            reader.join(1)
        proc.stdout.close()
        proc.stderr.close()

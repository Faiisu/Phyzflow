"""Clean process groups on agent death without signaling a saved/reused PID.

Only the agent owns the lifeline writer. The supervisor keeps the state lock
until cleanup; a live group leader remains until the supervisor kills the group.
"""
import json
import os
import select
import signal
import subprocess
import sys
import time


def exit_with(code):
    if code < 0:
        signal.signal(-code, signal.SIG_DFL)
        os.kill(os.getpid(), -code)
    return code


def worker():
    signal.signal(signal.SIGTERM, lambda *_: None)
    signal.signal(signal.SIGINT, lambda *_: None)
    result, inherited = (int(value) for value in sys.argv[2:4])
    process = subprocess.Popen(sys.argv[4:], pass_fds=() if inherited < 0 else (inherited,))
    if inherited >= 0:
        os.close(inherited)
    code = process.wait()
    os.write(result, json.dumps(code).encode() + b'\n')
    os.close(result)
    # Stay as the group leader so its identity cannot be reused during cleanup.
    while True:
        signal.pause()


def main():
    control, owner, inherited = (int(value) for value in sys.argv[1:4])
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    reader, writer = os.pipe()
    process = subprocess.Popen([sys.executable, '-I', __file__, '--worker', str(writer), str(inherited), *sys.argv[4:]],
                               process_group=0, pass_fds=(writer,) + (() if inherited < 0 else (inherited,)))
    os.close(writer)
    if inherited >= 0:
        os.close(inherited)
    code = -signal.SIGTERM
    try:
        while not stopping:
            ready = select.select([control, reader], [], [], .02)[0]
            if control in ready and not os.read(control, 1):
                stopping = True
            if reader in ready:
                data = os.read(reader, 128)
                code = int(data) if data else 127
                break
        os.killpg(process.pid, signal.SIGTERM)
        time.sleep(1 if stopping else .02)
        os.killpg(process.pid, signal.SIGKILL)
    finally:
        # This Popen is never polled/reaped before the final group signal.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        os.close(control)
        os.close(reader)
        if owner >= 0:
            os.close(owner)
    return exit_with(code)


if __name__ == '__main__':
    if sys.argv[1] == '--worker':
        worker()
    else:
        sys.exit(main())

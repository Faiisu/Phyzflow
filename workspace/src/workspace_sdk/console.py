"""Capture a sheet process's stdout and stderr for the active run."""

from codecs import getincrementaldecoder
from contextlib import ExitStack, contextmanager, suppress
import os
import sys
from threading import Event, Thread
from time import monotonic


def flush_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            with suppress(OSError, ValueError):
                stream.flush()


@contextmanager
def capture_console(emit):
    done = Event()
    drains = []
    flush_console()
    with ExitStack() as files, ExitStack() as redirects:
        for name, descriptor in (("stdout", 1), ("stderr", 2)):
            original = os.dup(descriptor)
            files.callback(os.close, original)
            reader, writer = os.pipe()
            files.callback(os.close, reader)
            try:
                os.set_blocking(reader, False)
                os.dup2(writer, descriptor)
                redirects.callback(os.dup2, original, descriptor)
            finally:
                os.close(writer)
            drains.append((name, reader, getincrementaldecoder("utf-8")(errors="replace")))

        def read_output():
            deadline = None
            while True:
                pending = False
                for name, descriptor, decoder in drains:
                    try:
                        data = os.read(descriptor, 4096)
                    except BlockingIOError:
                        continue
                    if data:
                        pending = True
                        text = decoder.decode(data)
                        if text:
                            emit(name, text)
                if done.is_set():
                    if deadline is None:
                        deadline = monotonic() + .25
                    if not pending or monotonic() >= deadline:
                        break
                elif not pending:
                    done.wait(.02)
            for name, _, decoder in drains:
                if text := decoder.decode(b"", final=True):
                    emit(name, text)

        reader_thread = Thread(target=read_output, name="sheet-console")
        reader_thread.start()
        try:
            yield
        finally:
            flush_console()
            redirects.close()
            done.set()
            reader_thread.join()

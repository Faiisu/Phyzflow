from collections.abc import Iterator
from itertools import count
from time import sleep


def count_up() -> Iterator[int]:
    """Generate the integers from one, with half a second between values."""
    for value in count(1):
        yield value
        sleep(0.5)

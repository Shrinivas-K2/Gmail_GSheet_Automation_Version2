"""
persistence/safe.py

FailSafe = a wrapper (Proxy / Decorator pattern) around any object.
If a method of the wrapped object raises, the error is reported and
swallowed, and the program carries on.

Used for the send tracker: a database problem must NEVER stop e-mails
from being sent.
"""

from app.domain.interfaces import Reporter


class FailSafe:

    def __init__(self, target, reporter: Reporter, label: str = "DB"):
        self._target = target
        self._reporter = reporter
        self._label = label

    def __getattr__(self, name):
        attribute = getattr(self._target, name)

        if not callable(attribute):
            return attribute

        def guarded(*args, **kwargs):
            try:
                return attribute(*args, **kwargs)
            except Exception as e:
                self._reporter.warn(f"[{self._label}] Warning ({name}): {e}")
                return None

        return guarded

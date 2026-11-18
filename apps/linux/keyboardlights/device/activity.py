"""A session-wide write interlock shared by lighting and assignments."""

import threading


class WriteGuard:
    def __init__(self):
        self._evidence = None
        self._blocked = False
        self._mutex = threading.Lock()

    @property
    def evidence(self):
        with self._mutex:
            return self._evidence

    @property
    def requires_inspection(self):
        with self._mutex:
            return self._blocked

    def block(self, evidence):
        with self._mutex:
            if not self._blocked:
                self._evidence = str(evidence) if evidence is not None else None
                self._blocked = True

    def check(self):
        with self._mutex:
            if not self._blocked:
                return

            evidence = self._evidence

        records = f" Records: {evidence}" if evidence is not None else ""
        raise ValueError("A previous keyboard write needs inspection. Further writes are blocked." + records)

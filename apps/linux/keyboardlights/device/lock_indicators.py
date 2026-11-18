"""The keyboard's host-supplied lock indicator snapshot, including reserved bits."""

from dataclasses import dataclass, field
from time import monotonic


@dataclass(frozen=True)
class LockIndicators:
    raw_value: int
    sampled_at: float = field(default_factory=monotonic, compare=False, repr=False)

    def __post_init__(self):
        if type(self.raw_value) is not int or not 0 <= self.raw_value <= 255:
            raise ValueError("Lock indicators must be one byte")

    @staticmethod
    def latest(first, second):
        if first is None:
            return second

        if second is None:
            return first

        return second if first.sampled_at <= second.sampled_at else first

    @property
    def num_lock(self):
        return bool(self.raw_value & 0x01)

    @property
    def caps_lock(self):
        return bool(self.raw_value & 0x02)

    @property
    def scroll_lock(self):
        return bool(self.raw_value & 0x04)

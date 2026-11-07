"""Structural interface for host memory adapters."""
from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from ..schemas import Record


@runtime_checkable
class MemoryAdapter(Protocol):
    """Read canonical state and atomically replace the scoped state.

    Adapters are responsible for translating native records to/from canonical
    ``Record`` values. ``replace_state`` must be atomic for the supplied scope;
    hosts that cannot provide this guarantee must stage writes or decline use
    as a guarded adapter.
    """

    backend_name: str

    def snapshot(self, scope: Mapping[str, str]) -> Sequence[Record]:
        """Return the current relevant state in canonical form."""
        ...

    def replace_state(self, scope: Mapping[str, str], records: Sequence[Record]) -> None:
        """Atomically replace the relevant state after an accepted transition."""
        ...

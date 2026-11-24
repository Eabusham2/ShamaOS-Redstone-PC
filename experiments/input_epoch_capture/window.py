"""Reference model for bounded, chronologically sealed input transactions."""
from dataclasses import dataclass, field


def universal(record):
    return 0x16 <= (record & 255) <= 0x19


@dataclass(frozen=True)
class Packet:
    records: tuple[int, ...]


@dataclass
class Window:
    depth: int
    ack: int | None = None
    prefix: list[Packet] = field(default_factory=list)
    urgent: list[int] = field(default_factory=list)
    lost: bool = False
    last_levels: int = 0
    shift: bool = False

    def receive(self, packet):
        if not packet.records:
            return
        if len(self.prefix) < self.depth:
            self.prefix.append(packet)
            return
        # Even a successful universal replacement in a full FIFO reports
        # overflow. Every epoch beyond the prefix offers at least one record.
        self.lost = True
        first = next((record for record in packet.records if universal(record)), None)
        if first is not None and len(self.urgent) < self.depth:
            self.urgent.append(first)


@dataclass
class Visible:
    depth: int
    records: list[int] = field(default_factory=list)
    overflow: bool = False

    def acknowledge(self, payload):
        if payload is not None and payload in self.records:
            self.records.remove(payload)

    def receive(self, records):
        replaced = False
        for record in records:
            if len(self.records) < self.depth:
                self.records.append(record)
                continue
            self.overflow = True
            if universal(record) and not replaced:
                for i in range(len(self.records) - 1, -1, -1):
                    if not universal(self.records[i]):
                        del self.records[i]
                        self.records.append(record)
                        replaced = True
                        break

    def observable(self, levels, shift):
        selected = next((r for r in self.records if universal(r)),
                        self.records[0] if self.records else levels << 16)
        return (int(shift) << 36 | len(self.records) << 28 | int(self.overflow) << 27 |
                selected << 1 | int(bool(self.records)))

    def replay(self, window):
        # Externally publish a new head/count/snapshot only once this window
        # commits. An ACK at its start refers to the earlier observed payload.
        self.acknowledge(window.ack)
        for packet in window.prefix:
            self.receive(packet.records)
        for record in window.urgent:
            self.receive((record,))
        self.overflow |= window.lost

@dataclass
class SerialService:
    """Two hardware banks. A fence is accepted only when one bank is free."""
    depth: int
    current: Visible = field(init=False)
    live: Window = field(init=False)
    retiring: Window | None = None
    work: Visible | None = None
    position: int = 0
    published: int = 0

    def __post_init__(self):
        self.current = Visible(self.depth)
        self.live = Window(self.depth)

    @property
    def ready(self):
        return self.retiring is None

    def seal(self, levels, ack=None):
        if not self.ready:
            raise BufferError("previous input fence has not committed")
        # A pre-edge observation uses current pins for an empty FIFO's
        # combinational controller fallback, even if that edge admits a press.
        self.live.last_levels = levels
        self.retiring = self.live
        self.work = Visible(self.depth, self.current.records.copy(), self.current.overflow)
        self.live = Window(self.depth, ack, last_levels=self.retiring.last_levels, shift=self.retiring.shift)
        self.position = -1

    def receive(self, packet, levels, shift):
        self.live.receive(packet)
        self.live.last_levels = levels
        self.live.shift = shift

    def worker_step(self):
        window = self.retiring
        if window is None:
            return None
        if self.position == -1:
            self.work.acknowledge(window.ack)
        elif self.position < len(window.prefix):
            self.work.receive(window.prefix[self.position].records)
        elif self.position < len(window.prefix) + len(window.urgent):
            self.work.receive((window.urgent[self.position - len(window.prefix)],))
        else:
            self.work.overflow |= window.lost
            self.current = self.work
            self.published = self.current.observable(window.last_levels, window.shift)
            self.retiring = self.work = None
            return self.published
        self.position += 1
        return None

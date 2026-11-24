"""Independent chronological model of simultaneous admission, ACK and eviction."""
from pathlib import Path
import random
import shutil
import subprocess

import pytest

from test_console_input_integration import expected_keyboard_banks

ROOT = Path(__file__).resolve().parents[1]
PRIORITY = (8, 9, 7, 6, 0, 1, 2, 3, 4, 5)
KEYS = expected_keyboard_banks()


def universal(record):
    return 0x16 <= (record & 255) <= 0x19


class Queue:
    def __init__(self, depth, stream):
        self.depth = depth
        self.stream = stream
        self.records = []
        self.shift = self.overflow = self.prev_ack = False
        self.prev_controller = self.prev_rows = self.prev_cols = 0
        self.edges = 0

    def edge(self, *, reset=False, rows=0, cols=0, controller=0, ack=False, payload=0):
        if reset:
            self.records.clear()
            self.shift = self.overflow = self.prev_ack = False
            self.prev_controller = self.prev_rows = self.prev_cols = 0
        else:
            if ack and not self.prev_ack and payload in self.records:
                self.records.remove(payload)  # Oldest identical payload only.
            arrivals = [(controller << 16) | (0x10 + i) for i in PRIORITY
                        if (controller & ~self.prev_controller) & (1 << i)]
            if rows.bit_count() == cols.bit_count() == 1 and (rows, cols) != (self.prev_rows, self.prev_cols):
                key = (rows.bit_length() - 1) * 8 + cols.bit_length() - 1
                if key == 63:
                    self.shift = not self.shift
                else:
                    arrivals.append((controller << 16) | (KEYS[64 * self.shift + key] << 8) | 1)
            evicted = False
            for record in arrivals:
                if len(self.records) < self.depth:
                    self.records.append(record)
                else:
                    self.overflow = True
                    if universal(record) and not evicted:
                        for victim in reversed(range(len(self.records))):
                            if not universal(self.records[victim]):
                                self.records.pop(victim)
                                self.records.append(record)
                                evicted = True
                                break
            self.prev_rows, self.prev_cols = rows, cols
            self.prev_ack, self.prev_controller = ack, controller

        head = next((record for record in self.records if universal(record)),
                    self.records[0] if self.records else controller << 16)
        observed = (self.shift << 36 | len(self.records) << 28 | self.overflow << 27 |
                    head << 1 | bool(self.records))
        for slot, record in enumerate(self.records):
            observed |= record << (37 + 26 * slot)
        pins = (int(reset) << 53 | rows << 45 | cols << 37 | controller << 27 | int(ack) << 26 | payload)
        self.stream.write(f"{pins:x} {observed:x}\n")
        self.edges += 1

    def type_key(self, column):
        self.edge(rows=1, cols=1 << (column % 8))
        self.edge()

    def press(self, control):
        self.edge(controller=1 << control)
        self.edge()


def write_cases(path, depth):
    rand = random.Random(0x4817 + depth)
    with path.open("w") as out:
        queue = Queue(depth, out)
        # Empty tail is not an eviction candidate. Files must never displace
        # the higher-priority Editor arriving with it, even at depth one.
        queue.edge(reset=True)
        for i in range(depth - 1):
            queue.type_key(i)
        queue.edge(controller=0x300)
        assert [r & 255 for r in queue.records if universal(r)] == ([0x18] if depth == 1 else [0x18, 0x19])
        assert queue.overflow

        # Occupied ordinary/universal records in different positions, with
        # an optional exact ACK before each possible subset of urgent edges.
        for pattern in ("ordinary", "alternating", "universal"):
            for mask in range(16):
                for ack_position in (None, 0, depth // 2, depth - 1):
                    queue.edge(reset=True)
                    for i in range(depth):
                        if pattern == "universal" or (pattern == "alternating" and i % 2):
                            queue.press(6 + i % 4)
                        else:
                            queue.type_key(i)
                    assert len(queue.records) == depth and not queue.overflow
                    payload = queue.records[ack_position] if ack_position is not None else 0
                    queue.edge(controller=(mask << 6) | 0x30, rows=1, cols=2,
                               ack=ack_position is not None, payload=payload)
                    queue.edge(ack=True, payload=payload)
                    queue.edge(ack=True, payload=0x3ffffef)  # Changed data on held ACK.
                    queue.edge()

        # Reuse every physical slot repeatedly through fill, eviction, drain
        # and refill. Capacity-sized occupancies must not wrap the count.
        queue.edge(reset=True)
        for round_number in range(4):
            for i in range(depth):
                queue.type_key(i + round_number)
            queue.edge(controller=0x3ff, rows=1, cols=1)
            assert len(queue.records) == depth
            while queue.records:
                payload = queue.records[-1]
                queue.edge(ack=True, payload=payload)
                queue.edge()
        # Exercise overlapping edges, stale payloads, Shift, invalid chords,
        # acknowledgement reprioritization and reset on occupied states.
        history = []
        for _ in range(4000):
            if queue.records:
                history.append(rand.choice(queue.records))
                history = history[-16:]
            payload = rand.choice([*history, 0x3ffffef])
            rows, cols = rand.choice([(0, 0), (3, 3), (128, 128),
                                      (1 << rand.randrange(8), 1 << rand.randrange(8))])
            queue.edge(reset=rand.randrange(127) == 0, rows=rows, cols=cols,
                       controller=rand.randrange(1024), ack=bool(rand.randrange(2)), payload=payload)
    return queue.edges


@pytest.mark.parametrize("depth", (1, 2, 3, 4, 16))
def test_full_fifo_keeps_admitted_universals_and_compacts_surviving_records(tmp_path, depth):
    if not shutil.which("iverilog") or not shutil.which("vvp"):
        pytest.skip("Icarus Verilog required for physical input FIFO RTL transactions")
    vectors, binary = tmp_path / "vectors.hex", tmp_path / "fifo"
    edges = write_cases(vectors, depth)
    subprocess.run(["iverilog", "-g2012", "-s", "input_eviction_tb", f"-Pinput_eviction_tb.DEPTH={depth}",
                    "-o", str(binary), str(ROOT / "rtl/shama_input.sv"), str(ROOT / "tests/rtl/input_eviction_tb.sv")],
                   check=True, capture_output=True, text=True, timeout=30)
    result = subprocess.run(["vvp", str(binary), f"+VECTORS={vectors}"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"FIFO TRANSACTIONS PASS depth={depth} edges={edges}" in result.stdout

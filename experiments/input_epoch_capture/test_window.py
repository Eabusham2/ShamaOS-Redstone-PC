"""Compressed raw epochs compared against the production transaction oracle."""
from collections import deque
from io import StringIO
import random

from experiments.input_epoch_capture.window import Packet, SerialService, Visible, Window
from test_input_eviction import KEYS, PRIORITY, Queue


class Capture:
    def __init__(self):
        self.prev = (0, 0, 0, False)
        self.shift = False

    def sample(self, rows, cols, ctl, ack):
        pr, pc, old, pa = self.prev
        self.prev = (rows, cols, ctl, ack)
        records = [ctl << 16 | (16 + i) for i in PRIORITY if ctl & ~old & (1 << i)]
        if rows.bit_count() == cols.bit_count() == 1 and (rows, cols) != (pr, pc):
            index = (rows.bit_length() - 1) * 8 + cols.bit_length() - 1
            if index == 63:
                self.shift = not self.shift
            else:
                records.append(ctl << 16 | KEYS[64 * self.shift + index] << 8 | 1)
        return Packet(tuple(records)), ack and not pa


def test_delayed_replay_preserves_original_records_through_ack_and_observation_fences():
    for depth in (1, 2, 3, 4, 16):
        rng = random.Random(8742 + depth)
        oracle = Queue(depth, StringIO())
        decoder = Capture()
        live = Window(depth)
        replay = Visible(depth)
        sealed = deque()

        def boundary():
            nonlocal live
            # Capture a complete state including empty-FIFO live controller
            # and the independent Shift state when a consumer asks to observe.
            head = next((record for record in oracle.records if 0x16 <= record & 255 <= 0x19),
                        oracle.records[0] if oracle.records else oracle.prev_controller << 16)
            observed = (oracle.shift << 36 | len(oracle.records) << 28 | oracle.overflow << 27 |
                        head << 1 | bool(oracle.records))
            sealed.append((live, oracle.records.copy(), observed))
            live = Window(depth, last_levels=oracle.prev_controller, shift=decoder.shift)

        def service_one():
            window, records, observed = sealed.popleft()
            assert len(window.prefix) <= depth and len(window.urgent) <= depth
            replay.replay(window)
            assert replay.records == records
            assert replay.observable(window.last_levels, window.shift) == observed

        for epoch in range(21800):
            index = rng.randrange(64)
            rows, cols = rng.choice([(0, 0), (1, 1), (3, 3), (128, 128),
                                     (1 << (index // 8), 1 << (index % 8))])
            # Long pauses with repeated ordinary/urgent presses and zero ACKs
            # exercise saturation even while the previous prefix is outstanding.
            ctl = rng.choice((0, 0, 0x3ff, 1 << rng.randrange(10), rng.randrange(1024)))
            ack = ((epoch // 233) % 3 != 0 and epoch % 37 in (0, 1, 2)) or epoch % 801 == 0
            payload = rng.choice([0, 0x3ffffef, *oracle.records])
            rise = ack and not decoder.prev[3]
            if rise or epoch % 991 == 0:
                # ACK applies before arrivals from its own sample. Freeze the
                # previous prefix even when its old selected head was replaced.
                boundary()
                live.ack = payload if rise else None
            packet, decoded_rise = decoder.sample(rows, cols, ctl, ack)
            assert rise == decoded_rise
            oracle.edge(rows=rows, cols=cols, controller=ctl, ack=ack, payload=payload)
            live.receive(packet)
            live.last_levels = ctl
            live.shift = decoder.shift
            if len(sealed) > 1:
                service_one()
        boundary()
        while sealed:
            service_one()


def test_long_pause_extremal_witness_uses_both_banks_of_one_window():
    for depth in (1, 2, 16):
        win = Window(depth)
        # Fill using separately released copies of the same key; all snapshots
        # have distinct admission epochs even when the payloads are identical.
        for _ in range(depth): win.receive(Packet((0x4101,)))
        for i in range(depth):
            full_snapshot = (0x300 | (i & 7)) << 16
            win.receive(Packet((full_snapshot | 0x18, full_snapshot | 0x19, 0x4201)))
            for _ in range(11): win.receive(Packet((0x4301,)))
        for _ in range(80): win.receive(Packet((0x170017,)))
        stable = Visible(depth)
        stable.replay(win)
        assert stable.records == [((0x300 | (i & 7)) << 16) | 0x18 for i in range(depth)]
        assert stable.overflow and len(win.prefix) == len(win.urgent) == depth


def test_fences_wait_for_actual_credit_while_raw_inputs_continue_at_full_rate():
    # Deliberately make the worker much slower than raw input and let the
    # consumer offer fences faster than work can retire. Keep the valid request
    # and its exact ACK payload stable until the same physical handshake fires.
    import pytest
    rng = random.Random(90)
    depth = 16
    service = SerialService(depth)
    oracle = Queue(depth, StringIO())
    capture = Capture()
    held = None
    expected = None
    delayed = transfers = seals = 0
    for raw in range(26000):
        ctl = rng.choice((0, 0x100, 0x300, 0x0f, rng.randrange(1024)))
        r, c = rng.choice(((1, 1), (0, 0), (128, 128), (1, 4), (3, 3)))
        if held is None and raw % 11 == 0:
            held = (rng.choice([None, None, 0x1ffffff, *oracle.records]),)
        accepted = held is not None and service.ready and not capture.prev[3]
        ack = accepted and held[0] is not None
        data = held[0] if ack else 0
        if accepted:
            # Use the independent oracle's last public value directly. The
            # raw consumer's previous selected record may have been displaced.
            old = oracle.stream.getvalue().splitlines()[-1:] or ["0 0"]
            status = int(old[0].split()[1], 16) & ((1 << 37) - 1)
            if not oracle.records:
                status = (status & ~(1023 << 17)) | (ctl << 17)
            expected = status, oracle.records.copy()
            service.seal(ctl, held[0])
            held = None
            seals += 1
        elif held is not None and not service.ready:
            delayed += 1
            with pytest.raises(BufferError):
                service.seal(ctl, held[0])
        packet, _ = capture.sample(r, c, ctl, ack)
        oracle.edge(rows=r, cols=c, controller=ctl, ack=ack, payload=data)
        service.receive(packet, ctl, capture.shift)
        previous = service.published
        # Pause the worker entirely during this interval; physical events keep arriving.
        if raw % 5 == 0 and not 5000 <= raw < 6500:
            published = service.worker_step()
            if published is not None:
                assert (published, service.current.records) == expected
                transfers += 1
            else:
                assert service.published == previous
    assert delayed > 20000 and transfers > 100 and seals - transfers <= 1


def test_empty_controller_snapshot_samples_current_pins_but_admits_press_after_fence():
    mask = 0x10
    for has_key in (False, True):
        service = SerialService(16)
        if has_key:
            service.receive(Packet((0x4101,)), 0, False)
        service.seal(mask)
        service.receive(Packet((mask << 16 | 0x14,)), mask, False)
        while (status := service.worker_step()) is None:
            pass
        assert status == ((1 << 28 | 0x4101 << 1 | 1) if has_key else mask << 17)


def test_accepted_ack_stays_with_concurrent_arrivals_across_fence():
    # If an already-issued ACK pulse is delayed, the new B is lost at capacity.
    a, b = 0x4101, 0x4201
    direct = Visible(16, [a]*16)
    direct.acknowledge(a)
    direct.receive((b,))
    wrong = Visible(16, [a]*16)
    wrong.receive((b,))
    wrong.acknowledge(a)
    assert (direct.records, direct.overflow) == ([a]*15 + [b], False)
    assert (wrong.records, wrong.overflow) == ([a]*15, True)
    svc = SerialService(16)
    for _ in range(16): svc.receive(Packet((a,)), 0, False)
    svc.seal(0, a)
    svc.receive(Packet((b,)), 0, False)
    while svc.worker_step() is None: pass
    assert svc.current.records == [a]*16
    svc.seal(0)
    while svc.worker_step() is None: pass
    assert (svc.current.records, svc.current.overflow) == (direct.records, False)

"""Independent expected transactions; these values never drive memory returns.

Only original host request, clock and reset pins are driven. Real native
controller/decoder/bank circuits must produce every compared output.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterator

CAPACITY = 8192
BANK_WORDS = 1024
HOST_WIDTHS = {"clk": 1, "rst": 1, "req_valid": 1, "req_we": 1,
               "req_addr": 32, "req_wdata": 32, "req_wstrb": 4}


def port(name: str, bit: int = 0) -> str:
    return f"port:{name}:{bit}"


def host(name: str, bit: int = 0) -> str:
    if name not in HOST_WIDTHS or not 0 <= bit < HOST_WIDTHS[name]:
        raise ValueError("only original host request/clock/reset pins may be driven")
    return f"host:{name}:{bit}"


def bus(name: str, value: int, width: int, *, drive=False) -> dict[str, bool]:
    if not 0 <= value < (1 << width):
        raise ValueError("bus value does not fit its exact width")
    return {(host if drive else port)(name, bit): bool(value >> bit & 1) for bit in range(width)}


def initial_words() -> tuple[int, ...]:
    return tuple(((index + 1) * 0x9E3779B9 ^ 0xA53C96F0) & 0xFFFFFFFF for index in range(CAPACITY // 4))


def words_sha256(words) -> str:
    _check_words(words)
    return hashlib.sha256(b"".join(word.to_bytes(4, "little") for word in words)).hexdigest()


def _check_words(words) -> None:
    if len(words) != CAPACITY // 4 or any(type(word) is not int or not 0 <= word <= 0xFFFFFFFF for word in words):
        raise ValueError("every boundary requires all 2048 exact unsigned physical words")


def storage_expect(words) -> dict[str, bool]:
    _check_words(words)
    return {f"storage:{index // BANK_WORDS}:{index % BANK_WORDS}:{bit}": bool(word >> bit & 1)
            for index, word in enumerate(words) for bit in range(32)}


def merge_bytes(old: int, new: int, strobes: int) -> int:
    if type(strobes) is not int or not 0 <= strobes < 16:
        raise ValueError("exactly four byte-lane strobes are supported")
    if any(type(word) is not int or not 0 <= word <= 0xFFFFFFFF for word in (old, new)):
        raise ValueError("lane merge requires exact unsigned 32-bit words")
    mask = sum(0xFF << (8 * lane) for lane in range(4) if strobes >> lane & 1)
    return (old & ~mask | new & mask) & 0xFFFFFFFF


def requests() -> list[dict]:
    def request(name, address, *, write=False, data=0, strobes=0):
        return dict(name=name, kind="request", address=address, write=write, data=data, strobes=strobes)

    result = [request(f"initial-{address:04x}", address) for address in (0, 4092, 4096, 8188)]
    result += [request("all-zero-low-bank", 2044, write=True, data=0, strobes=15),
               request("all-one-high-bank", 6144, write=True, data=0xFFFFFFFF, strobes=15)]
    for mask in range(16):
        result.append(request(f"lane-mask-{mask:x}", 2044 if mask % 2 == 0 else 6144,
                              write=True, data=0xA596C33C ^ (mask * 0x01010101), strobes=mask))
    result += [request("boundary-byte-before", 4095, write=True, data=0xA5000000, strobes=8),
               request("boundary-byte-after", 4096, write=True, data=0x5A, strobes=1),
               request("boundary-half-before", 4094, write=True, data=0xBEEF0000, strobes=12),
               request("boundary-half-after", 4096, write=True, data=0x1234, strobes=3)]
    result += [request(f"readback-{address:04x}", address) for address in (0, 2044, 4092, 4096, 6144, 8188)]
    for address in (8192, 8193, 0xFFFFFFFC, 0xFFFFFFFF):
        for write in (False, True):
            result.append(request(f"reject-{address:08x}-{'write' if write else 'read'}", address,
                                  write=write, data=0xBAD0CAFE, strobes=15))
    result += [dict(name="reset-read-settle", kind="reset-read-settle", address=8188,
                    write=False, data=0, strobes=0),
               dict(name="reset-completed-commit", kind="reset-completed-commit", address=8188,
                    write=True, data=0x6A95F00F, strobes=15)]
    result += [request("after-reset-low-bank", 0), request("after-reset-high-bank", 8188)]
    return result


@dataclass(frozen=True)
class Frame:
    label: str
    half_periods: int = 1
    drive: dict[str, bool] | None = None
    expect: dict[str, bool] | None = None
    stored_words: tuple[int, ...] | None = None

    def __post_init__(self):
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("scenario frame needs a nonempty label")
        if type(self.half_periods) is not int or self.half_periods < 0:
            raise ValueError("frame half-period count must be a nonnegative integer")
        for values in (self.drive, self.expect):
            if values is not None and (not isinstance(values, dict) or any(
                    not isinstance(name, str) or type(value) is not bool for name, value in values.items())):
                raise ValueError("frame signals must be named exact booleans")
        if self.stored_words is not None:
            _check_words(self.stored_words)


def functional_frames(start_words=None, *, resume=False) -> Iterator[Frame]:
    values = list(initial_words() if start_words is None else start_words)
    _check_words(values)
    safe = {port("read_enable"): False, port("write_commit"): False, port("req_ready"): False}

    def cycle(label, expect, *, rising_drive=None):
        yield Frame(label + ":rise", drive={host("clk"): True, **(rising_drive or {})}, expect=expect)
        yield Frame(label + ":fall", drive={host("clk"): False}, expect=expect)

    def reset(label, *, concurrent_clock=False):
        if not concurrent_clock:
            yield Frame(label + ":assert", drive={host("rst"): True}, expect=safe)
        for index in range(3):
            yield from cycle(f"{label}:{index}", safe,
                             rising_drive={host("rst"): True} if concurrent_clock and index == 0 else None)
        yield Frame(label + ":clear-host-request", drive={host("req_valid"): False}, expect=safe)
        yield Frame(label + ":release", drive={host("rst"): False}, expect=safe)
        yield from cycle(label + ":idle", safe)
        yield Frame(label + ":storage", 0, expect=safe, stored_words=tuple(values))

    all_low = {host(name, bit): False for name, width in HOST_WIDTHS.items() for bit in range(width)}
    if resume:
        yield Frame("reopened-idle-hold", 4, drive=all_low, expect=safe, stored_words=tuple(values))
        operations = [dict(name="reopened-low", kind="request", address=0, write=False, data=0, strobes=0),
                      dict(name="reopened-high", kind="request", address=8188, write=False, data=0, strobes=0),
                      dict(name="fresh-process-write", kind="request", address=4096, write=True, data=0xC33CA55A, strobes=15),
                      dict(name="fresh-process-read", kind="request", address=4096, write=False, data=0, strobes=0)]
    else:
        yield Frame("initial-host-reset", 4, drive=all_low | {host("rst"): True}, expect=safe)
        yield from reset("cold-reset")
        operations = requests()

    selected_bank = 0
    selected_row = 0
    for operation in operations:
        name, address = operation["name"], operation["address"]
        drive = bus("req_addr", address, 32, drive=True) | bus("req_wdata", operation["data"], 32, drive=True)
        drive |= bus("req_wstrb", operation["strobes"], 4, drive=True)
        drive |= {host("req_valid"): True, host("req_we"): operation["write"]}
        yield Frame(name + ":request-setup", drive=drive, expect=safe)
        if address >= CAPACITY:
            completed = safe | {port("req_ready"): True} | bus("req_rdata", 0, 32)
            completed |= bus("bank_select", 1 << selected_bank, 2) | bus("row_address", selected_row, 10)
            yield from cycle(name + ":reject", completed)
            yield Frame(name + ":storage", 0, expect=completed, stored_words=tuple(values))
            yield from cycle(name + ":return-idle", safe)
            continue

        selected_bank, selected_row = address // 4096, (address // 4) % 1024
        selected = bus("bank_select", 1 << selected_bank, 2) | bus("row_address", selected_row, 10)
        yield from cycle(name + ":address-settle", safe | selected)
        reading = safe | selected | {port("read_enable"): True}
        yield from cycle(name + ":read-settle", reading)
        if operation["kind"] == "reset-read-settle":
            # Reset and the next clock transition are simultaneous. Do not add
            # a hidden settling interval that could conceal selector hazards.
            yield from reset(name + ":reset", concurrent_clock=True)
            selected_bank = selected_row = 0
            continue
        yield from cycle(name + ":access", reading)
        expected = values[address // 4]
        if operation["write"]:
            expected = merge_bytes(expected, operation["data"], operation["strobes"])
            values[address // 4] = expected
            committing = reading | {port("write_commit"): True} | bus("write_data", expected, 32)
            yield from cycle(name + ":commit", committing)
            if operation["kind"] == "reset-completed-commit":
                # Level-sensitive native storage has already seen the entire
                # intended commit pulse. Prove that fact before resetting;
                # then reject a wrong-bank/row write as selectors reset.
                yield Frame(name + ":intended-write-complete", 0, expect=committing, stored_words=tuple(values))
                yield from reset(name + ":reset", concurrent_clock=True)
                selected_bank = selected_row = 0
                continue
            yield from cycle(name + ":post-commit", reading | bus("write_data", expected, 32))
        completed = safe | selected | {port("req_ready"): True} | bus("req_rdata", expected, 32)
        yield from cycle(name + ":response", completed)
        yield Frame(name + ":storage", 0, expect=completed, stored_words=tuple(values))
        # req_valid remains high across transfers; RESP consumes one cycle
        # before IDLE may accept the next request. No artificial valid-low gap.
        yield from cycle(name + ":return-idle", safe)
    # Fresh-import input-drive validation requires all saved external pads to
    # be air. Clear host inputs while clock is low and controller is idle;
    # no selected_word or internal register/storage signal is driven.
    yield Frame("clear-all-host-pads", 4, drive=all_low, expect=safe, stored_words=tuple(values))


def summarize_frames(frames: list[Frame]) -> dict:
    boundaries = [frame for frame in frames if frame.stored_words is not None]
    if not boundaries:
        raise ValueError("cache request plan must assert complete physical storage")
    return {
        "frames": len(frames), "clock_rising_edges": sum(bool((frame.drive or {}).get(host("clk"))) for frame in frames),
        "half_period_units": sum(frame.half_periods for frame in frames),
        "storage_boundaries": len(boundaries), "storage_bits_per_boundary": CAPACITY * 8,
        "full_storage_assertions": len(boundaries) * CAPACITY * 8,
        "boundary_states": [{"label": frame.label, "words_sha256": words_sha256(frame.stored_words)} for frame in boundaries],
        "final_words": list(boundaries[-1].stored_words),
    }


def trace_budget(frames: list[Frame], *, half_period: int, initial_ticks: int,
                 traced_outputs: int, max_trace_samples: int = 20_000_000) -> dict:
    """Count every captured sample before allocating a scenario or engine run.

    The engine captures only step ticks, excluding initial_ticks. The retained
    bool vectors, serde_json::Value report and serialization are separate live
    allocations; this sample limit is not a claim about total process RSS.
    """
    if (type(half_period) is not int or half_period < 1
            or type(initial_ticks) is not int or initial_ticks < 0):
        raise ValueError("runtime tick counts must be explicit nonnegative integers")
    if type(traced_outputs) is not int or traced_outputs < 0:
        raise ValueError("traced output count must be a nonnegative integer")
    if type(max_trace_samples) is not int or max_trace_samples < 1:
        raise ValueError("trace-sample budget must be a positive integer")
    if not frames:
        raise ValueError("empty cache runtime scenario")
    traced_ticks = sum(frame.half_periods for frame in frames) * half_period
    samples = traced_ticks * traced_outputs
    if samples > max_trace_samples:
        raise ValueError(f"trace budget exceeded: {samples} samples > {max_trace_samples}; split the observation run or set an explicit measured budget")
    return {
        "traced_ticks_per_output": traced_ticks, "traced_outputs": traced_outputs,
        "total_trace_samples": samples, "maximum_trace_samples": max_trace_samples,
        "total_execution_ticks": initial_ticks + traced_ticks,
        "initial_ticks_excluded_from_trace": initial_ticks,
        "minimum_bool_vector_payload_bytes": samples,
        "memory_scope": "Sample count and minimum bool payload only; report Value, vector capacity, native world and stdout serialization require external whole-process RSS measurement",
    }


def write_scenario(path: Path, frames: list[Frame], inputs: dict, outputs: dict, *,
                   half_period: int, initial_ticks: int, trace_outputs: list[str] | None,
                   max_trace_samples: int = 20_000_000) -> dict:
    required_inputs = {host(name, bit) for name, width in HOST_WIDTHS.items() for bit in range(width)}
    if set(inputs) != required_inputs:
        raise ValueError("scenario must drive exactly the 72 original host inputs")
    if trace_outputs is not None and (not trace_outputs or len(set(trace_outputs)) != len(trace_outputs)
                                      or not set(trace_outputs) <= outputs.keys()):
        raise ValueError("trace selection must be a nonempty distinct subset of real outputs")
    budget = trace_budget(frames, half_period=half_period, initial_ticks=initial_ticks,
                          traced_outputs=len(trace_outputs or ()), max_trace_samples=max_trace_samples)
    if any(frame.stored_words is not None for frame in frames):
        if not storage_expect(initial_words()).keys() <= outputs.keys():
            raise ValueError("a full-storage expectation has no native observable")
    for frame in frames:
        if not (frame.expect or {}).keys() <= outputs.keys():
            raise ValueError("an expectation has no native observable")
        if not (frame.drive or {}).keys() <= inputs.keys():
            raise ValueError("a drive refers to a memory/internal pin")
    if not any(frame.expect or frame.stored_words is not None for frame in frames) and not trace_outputs:
        raise ValueError("scenario has no assertions or captured observations")
    if path.exists():
        raise FileExistsError(path)
    # Stream full-boundary maps so Python never retains a second complete
    # expanded scenario. The actual engine independently reads every entry.
    with path.open("x") as stream:
        header = dict(inputs=inputs, outputs=outputs, initial_ticks=initial_ticks, capture_trace=trace_outputs is not None)
        if trace_outputs is not None:
            header["trace_outputs"] = trace_outputs
        stream.write(json.dumps(header, separators=(",", ":"))[:-1] + ',"steps":[')
        assertions = 0
        for index, frame in enumerate(frames):
            expect = dict(frame.expect or {})
            if frame.stored_words is not None:
                expect.update(storage_expect(frame.stored_words))
            step = dict(ticks=frame.half_periods * half_period, expect=expect)
            if frame.drive:
                step["drive"] = frame.drive
            if index:
                stream.write(",")
            json.dump(step, stream, separators=(",", ":"))
            assertions += len(expect)
        stream.write("]}\n")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"scenario_sha256": digest, "assertions": assertions, "trace_budget": budget,
            "ticks": budget["total_execution_ticks"],
            "trace_outputs": len(trace_outputs or ()),
            "full_storage_boundaries": sum(frame.stored_words is not None for frame in frames)}

"""Replay one reachable raw-bank pointer transition with optimistic gate delays."""
import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path


def replay(mapped):
    module = json.loads(mapped.read_bytes())["modules"]["input_window_bank"]
    ports = module["ports"]
    delays = {"$_NAND_": 3, "$_NOR_": 3, "$_NOT_": 2}
    gates = {}
    registers = {}
    for cell in module["cells"].values():
        kind = cell["type"]
        pins = cell["connections"]
        if kind == "DFF":
            registers[pins["Q"][0]] = pins["D"][0]
        else:
            inputs = tuple(bits[0] for pin, bits in pins.items() if pin != "Y")
            gates[pins["Y"][0]] = (kind, inputs)

    # Sixteen ordinary epochs saturate the prefix. Fifteen independently
    # released urgent presses fill exactly the first fifteen urgent slots.
    # Use controller bit 0 for each ordinary press and bit 6 for each urgent press.
    stable = {"0": False, "1": True}
    stable.update({q: False for q in registers})
    for port in ports.values():
        if port["direction"] == "input":
            stable.update({bit: False for bit in port["bits"]})
    def set_word(name, word):
        for index, bit in enumerate(module["netnames"][name]["bits"]):
            stable[bit] = bool(word & (1 << index))

    for index in range(16):
        set_word(f"prefix[{index}]", (1 << 18) | (1 << 8))
    for index in range(15):
        set_word(f"urgent[{index}]", (1 << 6) << 2)
    stable[ports["lost"]["bits"][0]] = True
    stable[ports["active"]["bits"][0]] = True
    stable[ports["next_prefix"]["bits"][16]] = True
    pointer_q = ports["next_urgent"]["bits"][15]
    stable[pointer_q] = True
    pointer_d = registers[pointer_q]
    changed = {ports[name]["bits"][7] for name in ("levels", "edges")}

    @lru_cache(maxsize=None)
    def value(bit, tick):
        if bit in stable:
            return stable[bit] or (tick >= 0 and bit in changed)
        kind, inputs = gates[bit]
        previous = max(-1, tick - delays[kind])
        inputs_high = [value(pin, previous) for pin in inputs]
        return not (any(inputs_high) if kind == "$_NOR_" else all(inputs_high))

    samples = [value(pointer_d, tick) for tick in range(-1, 71)]
    first_low = samples.index(False) - 1
    assert all(samples[:first_low + 1])
    assert not any(samples[first_low + 1:])
    assert first_low == 44
    return {
        "mapped_sha256": hashlib.sha256(mapped.read_bytes()).hexdigest(),
        "destination": "next_urgent[15] D",
        "old_bit": True,
        "new_bit": False,
        "first_change_rt": first_low,
        "optimistic_launch_plus_path_plus_setup_rt": 7 + first_low + 8,
        "wire_and_skew_allowance_at_70rt": 70 - 7 - first_low - 8,
        "gate_delay_model_rt": delays,
        "timing_model": "Ideal zero-wire connections, fixed local gate delays, settled prestate.",
        "native_receiver_timing_verified": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mapped", type=Path, help="Production mapping of input_window_bank")
    print(json.dumps(replay(parser.parse_args().mapped), indent=2))

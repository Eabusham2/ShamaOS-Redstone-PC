from copy import deepcopy

import pytest

from experiments.input_registered_bank.fixture import build
from experiments.input_registered_bank.prove import check_launch, events_and_words, scenario_steps


def test_saved_inputs_and_complete_physical_bank():
    blocks, controls, probes = build()
    assert set(controls) == {"enable", "seed", *[f"word{b}" for b in range(32)]}
    states = {item.pos: item.block for item in blocks}
    assert len(set(controls.values())) == 34
    assert all(states[point].name == "minecraft:lever" for point in controls.values())
    assert len({probes[f"launch{b}_{p}"] for b in range(32) for p in "DCQ"}) == 96
    assert len({probes[f"sel{row}_{p}"] for row in range(16) for p in "DCQ"}) == 48
    assert len({probes[f"bit{row}_{bit}"] for row in range(16) for bit in range(32)}) == 512
    assert len({probes[f"terminal{b}"] for b in range(32)} & set(controls.values())) == 0


def test_uses_only_saved_controls_for_four_walking_row_tokens():
    controls = {name: [index, 20, 4] for index, name in enumerate(
        ["enable", "seed", *[f"word{b}" for b in range(32)]])}
    events, _ = events_and_words()
    epochs, steps = scenario_steps(157, controls, events)
    assert len(events) == len(epochs) == 80
    assert all(set(step) == {"use_blocks", "ticks"} for step in steps)
    assert all(step["ticks"] > 0 for step in steps)
    assert all(mask == (1 << (i % 20) if i % 20 < 16 else 0)
               for i, (mask, _) in enumerate(events))
    assert sum(controls["seed"] in step["use_blocks"] for step in steps) == 8


def launch_wave():
    def square(start):
        return [start<=t<start+7*70 and (t-start)%70<35 for t in range(550)]
    def pulse(start,end):
        return [start<=t<end for t in range(550)]
    return {"oscillator":square(5),"launch1_C":square(31),"launch1_D":pulse(195,265),
            "launch1_Q":pulse(248,318),"terminal1":pulse(260,330)}


@pytest.mark.parametrize("breakage",["late", "glitch", "input_flip", "split_clock"])
def test_launch_review_rejects_causal_faults(breakage):
    wave=launch_wave()
    events=[(0,2),(0,0)]
    epochs=[215,285]
    assert check_launch(wave,5,epochs,events,1)["terminal_rt"] == 45
    damaged=deepcopy(wave)
    if breakage=="late":
        damaged["terminal1"]=[False]*6+wave["terminal1"][:-6]
    elif breakage=="glitch":
        damaged["launch1_Q"][400]=True
    elif breakage=="input_flip":
        damaged["launch1_D"][241]=False
    else:
        damaged["launch1_C"][42]=False
    with pytest.raises(ValueError):
        check_launch(damaged,5,epochs,events,1)

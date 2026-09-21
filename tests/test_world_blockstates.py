from shamaos.hardware.display import LAMP_OFF
from shamaos.hardware.memory import comparator, repeater
from shamaos.model import BlockState
from shamaos.worldio import to_amulet_block


def props(block):
    return {k: str(v) for k, v in block.properties.items()}


def test_world_writer_preserves_functional_redstone_properties():
    locked = to_amulet_block(repeater("east", powered=True, locked=True))
    lp = props(locked)
    assert locked.namespace == "minecraft"
    assert locked.base_name == "repeater"
    assert lp["facing"] == '"east"'
    assert lp["powered"] == '"true"'
    assert lp["locked"] == '"true"'
    assert lp["delay"] == '"1"'

    comp = to_amulet_block(comparator("north", powered=True))
    cp = props(comp)
    assert cp["facing"] == '"north"'
    assert cp["mode"] == '"subtract"'
    assert cp["powered"] == '"true"'

    lamp = to_amulet_block(LAMP_OFF)
    assert props(lamp)["lit"] == '"false"'

    wire = to_amulet_block(BlockState.of("minecraft:redstone_wire"))
    assert wire.base_name == "redstone_wire"

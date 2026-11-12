//! JSON harness around the actual MCHPRS redstone simulator. No logic-cell
//! functions are implemented here: generated Minecraft blocks drive all work.
use mchprs_blocks::blocks::Block;
use mchprs_blocks::{BlockFace, BlockPos};
use mchprs_world::{testing::TestWorld, World};
use serde::Deserialize;
use std::collections::HashMap;
use std::io::{self, Read};

#[derive(Deserialize)]
struct Placement {
    pos: [i32; 3],
    name: String,
    properties: HashMap<String, String>,
}

#[derive(Deserialize)]
struct Step {
    #[serde(default)]
    drive: HashMap<String, bool>,
    #[serde(default)]
    use_blocks: Vec<[i32; 3]>,
    ticks: usize,
    #[serde(default)]
    expect: HashMap<String, bool>,
    #[serde(default)]
    continuous: bool,
}

#[derive(Deserialize)]
struct Case {
    blocks: Vec<Placement>,
    #[serde(default)]
    inputs: HashMap<String, [i32; 3]>,
    outputs: HashMap<String, [i32; 3]>,
    #[serde(default = "default_initial_ticks")]
    initial_ticks: usize,
    steps: Vec<Step>,
}

fn default_initial_ticks() -> usize { 32 }
fn pos(p: [i32; 3]) -> BlockPos { BlockPos::new(p[0], p[1], p[2]) }

fn tick(world: &mut TestWorld) {
    world.to_be_ticked.sort_by_key(|entry| (entry.ticks_left, entry.tick_priority));
    for entry in &mut world.to_be_ticked {
        entry.ticks_left = entry.ticks_left.saturating_sub(1);
    }
    while world.to_be_ticked.first().is_some_and(|entry| entry.ticks_left == 0) {
        let entry = world.to_be_ticked.remove(0);
        mchprs_redstone::tick(world.get_block(entry.pos), world, entry.pos);
    }
}

fn powered(world: &TestWorld, p: BlockPos) -> bool {
    match world.get_block(p) {
        Block::RedstoneWire(wire) => wire.power > 0,
        Block::Repeater(rep) => rep.powered,
        Block::RedstoneLamp { lit } => lit,
        Block::RedstoneTorch { lit } | Block::RedstoneWallTorch { lit, .. } => lit,
        block => mchprs_redstone::get_redstone_power(block, world, p, BlockFace::Top) > 0,
    }
}

fn verify(world: &TestWorld, case: &Case, expected: &HashMap<String, bool>, index: usize, tick: usize) {
    for (name, value) in expected {
        let p = pos(*case.outputs.get(name).expect("unknown output name"));
        assert_eq!(powered(world, p), *value,
            "step={index} tick={tick} output={name} block={:?}", world.get_block(p));
    }
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("read stdin");
    let case: Case = serde_json::from_str(&input).expect("case JSON");
    assert!(!case.blocks.is_empty());
    let max_x = case.blocks.iter().map(|b| b.pos[0]).max().unwrap() + 32;
    let max_y = case.blocks.iter().map(|b| b.pos[1]).max().unwrap() + 32;
    let max_z = case.blocks.iter().map(|b| b.pos[2]).max().unwrap() + 32;
    let mut world = TestWorld::new(max_x / 16 + 1, max_y / 16 + 1, max_z / 16 + 1);
    for placement in &case.blocks {
        assert!(placement.pos.iter().all(|coordinate| *coordinate >= 0), "negative fixture coordinate");
        let mut block = Block::from_name(&placement.name).unwrap_or_else(|| panic!("MCHPRS does not implement {}", placement.name));
        block.set_properties(placement.properties.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect());
        world.set_block(pos(placement.pos), block);
    }
    // A complete neighbor-update pass starts preplaced clocks/torches and
    // normalizes wire states, followed by the simulator's scheduled tick queue.
    for placement in &case.blocks {
        let p = pos(placement.pos);
        mchprs_redstone::update(world.get_block(p), &mut world, p);
        mchprs_redstone::update_surrounding_blocks(&mut world, p);
    }
    for _ in 0..case.initial_ticks { tick(&mut world); }
    let mut total_ticks = case.initial_ticks;
    for (index, step) in case.steps.iter().enumerate() {
        for (name, value) in &step.drive {
            let pin = pos(*case.inputs.get(name).expect("unknown input name"));
            let driver = pin.offset(BlockFace::South);
            world.set_block(driver, if *value { Block::RedstoneBlock } else { Block::Air });
            mchprs_redstone::update_surrounding_blocks(&mut world, driver);
        }
        for point in &step.use_blocks {
            let p = pos(*point);
            mchprs_redstone::on_use(world.get_block(p), &mut world, p);
        }
        for offset in 0..step.ticks {
            tick(&mut world);
            total_ticks += 1;
            if step.continuous { verify(&world, &case, &step.expect, index, offset); }
        }
        verify(&world, &case, &step.expect, index, step.ticks);
    }
    println!("{{\"runtime\":\"MCHPRS redstone\",\"revision\":\"f9e10d2572116bf5dcb9a36a305f28e40641a61a\",\"steps\":{},\"ticks\":{},\"status\":\"passed\"}}", case.steps.len(), total_ticks);
}

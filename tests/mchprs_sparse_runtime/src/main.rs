//! Experimental all-loaded sparse world. Every redstone operation is performed
//! by the pinned MCHPRS engine; this layer only stores/imports actual blocks.
use mchprs_blocks::block_entities::BlockEntity;
use mchprs_blocks::blocks::Block;
use mchprs_blocks::{BlockFace, BlockPos};
use mchprs_world::storage::{Chunk, ChunkSection};
use mchprs_world::{TickEntry, TickPriority, World};
use serde::Deserialize;
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::time::Instant;

const SECTIONS: usize = 24;
const Y_OFFSET: i32 = 64;

mod snapshot;
mod pending;
use pending::PendingTicks;

struct HashedReader {
    source: File,
    hash_input: ChildStdin,
}

impl Read for HashedReader {
    fn read(&mut self, bytes: &mut [u8]) -> std::io::Result<usize> {
        let count = self.source.read(bytes)?;
        self.hash_input.write_all(&bytes[..count])?;
        Ok(count)
    }
}

fn hashing_reader(path: &str) -> (BufReader<HashedReader>, Child) {
    let source = File::open(path).expect("input file");
    // Hash the exact bytes consumed by the parser, using bounded pipes rather
    // than rereading a possibly replaced file or retaining a second JSON copy.
    let mut hasher = Command::new("sha256sum")
        .stdin(Stdio::piped()).stdout(Stdio::piped()).spawn()
        .expect("sha256sum is required to bind runtime evidence to imported bytes");
    let hash_input = hasher.stdin.take().expect("hash input pipe");
    (BufReader::new(HashedReader { source, hash_input }), hasher)
}

fn finish_hash(hasher: Child) -> String {
    let result = hasher.wait_with_output().expect("stream hash process");
    assert!(result.status.success(), "stream hashing failed");
    let output = String::from_utf8(result.stdout).expect("stream hash UTF-8");
    let digest = output.split_whitespace().next().expect("missing stream hash");
    assert!(digest.len() == 64 && digest.bytes().all(|byte| byte.is_ascii_hexdigit()), "invalid stream hash");
    digest.to_string()
}

#[derive(Deserialize)]
struct State {
    name: String,
    #[serde(default)]
    properties: HashMap<String, String>,
}

impl State {
    fn block(&self) -> Block {
        let mut block = Block::from_name(&self.name)
            .unwrap_or_else(|| panic!("MCHPRS does not implement {}", self.name));
        block.set_properties(self.properties.iter().map(|(k,v)| (k.as_str(),v.as_str())).collect());
        let actual:HashMap<String,String>=block.properties().into_iter().map(|(k,v)|(k.to_string(),v)).collect();
        assert_eq!(actual,self.properties,"engine state would lose/change native properties for {}",self.name);
        assert_eq!(block.get_name(),self.name,"engine state would change native block identity");
        assert_eq!(Block::from_id(block.get_id()),block,"native state ID does not round-trip");
        block
    }
}

#[derive(Deserialize)]
#[serde(tag="kind")]
enum Record {
    #[serde(rename="header")]
    Header { schema: String, surface_y: Option<i32>, source: serde_json::Value },
    #[serde(rename="section")]
    Section { chunk: [i32;2], y: i32, bits: u8, palette: Vec<State>, data: Vec<u64>, nonair: u32 },
    #[serde(rename="uniform_section")]
    UniformSection { chunk: [i32;2], y: i32, state: State, nonair: u32 },
    #[serde(rename="comparator")]
    Comparator { pos: [i32;3], output_strength: u8 },
    #[serde(rename="complete")]
    Complete { chunks: usize, sections: usize, nonair: u64, comparators: usize },
}

fn native_section(bits:u8,palette:Vec<State>,data:Vec<u64>,nonair:u32)->ChunkSection {
    assert!((4..=12).contains(&bits));
    assert!(!palette.is_empty() && palette.len()<=4096,"invalid native palette length");
    let expected_bits=4u8.max((usize::BITS-(palette.len()-1).leading_zeros()) as u8);
    assert_eq!(bits,expected_bits,"native palette bit width does not match its length");
    let ids:Vec<u32>=palette.iter().map(|s|s.block().get_id()).collect();
    assert!(ids.iter().all(|id|*id<32768),"state outside pinned engine ID range");
    assert_eq!(ids.iter().copied().collect::<HashSet<_>>().len(),ids.len(),"duplicate native palette state");
    let per_long=64/bits as usize;
    assert_eq!(data.len(),4096usize.div_ceil(per_long));
    let mask=(1u64<<bits)-1;
    let mut actual=0;
    let mut direct=if bits>=9 {vec![0u64;1024]} else {Vec::new()};
    for index in 0..4096 {
        let palette_id=((data[index/per_long]>>((index%per_long)*bits as usize))&mask) as usize;
        let id=*ids.get(palette_id).expect("native palette index out of range");
        actual+=u32::from(id!=0);
        if bits>=9 {direct[index/4]|=(id as u64)<<((index%4)*15);}
    }
    assert_eq!(actual,nonair,"native non-air count mismatch");
    // Native Anvil still uses a local palette above 8 bits. Pinned MCHPRS
    // interprets 9+ bits as direct IDs, so repack without changing any state.
    if bits>=9 {ChunkSection::from_raw(direct,15,Vec::new(),actual)}
    else {ChunkSection::from_raw(data,bits,ids,actual)}
}

fn uniform_native_section(state:State,nonair:u32)->ChunkSection {
    let id=state.block().get_id();
    assert!(id<32768,"state outside pinned engine ID range");
    assert_eq!(nonair,if id==0 {0} else {4096},"uniform section non-air count mismatch");
    ChunkSection::from_raw(vec![0;256],4,vec![id],nonair)
}

fn register_section(chunk:[i32;2],y:i32,seen:&mut HashSet<(i32,i32,i32)>) {
    assert!((-4..20).contains(&y),"native section outside Java height");
    assert!(chunk.iter().all(|value| (i64::from(*value)*16>=-29_999_984) && (i64::from(*value)*16+15<=29_999_983)),
            "native chunk outside safe Java world border");
    assert!(seen.insert((chunk[0],y,chunk[1])),"duplicate native section");
}

struct SparseWorld {
    chunks: HashMap<(i32,i32),Chunk>,
    background: Chunk,
    pending: PendingTicks,
    maximum_chunks: usize,
    scheduled_total: u64,
    pending_peak: usize,
}

impl SparseWorld {
    fn new(surface_y: Option<i32>, maximum_chunks: usize) -> Self {
        let mut background = Chunk::empty(0,0,SECTIONS);
        if let Some(surface) = surface_y {
            assert!((-63..=319).contains(&surface));
            let required=|name:&str| Block::from_name(name)
                .unwrap_or_else(||panic!("MCHPRS registry does not implement native terrain {name}; refusing to omit or replace it")).get_id();
            let bedrock = required("minecraft:bedrock");
            let dirt = required("minecraft:dirt");
            let grass = required("minecraft:grass_block");
            for sy in 0..=((surface+Y_OFFSET)/16) as usize {
                let mut data=vec![0u64;256];
                let mut nonair=0;
                for index in 0..4096 {
                    let y=(sy*16+(index>>8)) as i32-Y_OFFSET;
                    let state=if y == -64 {1} else if y < surface {2} else if y == surface {3} else {0};
                    data[index/16] |= state << ((index%16)*4);
                    nonair += u32::from(state!=0);
                }
                background.sections[sy]=ChunkSection::from_raw(data,4,vec![0,bedrock,dirt,grass],nonair);
            }
        }
        Self {chunks:HashMap::new(),background,pending:PendingTicks::default(),maximum_chunks,
              scheduled_total:0,pending_peak:0}
    }

    fn ensure_chunk(&mut self, x:i32,z:i32) -> &mut Chunk {
        if !self.chunks.contains_key(&(x,z)) {
            assert!(self.chunks.len()<self.maximum_chunks,"explicit loaded-chunk memory limit reached; import is incomplete");
            let mut chunk=self.background.clone();
            chunk.x=x;chunk.z=z;
            self.chunks.insert((x,z),chunk);
        }
        self.chunks.get_mut(&(x,z)).unwrap()
    }

    fn tick(&mut self) {
        // Exact stable-priority ordering used by pinned PlotWorld/TestWorld.
        self.pending.begin_tick();
        while let Some(entry)=self.pending.pop_ready() {
            mchprs_redstone::tick(self.get_block(entry.pos),self,entry.pos);
        }
    }
}

impl World for SparseWorld {
    fn get_block_raw(&self,pos:BlockPos)->u32 {
        if !(0..384).contains(&pos.y) {return 0;}
        self.chunks.get(&(pos.x.div_euclid(16),pos.z.div_euclid(16)))
            .unwrap_or(&self.background).get_block((pos.x&15) as u32,pos.y as u32,(pos.z&15) as u32)
    }
    fn set_block_raw(&mut self,pos:BlockPos,block:u32)->bool {
        if !(0..384).contains(&pos.y) {return false;}
        if self.get_block_raw(pos)==block {return false;}
        self.ensure_chunk(pos.x.div_euclid(16),pos.z.div_euclid(16))
            .set_block((pos.x&15) as u32,pos.y as u32,(pos.z&15) as u32,block)
    }
    fn delete_block_entity(&mut self,pos:BlockPos) {
        if let Some(chunk)=self.chunks.get_mut(&(pos.x.div_euclid(16),pos.z.div_euclid(16))) {
            chunk.delete_block_entity(BlockPos::new(pos.x&15,pos.y,pos.z&15));
        }
    }
    fn get_block_entity(&self,pos:BlockPos)->Option<&BlockEntity> {
        self.chunks.get(&(pos.x.div_euclid(16),pos.z.div_euclid(16)))?
            .get_block_entity(BlockPos::new(pos.x&15,pos.y,pos.z&15))
    }
    fn set_block_entity(&mut self,pos:BlockPos,entity:BlockEntity) {
        self.ensure_chunk(pos.x.div_euclid(16),pos.z.div_euclid(16))
            .set_block_entity(BlockPos::new(pos.x&15,pos.y,pos.z&15),entity);
    }
    fn get_chunk(&self,x:i32,z:i32)->Option<&Chunk> {Some(self.chunks.get(&(x,z)).unwrap_or(&self.background))}
    fn get_chunk_mut(&mut self,x:i32,z:i32)->Option<&mut Chunk> {Some(self.ensure_chunk(x,z))}
    fn schedule_tick(&mut self,pos:BlockPos,delay:u32,priority:TickPriority) {
        self.pending.push(TickEntry{pos,ticks_left:delay,tick_priority:priority});
        self.scheduled_total+=1;
        self.pending_peak=self.pending_peak.max(self.pending.len());
    }
    fn pending_tick_at(&mut self,pos:BlockPos)->bool {self.pending.contains(pos)}
}

#[derive(Deserialize)]
struct Step {
    #[serde(default)] drive:HashMap<String,bool>,
    #[serde(default)] use_blocks:Vec<[i32;3]>,
    ticks:usize,
    #[serde(default)] expect:HashMap<String,bool>,
    #[serde(default)] continuous:bool,
}

#[derive(Deserialize)]
struct Scenario {
    #[serde(default)] inputs:HashMap<String,[i32;3]>,
    outputs:HashMap<String,[i32;3]>,
    initial_ticks:usize,
    #[serde(default)] capture_trace:bool,
    #[serde(default,deserialize_with="deserialize_trace_outputs")] trace_outputs:Option<Vec<String>>,
    steps:Vec<Step>,
}

fn deserialize_trace_outputs<'de,D:serde::Deserializer<'de>>(deserializer:D)
    ->Result<Option<Vec<String>>,D::Error> {
    // Omitted means all outputs; an explicitly present value must be a list,
    // so null cannot silently turn a requested selection into full capture.
    Vec::<String>::deserialize(deserializer).map(Some)
}

fn native_pos(p:[i32;3])->BlockPos {
    assert!((-64..=319).contains(&p[1]),"scenario/native coordinate outside Java height: {p:?}");
    assert!((-29_999_984..=29_999_983).contains(&p[0]) && (-29_999_984..=29_999_983).contains(&p[2]),
            "scenario/native coordinate outside safe Java world border: {p:?}");
    BlockPos::new(p[0],p[1]+Y_OFFSET,p[2])
}

fn validate_scenario(world: &SparseWorld, scenario: &Scenario) -> usize {
    assert!(!scenario.outputs.is_empty(), "scenario has no observable outputs");
    assert!(!scenario.steps.is_empty(), "scenario has no execution steps");
    assert!(scenario.initial_ticks>0 || scenario.steps.iter().any(|step|step.ticks>0), "scenario executes no ticks");
    let mut pads=HashSet::new();
    for point in scenario.inputs.values() {
        native_pos(*point);
        let pad=native_pos([point[0],point[1],point[2]+1]);
        assert!(pads.insert(pad),"duplicate external input drive pad");
        assert_eq!(world.get_block_raw(pad),0,"external drive pad would overwrite a saved block at {pad:?}");
    }
    for point in scenario.outputs.values() {
        let pos=native_pos(*point);
        assert!(world.chunks.contains_key(&(pos.x.div_euclid(16),pos.z.div_euclid(16))) && world.get_block_raw(pos)!=0,
                "observable output is not a saved non-air block: {point:?}");
    }
    let mut assertions=0;
    for step in &scenario.steps {
        for name in step.drive.keys() {assert!(scenario.inputs.contains_key(name),"unknown input name {name}");}
        for name in step.expect.keys() {assert!(scenario.outputs.contains_key(name),"unknown output name {name}");}
        assertions+=step.expect.len();
        for point in &step.use_blocks {
            let pos=native_pos(*point);
            assert!(world.chunks.contains_key(&(pos.x.div_euclid(16),pos.z.div_euclid(16))), "actuator is outside saved chunks");
            assert!(matches!(world.get_block(pos),Block::Lever{..}|Block::StoneButton{..}|Block::Repeater(_)|Block::Comparator(_)|Block::RedstoneWire(_)),
                    "use_blocks must address an actual supported saved actuator: {point:?}");
        }
    }
    assert!(assertions>0 || (scenario.capture_trace && scenario.steps.iter().any(|step|step.ticks>0)),
            "scenario has neither assertions nor observed tick traces");
    assertions
}

fn trace_points(scenario:&Scenario)->Vec<(&str,BlockPos)> {
    if let Some(names)=&scenario.trace_outputs {
        assert!(scenario.capture_trace,"trace_outputs requires capture_trace=true");
        assert!(!names.is_empty(),"trace_outputs must select at least one declared output");
        let mut seen=HashSet::new();
        names.iter().map(|name| {
            assert!(seen.insert(name.as_str()),"duplicate trace output name {name}");
            let point=scenario.outputs.get(name)
                .unwrap_or_else(||panic!("unknown trace output name {name}"));
            (name.as_str(),native_pos(*point))
        }).collect()
    } else {
        // Keep the existing all-output map, including its empty arrays when
        // capture_trace is disabled. Selection never narrows step.expect.
        scenario.outputs.iter().map(|(name,point)|(name.as_str(),native_pos(*point))).collect()
    }
}

fn powered(world:&SparseWorld,p:BlockPos)->bool {
    match world.get_block(p) {
        Block::RedstoneWire(wire)=>wire.power>0,
        Block::Repeater(rep)=>rep.powered,
        Block::RedstoneLamp{lit}=>lit,
        Block::RedstoneTorch{lit}|Block::RedstoneWallTorch{lit,..}=>lit,
        block=>mchprs_redstone::get_redstone_power(block,world,p,BlockFace::Top)>0,
    }
}

fn verify(world:&SparseWorld,scenario:&Scenario,expected:&HashMap<String,bool>,step:usize,tick:usize) {
    for (name,value) in expected {
        let p=native_pos(*scenario.outputs.get(name).expect("unknown output name"));
        assert_eq!(powered(world,p),*value,"step={step} tick={tick} output={name} block={:?}",world.get_block(p));
    }
}

fn rss_kib(field:&str)->Option<u64> {
    std::fs::read_to_string("/proc/self/status").ok()?.lines()
        .find(|line|line.starts_with(field))?.split_whitespace().nth(1)?.parse().ok()
}

fn main() {
    let args:Vec<String>=std::env::args().collect();
    if args.len()==2 && args[1]=="--registry" {
        let mut states=Vec::new();
        for id in 0..32768 {
            let block=Block::from_id(id);
            if block.get_id()==id {
                states.push(serde_json::json!({"id":id,"name":block.get_name(),"properties":block.properties(),
                    "solid":block.is_solid(),"cube":block.is_cube(),"transparent":block.is_transparent(),
                    "stone":block.is_stone(),"wood":block.is_wood(),"wool":block.is_wool(),"glass":block.is_glass()}));
            }
        }
        println!("{}",serde_json::json!({"states":states}));
        return;
    }
    assert!((3..=6).contains(&args.len()),"usage: sparse-runner WORLD.jsonl SCENARIO.json [MAX_CHUNKS] [--snapshot OUTPUT.jsonl]");
    let (maximum_chunks, snapshot_path) = match args.len() {
        3 => (100_000, None),
        4 => (args[3].parse().expect("MAX_CHUNKS"), None),
        5 if args[3] == "--snapshot" => (100_000, Some(args[4].as_str())),
        6 if args[4] == "--snapshot" => (args[3].parse().expect("MAX_CHUNKS"), Some(args[5].as_str())),
        _ => panic!("invalid sparse runner arguments"),
    };
    let started=Instant::now();
    let (reader,world_hasher)=hashing_reader(&args[1]);
    let mut lines=reader.lines();
    let header:Record=serde_json::from_str(&lines.next().expect("missing header").expect("header read")).expect("header JSON");
    let (surface_y,source,uniform_encoding)=match header {
        Record::Header{schema,surface_y,source}=>{
            assert!(schema=="shama-native-anvil-sections-v1" || schema=="shama-native-anvil-sections-v2","unsupported native stream schema");
            (surface_y,source,schema=="shama-native-anvil-sections-v2")
        },
        _=>panic!("first record must be header"),
    };
    let mut world=SparseWorld::new(surface_y,maximum_chunks);
    let mut seen=HashSet::new();
    let mut nonair_total=0u64;
    let mut comparator_count=0;
    let mut uniform_sections=0;
    let mut complete=false;
    for line in lines {
        assert!(!complete,"records after completion marker");
        match serde_json::from_str::<Record>(&line.expect("world stream read")).expect("record JSON") {
            Record::Section{chunk,y,bits,palette,data,nonair}=>{
                register_section(chunk,y,&mut seen);
                let section=native_section(bits,palette,data,nonair);
                world.ensure_chunk(chunk[0],chunk[1]).sections[(y+4) as usize]=section;
                nonair_total+=nonair as u64;
            },
            Record::UniformSection{chunk,y,state,nonair}=>{
                assert!(uniform_encoding,"uniform sections require the v2 native stream schema");
                register_section(chunk,y,&mut seen);
                let section=uniform_native_section(state,nonair);
                world.ensure_chunk(chunk[0],chunk[1]).sections[(y+4) as usize]=section;
                nonair_total+=nonair as u64;
                uniform_sections+=1;
            },
            Record::Comparator{pos,output_strength}=>{
                assert!(output_strength<=15);
                let p=native_pos(pos);
                assert!(matches!(world.get_block(p),Block::Comparator(_)),"entity without native comparator");
                assert!(world.get_block_entity(p).is_none(),"duplicate block entity");
                world.set_block_entity(p,BlockEntity::Comparator{output_strength});
                comparator_count+=1;
            },
            Record::Complete{chunks,sections,nonair,comparators}=>{
                assert_eq!(world.chunks.len(),chunks);
                assert_eq!(seen.len(),sections);
                assert!(chunks>0,"empty source is not a runtime fixture");
                assert_eq!(sections,chunks*SECTIONS,"every native chunk must include all 24 height sections");
                assert_eq!(nonair_total,nonair);
                assert_eq!(comparator_count,comparators);
                complete=true;
            },
            Record::Header{..}=>panic!("duplicate header"),
        }
    }
    assert!(complete,"truncated import: missing completion marker");
    let imported_stream_sha256=finish_hash(world_hasher);
    let (scenario_reader,scenario_hasher)=hashing_reader(&args[2]);
    let scenario:Scenario=serde_json::from_reader(scenario_reader).expect("scenario JSON");
    let scenario_sha256=finish_hash(scenario_hasher);
    let assertions=validate_scenario(&world,&scenario);
    let trace_points=trace_points(&scenario);
    let loaded=Instant::now();
    let loaded_chunks=world.chunks.len();
    let palette_data_bytes:usize=world.chunks.values().flat_map(|c|&c.sections)
        .map(|s|s.data().len()*8+s.palette().len()*4).sum();
    let mut sections:Vec<_>=seen.into_iter().collect();
    sections.sort_unstable();
    // Initialize every non-air block actually decoded from the saved world;
    // no chunk is unloaded or paused, regardless of console/player distance.
    let mut initialized=0u64;
    for (cx,sy,cz) in sections {
        if world.get_chunk(cx,cz).unwrap().sections[(sy+4) as usize].block_count()==0 {continue;}
        for index in 0..4096 {
            let p=BlockPos::new(cx*16+(index&15), (sy+4)*16+(index>>8),cz*16+((index>>4)&15));
            if world.get_block_raw(p)!=0 {
                mchprs_redstone::update(world.get_block(p),&mut world,p);
                mchprs_redstone::update_surrounding_blocks(&mut world,p);
                initialized+=1;
            }
        }
    }
    assert_eq!(initialized,nonair_total);
    let initialized_at=Instant::now();
    for _ in 0..scenario.initial_ticks {world.tick();}
    let mut ticks=scenario.initial_ticks;
    let mut traces:HashMap<String,Vec<bool>>=trace_points.iter().map(|(name,_)|((*name).to_string(),Vec::new())).collect();
    for (index,step) in scenario.steps.iter().enumerate() {
        for (name,value) in &step.drive {
            let p=native_pos(*scenario.inputs.get(name).expect("unknown input name")).offset(BlockFace::South);
            assert!(matches!(world.get_block(p),Block::Air|Block::RedstoneBlock), "external drive pad acquired an unexpected block");
            world.set_block(p,if *value {Block::RedstoneBlock} else {Block::Air});
            mchprs_redstone::update_surrounding_blocks(&mut world,p);
        }
        for p in &step.use_blocks {
            let pos=native_pos(*p);
            assert!(mchprs_redstone::on_use(world.get_block(pos),&mut world,pos), "saved actuator did not accept use_blocks action");
        }
        for offset in 0..step.ticks {
            world.tick();ticks+=1;
            if scenario.capture_trace {
                // Read only selected trace coordinates. Full memory-bit
                // assertions below still use the complete scenario output map.
                for (name,p) in &trace_points {traces.get_mut(*name).unwrap().push(powered(&world,*p));}
            }
            if step.continuous {verify(&world,&scenario,&step.expect,index,offset);}
        }
        verify(&world,&scenario,&step.expect,index,step.ticks);
    }
    let execution_seconds = initialized_at.elapsed().as_secs_f64();
    let snapshot_report = snapshot_path.map(|path| snapshot::save(&world, path, surface_y, &source,
        &imported_stream_sha256, &scenario_sha256, ticks));
    let mut report=serde_json::json!({
        "kind":"streamed-native-anvil-sparse-mchprs-runtime","status":if assertions>0 {"passed"} else {"executed-trace-only"},
        "assertions":assertions,"imported_stream_sha256":imported_stream_sha256,"scenario_sha256":scenario_sha256,
        "engine_revision":"f9e10d2572116bf5dcb9a36a305f28e40641a61a",
        "engine_registry":if cfg!(feature="terrain_registry") {"pinned registry plus explicit native bedrock/dirt/grass states"} else {"unmodified pinned registry"},
        "engine_registry_patch_sha256":option_env!("SHAMA_TERRAIN_PATCH_SHA256"),
        "source":source,"complete_machine_runtime_verified":false,
        "loading":"every decoded chunk remains loaded and every scheduled tick is processed; no player radius or unloading",
        "native_to_runtime_translation_y":Y_OFFSET,"loaded_chunks":loaded_chunks,
        "uniform_sections":uniform_sections,
        "chunks_after_drives":world.chunks.len(),"decoded_nonair_blocks":nonair_total,
        "initialized_nonair_blocks":initialized,"paletted_storage_bytes_before_updates":palette_data_bytes,
        "comparator_entities":comparator_count,"steps":scenario.steps.len(),"ticks":ticks,
        "scheduled_ticks_total":world.scheduled_total,"peak_pending_ticks":world.pending_peak,
        "pending_ticks_after_scenario":world.pending.len(),"snapshot":snapshot_report,
        "import_seconds":loaded.duration_since(started).as_secs_f64(),
        "initialization_seconds":initialized_at.duration_since(loaded).as_secs_f64(),
        "runtime_seconds":execution_seconds,
        "peak_rss_kib":null,
        "memory_sample_scope":"process high-water mark after report assembly, before stdout serialization; external process measurement may be higher",
        "trace_capture":{
            "mode":if !scenario.capture_trace {"disabled"} else if scenario.trace_outputs.is_some() {"selected"} else {"all"},
            "declared_outputs":scenario.outputs.len(),
            "traced_outputs":if scenario.capture_trace {trace_points.len()} else {0},
            "samples_per_output":if scenario.capture_trace {ticks-scenario.initial_ticks} else {0}
        },
        "traces":traces
    });
    report["peak_rss_kib"]=serde_json::json!(rss_kib("VmHWM:"));
    println!("{}",report);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uniform_sections_expand_to_every_exact_native_state() {
        for block in [Block::Air,Block::Stone,Block::RedstoneLamp{lit:true}] {
            let state=State{name:block.get_name().to_string(),properties:block.properties().into_iter().map(|(k,v)|(k.to_string(),v)).collect()};
            let nonair=if block==Block::Air {0} else {4096};
            let section=uniform_native_section(state,nonair);
            for index in 0..4096 {
                assert_eq!(section.get_block(index&15,index>>8,(index>>4)&15),block.get_id());
            }
            assert_eq!(section.block_count(),nonair);
        }
    }

    #[test]
    fn wide_native_palette_preserves_every_state_when_engine_uses_direct_ids() {
        let ids:Vec<u32>=(0..32768).filter(|id|Block::from_id(*id).get_id()==*id).take(300).collect();
        assert_eq!(ids.len(),300);
        for count in [15usize,257,300] {
            let bits=if count<=16 {4} else {9};
            let per_long=64/bits;
            let mut data=vec![0u64;4096usize.div_ceil(per_long)];
            let mut expected=Vec::new();
            for index in 0..4096 {
                let value=(index*137+11)%count;
                data[index/per_long]|=(value as u64)<<((index%per_long)*bits);
                expected.push(ids[value]);
            }
            let palette=ids[..count].iter().map(|id| {
                let block=Block::from_id(*id);
                State{name:block.get_name().to_string(),properties:block.properties().into_iter().map(|(k,v)|(k.to_string(),v)).collect()}
            }).collect();
            let nonair=expected.iter().filter(|id|**id!=0).count() as u32;
            let section=native_section(bits as u8,palette,data,nonair);
            for (index,id) in expected.into_iter().enumerate() {
                assert_eq!(section.get_block((index&15) as u32,(index>>8) as u32,((index>>4)&15) as u32),id);
            }
        }
    }
}

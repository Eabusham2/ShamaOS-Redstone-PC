//! Explicit quiescent persistence experiment, separate from the frozen runner.
use super::{Block, BlockEntity, BlockPos, ChildStdin, Command, SparseWorld, Stdio, Y_OFFSET, finish_hash, native_pos};
use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Write};
use std::path::Path;
use std::time::Instant;

struct HashedWriter {
    destination: BufWriter<File>,
    hash_input: ChildStdin,
}

impl Write for HashedWriter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.destination.write_all(bytes)?;
        self.hash_input.write_all(bytes)?;
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.destination.flush()?;
        self.hash_input.flush()
    }
}

fn emit(writer: &mut HashedWriter, value: serde_json::Value) {
    serde_json::to_writer(&mut *writer, &value).expect("snapshot JSON write");
    writer.write_all(b"\n").expect("snapshot newline write");
}

fn state(id: u32) -> serde_json::Value {
    let block = Block::from_id(id);
    assert_eq!(block.get_id(), id, "snapshot encountered an unsupported engine block ID");
    serde_json::json!({"name": block.get_name(), "properties": block.properties()})
}

pub(super) fn save(world: &SparseWorld, destination: &str, surface_y: Option<i32>,
    source: &serde_json::Value, imported_sha256: &str, scenario_sha256: &str,
    ticks: usize) -> serde_json::Value
{
    // Never invent scheduled tick semantics or silently wait for a different
    // state. The caller must reach a quiescent boundary with the actual engine.
    assert!(world.pending.is_empty(), "snapshot requires zero pending scheduled ticks; refusing to discard active circuit work");
    let started = Instant::now();
    let path = Path::new(destination);
    assert!(!path.exists(), "snapshot destination already exists; refusing to overwrite it");
    let temporary = path.with_extension("jsonl.partial");
    let file = OpenOptions::new().write(true).create_new(true).open(&temporary)
        .expect("create new incomplete snapshot");
    let mut hasher = Command::new("sha256sum").stdin(Stdio::piped()).stdout(Stdio::piped())
        .spawn().expect("snapshot sha256sum");
    let mut writer = HashedWriter { destination: BufWriter::new(file), hash_input: hasher.stdin.take().unwrap() };
    emit(&mut writer, serde_json::json!({
        "kind": "snapshot_header", "schema": "shama-mchprs-quiescent-snapshot-v1",
        "surface_y": surface_y, "source": source,
        "imported_stream_sha256": imported_sha256, "scenario_sha256": scenario_sha256,
        "engine_revision": "f9e10d2572116bf5dcb9a36a305f28e40641a61a",
        "engine_registry_patch_sha256": option_env!("SHAMA_TERRAIN_PATCH_SHA256"),
        "pending_scheduled_ticks": 0, "executed_redstone_ticks": ticks,
        "scheduled_ticks_total": world.scheduled_total,
        "complete_machine_runtime_verified": false
    }));
    let mut coordinates: Vec<_> = world.chunks.keys().copied().collect();
    coordinates.sort_unstable();
    let mut nonair_total = 0u64;
    let mut comparators = 0usize;
    let mut sections = 0usize;
    let mut uniforms = 0usize;
    for &(cx, cz) in &coordinates {
        native_pos([cx * 16, -64, cz * 16]);
        native_pos([cx * 16 + 15, 319, cz * 16 + 15]);
        let chunk = &world.chunks[&(cx, cz)];
        assert_eq!(chunk.section_count(), 24, "snapshot requires every native height section");
        let mut entities = Vec::new();
        for sy in 0..chunk.section_count() {
            let section=chunk.section(sy);
            let mut palette = Vec::new();
            let mut palette_indices = HashMap::new();
            let mut indices = Vec::with_capacity(4096);
            let mut nonair = 0u32;
            if section.block_count() == 0 {
                palette.push(0);
            } else {
                for index in 0..4096u32 {
                    // data()/palette() alone exclude the actual mutation
                    // overlay. get_block() includes every scheduled update.
                    let id = section.get_block(index & 15, index >> 8, (index >> 4) & 15);
                    let next = palette.len();
                    let value = *palette_indices.entry(id).or_insert_with(|| { palette.push(id); next });
                    indices.push(value);
                    nonair += u32::from(id != 0);
                    if matches!(Block::from_id(id), Block::Comparator(_)) {
                        let local = BlockPos::new((index & 15) as i32, (sy * 16 + (index >> 8) as usize) as i32,
                            ((index >> 4) & 15) as i32);
                        let strength = match chunk.get_block_entity(local) {
                            Some(BlockEntity::Comparator { output_strength }) => *output_strength,
                            _ => panic!("snapshot comparator is missing its actual block entity"),
                        };
                        assert!(strength <= 15, "snapshot comparator output is outside native bounds");
                        entities.push(serde_json::json!({"kind": "comparator",
                            "pos": [cx * 16 + local.x, local.y - Y_OFFSET, cz * 16 + local.z],
                            "output_strength": strength}));
                    }
                }
            }
            assert_eq!(nonair, section.block_count(), "snapshot non-air metadata disagrees with actual mutated blocks");
            let native_y = sy as i32 - 4;
            if palette.len() == 1 {
                emit(&mut writer, serde_json::json!({"kind": "uniform_section", "chunk": [cx,cz],
                    "y": native_y, "state": state(palette[0]), "nonair": nonair}));
                uniforms += 1;
            } else {
                let bits = 4usize.max((usize::BITS - (palette.len() - 1).leading_zeros()) as usize);
                let per_word = 64 / bits;
                let mut data = vec![0u64; 4096usize.div_ceil(per_word)];
                for (index, value) in indices.into_iter().enumerate() {
                    data[index / per_word] |= (value as u64) << ((index % per_word) * bits);
                }
                let native_palette: Vec<_> = palette.into_iter().map(state).collect();
                emit(&mut writer, serde_json::json!({"kind": "section", "chunk": [cx,cz], "y": native_y,
                    "bits": bits, "palette": native_palette, "data": data, "nonair": nonair}));
            }
            sections += 1;
            nonair_total += nonair as u64;
        }
        assert_eq!(entities.len(), chunk.entity_count(), "snapshot has an unsupported or orphaned block entity");
        comparators += entities.len();
        for entity in entities { emit(&mut writer, entity); }
    }
    emit(&mut writer, serde_json::json!({"kind": "snapshot_complete", "chunks": coordinates.len(),
        "sections": sections, "nonair": nonair_total, "comparators": comparators,
        "pending_scheduled_ticks": 0}));
    writer.flush().expect("flush complete snapshot");
    writer.destination.get_ref().sync_all().expect("sync snapshot file");
    drop(writer);
    let sha256 = finish_hash(hasher);
    // link is atomic and fails if another file appeared at the destination;
    // neither the input world nor an existing snapshot can be overwritten.
    std::fs::hard_link(&temporary, path).expect("publish snapshot without overwriting an existing file");
    std::fs::remove_file(&temporary).expect("remove published snapshot temporary link");
    serde_json::json!({"status": "quiescent-snapshot-written", "path": destination,
        "sha256": sha256, "bytes": path.metadata().unwrap().len(), "chunks": coordinates.len(),
        "sections": sections, "uniform_sections": uniforms, "nonair": nonair_total,
        "comparators": comparators, "pending_scheduled_ticks": 0,
        "seconds": started.elapsed().as_secs_f64(), "complete_machine_runtime_verified": false})
}

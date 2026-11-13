//! Exact storage only: the pinned MCHPRS redstone implementation is unchanged.
//!
//! Every native section remains present. Uniform sections store their one state
//! ID until the first differing write. Mixed sections and all writes use the
//! upstream ChunkSection implementation. A full Chunk requested through World
//! becomes authoritative, including later block and block-entity mutations.
use mchprs_blocks::block_entities::BlockEntity;
use mchprs_blocks::BlockPos;
use mchprs_world::storage::{Chunk, ChunkSection};
use std::cell::OnceCell;
use std::collections::HashMap;

#[derive(Clone)]
pub enum StoredSection {
    Uniform(u32),
    Native {
        section: Box<ChunkSection>,
        // A section expanded from one uniform value can return to that value
        // without scanning. This tracks exact differences, not a state guess.
        uniform_origin: Option<(u32, u32)>,
    },
}

impl StoredSection {
    pub fn uniform(id: u32) -> Self {
        assert!(id < 32768, "state outside pinned engine ID range");
        Self::Uniform(id)
    }

    pub fn native(section: ChunkSection) -> Self {
        Self::Native { section: Box::new(section), uniform_origin: None }
    }

    fn expanded(id: u32) -> ChunkSection {
        ChunkSection::from_raw(vec![0; 256], 4, vec![id], if id == 0 { 0 } else { 4096 })
    }

    pub fn as_native(&self) -> ChunkSection {
        match self {
            Self::Uniform(id) => Self::expanded(*id),
            Self::Native { section, .. } => (**section).clone(),
        }
    }

    pub fn get_block(&self, x: u32, y: u32, z: u32) -> u32 {
        assert!(x < 16 && y < 16 && z < 16, "section coordinate outside native bounds");
        match self {
            Self::Uniform(id) => *id,
            Self::Native { section, .. } => section.get_block(x, y, z),
        }
    }

    pub fn set_block(&mut self, x: u32, y: u32, z: u32, id: u32) -> bool {
        assert!(id < 32768, "state outside pinned engine ID range");
        let old = self.get_block(x, y, z);
        if old == id { return false; }
        if let Self::Uniform(original) = self {
            *self = Self::Native {
                section: Box::new(Self::expanded(*original)),
                uniform_origin: Some((*original, 0)),
            };
        }
        let mut collapse = None;
        if let Self::Native { section, uniform_origin } = self {
            assert!(section.set_block(x, y, z, id));
            if let Some((original, differing)) = uniform_origin {
                if old != *original { *differing -= 1; }
                if id != *original { *differing += 1; }
                if *differing == 0 { collapse = Some(*original); }
            }
            if section.block_count() == 0 { collapse = Some(0); }
        }
        if let Some(id) = collapse { *self = Self::Uniform(id); }
        true
    }

    pub fn block_count(&self) -> u32 {
        match self {
            Self::Uniform(id) => if *id == 0 { 0 } else { 4096 },
            Self::Native { section, .. } => section.block_count(),
        }
    }

    pub fn palette_data_bytes(&self) -> usize {
        match self {
            Self::Uniform(_) => 0,
            Self::Native { section, .. } => section.data().len() * 8 + section.palette().len() * 4,
        }
    }
}

pub enum SectionRef<'a> {
    Compact(&'a StoredSection),
    Native(&'a ChunkSection),
}

impl SectionRef<'_> {
    pub fn get_block(&self, x: u32, y: u32, z: u32) -> u32 {
        match self {
            Self::Compact(section) => section.get_block(x, y, z),
            Self::Native(section) => section.get_block(x, y, z),
        }
    }

    pub fn block_count(&self) -> u32 {
        match self {
            Self::Compact(section) => section.block_count(),
            Self::Native(section) => section.block_count(),
        }
    }

    pub fn palette_data_bytes(&self) -> usize {
        match self {
            Self::Compact(section) => section.palette_data_bytes(),
            Self::Native(section) => section.data().len() * 8 + section.palette().len() * 4,
        }
    }
}

#[derive(Clone)]
pub struct StoredChunk {
    pub x: i32,
    pub z: i32,
    sections: Vec<StoredSection>,
    entities: HashMap<BlockPos, BlockEntity>,
    native: OnceCell<Chunk>,
}

impl StoredChunk {
    pub fn empty(x: i32, z: i32, count: usize) -> Self {
        Self { x, z, sections: vec![StoredSection::uniform(0); count],
            entities: HashMap::new(), native: OnceCell::new() }
    }

    pub fn clone_at(&self, x: i32, z: i32) -> Self {
        let mut result = self.clone();
        result.x = x;
        result.z = z;
        if let Some(chunk) = result.native.get_mut() {
            chunk.x = x;
            chunk.z = z;
            // Once native exists, the compact copy has no authority.
            result.sections.clear();
            result.entities.clear();
        }
        result
    }

    pub fn section_count(&self) -> usize {
        self.native.get().map_or_else(|| self.sections.len(), |chunk| chunk.sections.len())
    }

    pub fn section(&self, index: usize) -> SectionRef<'_> {
        match self.native.get() {
            Some(chunk) => SectionRef::Native(&chunk.sections[index]),
            None => SectionRef::Compact(&self.sections[index]),
        }
    }

    pub fn replace_section(&mut self, index: usize, section: StoredSection) {
        if self.native.get().is_some() {
            self.native_mut().sections[index] = section.as_native();
        } else {
            self.sections[index] = section;
        }
    }

    pub fn get_block(&self, x: u32, y: u32, z: u32) -> u32 {
        let index = (y / 16) as usize;
        if index >= self.section_count() { return 0; }
        self.section(index).get_block(x, y & 15, z)
    }

    pub fn set_block(&mut self, x: u32, y: u32, z: u32, id: u32) -> bool {
        if self.native.get().is_some() {
            self.native_mut().set_block(x, y, z, id)
        } else {
            self.sections[(y / 16) as usize].set_block(x, y & 15, z, id)
        }
    }

    pub fn get_block_entity(&self, pos: BlockPos) -> Option<&BlockEntity> {
        match self.native.get() {
            Some(chunk) => chunk.get_block_entity(pos),
            None => self.entities.get(&pos),
        }
    }

    pub fn set_block_entity(&mut self, pos: BlockPos, entity: BlockEntity) {
        if self.native.get().is_some() {
            self.native_mut().set_block_entity(pos, entity);
        } else {
            self.entities.insert(pos, entity);
        }
    }

    pub fn delete_block_entity(&mut self, pos: BlockPos) {
        if self.native.get().is_some() {
            self.native_mut().delete_block_entity(pos);
        } else {
            self.entities.remove(&pos);
        }
    }

    pub fn entity_count(&self) -> usize {
        self.native.get().map_or_else(|| self.entities.len(), |chunk| chunk.block_entities.len())
    }

    pub fn native(&self) -> &Chunk {
        self.native.get_or_init(|| {
            // World requires an actual upstream Chunk reference. Redstone uses
            // the block/entity interface, so only explicit chunk consumers pay
            // for this compatibility materialization. Every later read uses it.
            let mut chunk = Chunk::empty(self.x, self.z, 0);
            chunk.sections = self.sections.iter().map(StoredSection::as_native).collect();
            for (&pos, entity) in &self.entities {
                chunk.set_block_entity(pos, entity.clone());
            }
            chunk
        })
    }

    pub fn native_mut(&mut self) -> &mut Chunk {
        self.native();
        // Immutable materialization cannot remove compact fields while World
        // holds &self. Discard that inactive input copy on first mutable access;
        // it is never consulted after native() has returned a chunk.
        self.sections.clear();
        self.entities.clear();
        self.native.get_mut().unwrap()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use mchprs_blocks::blocks::Block;

    fn assert_same(section: &StoredSection, native: &ChunkSection) {
        assert_eq!(section.block_count(), native.block_count());
        for index in 0..4096 {
            assert_eq!(section.get_block(index & 15, index >> 8, (index >> 4) & 15),
                native.get_block(index & 15, index >> 8, (index >> 4) & 15));
        }
    }

    #[test]
    fn uniform_expands_and_collapses_without_losing_any_position() {
        for id in [0, Block::Stone.get_id(), Block::RedstoneLamp { lit: true }.get_id()] {
            let mut section = StoredSection::uniform(id);
            let mut native = StoredSection::expanded(id);
            let replacement = if id == 0 { Block::Stone.get_id() } else { 0 };
            assert!(!section.set_block(0, 0, 0, id));
            for index in [0, 16, 255, 256, 2048, 4095] {
                let (x, y, z) = (index & 15, index >> 8, (index >> 4) & 15);
                assert_eq!(section.set_block(x, y, z, replacement), native.set_block(x, y, z, replacement));
                assert_same(&section, &native);
            }
            for index in [4095, 16, 0, 2048, 255, 256] {
                let (x, y, z) = (index & 15, index >> 8, (index >> 4) & 15);
                assert_eq!(section.set_block(x, y, z, id), native.set_block(x, y, z, id));
                assert_same(&section, &native);
            }
            assert!(matches!(section, StoredSection::Uniform(value) if value == id));
            assert_eq!(section.palette_data_bytes(), 0);
        }
    }

    #[test]
    fn every_uniform_solid_can_be_cleared_and_remains_exact_air() {
        let mut section = StoredSection::uniform(Block::Stone.get_id());
        for index in 0..4096 {
            assert!(section.set_block(index & 15, index >> 8, (index >> 4) & 15, 0));
        }
        assert!(matches!(section, StoredSection::Uniform(0)));
        assert_eq!(section.block_count(), 0);
    }

    #[test]
    fn materialized_chunk_is_authoritative_for_all_later_access() {
        let mut chunk = StoredChunk::empty(-3, 7, 24);
        let pos = BlockPos::new(15, 31, 1);
        chunk.replace_section(1, StoredSection::uniform(Block::Stone.get_id()));
        chunk.set_block_entity(pos, BlockEntity::Comparator { output_strength: 3 });
        assert_eq!(chunk.native().sections.len(), 24);
        assert_eq!(chunk.native().get_block(15, 31, 1), Block::Stone.get_id());
        chunk.native_mut().set_block(15, 31, 1, Block::RedstoneLamp { lit: true }.get_id());
        chunk.native_mut().set_block_entity(pos, BlockEntity::Comparator { output_strength: 13 });
        assert_eq!(chunk.get_block(15, 31, 1), Block::RedstoneLamp { lit: true }.get_id());
        assert!(matches!(chunk.get_block_entity(pos), Some(BlockEntity::Comparator { output_strength: 13 })));
        assert!(chunk.set_block(15, 31, 1, 0));
        assert_eq!(chunk.native().get_block(15, 31, 1), 0);
        chunk.set_block_entity(pos, BlockEntity::Comparator { output_strength: 8 });
        assert!(matches!(chunk.native().get_block_entity(pos), Some(BlockEntity::Comparator { output_strength: 8 })));
        chunk.delete_block_entity(pos);
        assert_eq!(chunk.native().block_entities.len(), 0);
        assert_eq!(chunk.entity_count(), 0);
        assert!(chunk.sections.is_empty() && chunk.entities.is_empty());
        let copied = chunk.clone_at(12, -9);
        assert_eq!((copied.native().x, copied.native().z), (12, -9));
        assert_eq!(copied.get_block(15, 31, 1), 0);
    }
}

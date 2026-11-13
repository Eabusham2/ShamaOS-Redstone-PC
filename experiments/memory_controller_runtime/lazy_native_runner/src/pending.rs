//! Same tick ordering as the pinned Vec scheduler, with constant-time removal
//! and position membership. This does not change redstone scheduling policy.
use mchprs_blocks::BlockPos;
use mchprs_world::TickEntry;
use std::collections::{HashMap, VecDeque};

#[derive(Default)]
pub(crate) struct PendingTicks {
    entries: VecDeque<TickEntry>,
    positions: HashMap<BlockPos, usize>,
}

impl PendingTicks {
    pub(crate) fn len(&self) -> usize { self.entries.len() }
    pub(crate) fn is_empty(&self) -> bool { self.entries.is_empty() }
    pub(crate) fn contains(&self, position: BlockPos) -> bool { self.positions.contains_key(&position) }

    pub(crate) fn push(&mut self, entry: TickEntry) {
        *self.positions.entry(entry.pos).or_insert(0) += 1;
        self.entries.push_back(entry);
    }

    pub(crate) fn begin_tick(&mut self) {
        // Sorting is stable and happens before decrement, exactly once per
        // world tick. Callbacks append at the back, including zero-delay work.
        self.entries.make_contiguous().sort_by_key(|entry| (entry.ticks_left, entry.tick_priority));
        for entry in &mut self.entries {
            entry.ticks_left = entry.ticks_left.saturating_sub(1);
        }
    }

    pub(crate) fn pop_ready(&mut self) -> Option<TickEntry> {
        if !self.entries.front().is_some_and(|entry| entry.ticks_left == 0) {
            return None;
        }
        let entry = self.entries.pop_front().expect("checked pending front");
        let count = self.positions.get_mut(&entry.pos).expect("queued position has a pending count");
        *count -= 1;
        if *count == 0 { self.positions.remove(&entry.pos); }
        // Remove membership before the engine callback so it observes exactly
        // the entries still pending at this position, including duplicates.
        Some(entry)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use mchprs_world::TickPriority;

    fn event(position: i32, delay: u32, priority: TickPriority) -> TickEntry {
        TickEntry { pos: BlockPos::new(position, 4, -position), ticks_left: delay, tick_priority: priority }
    }

    fn begin_reference(entries: &mut [TickEntry]) {
        entries.sort_by_key(|entry| (entry.ticks_left, entry.tick_priority));
        for entry in entries { entry.ticks_left = entry.ticks_left.saturating_sub(1); }
    }

    fn check(queue: &PendingTicks, reference: &[TickEntry]) {
        assert_eq!(queue.len(), reference.len());
        assert_eq!(queue.is_empty(), reference.is_empty());
        assert_eq!(queue.entries.iter().cloned().collect::<Vec<_>>(), reference);
        assert_eq!(queue.positions.values().sum::<usize>(), reference.len());
        for position in -1..=32 {
            let point = BlockPos::new(position, 4, -position);
            assert_eq!(queue.contains(point), reference.iter().any(|entry| entry.pos == point));
        }
    }

    #[test]
    fn duplicates_remain_pending_until_the_last_event_is_removed() {
        let mut queue = PendingTicks::default();
        let first = event(3, 1, TickPriority::Normal);
        let second = event(3, 2, TickPriority::Highest);
        queue.push(first.clone());
        queue.push(second);
        queue.begin_tick();
        assert_eq!(queue.pop_ready().unwrap().pos, first.pos);
        assert!(queue.contains(first.pos));
        assert!(queue.pop_ready().is_none());
        queue.begin_tick();
        assert_eq!(queue.pop_ready().unwrap().pos, first.pos);
        assert!(!queue.contains(first.pos));
        assert!(queue.is_empty());
    }

    #[test]
    fn callback_zero_delay_keeps_the_original_append_and_blocking_rules() {
        let mut queue = PendingTicks::default();
        queue.push(event(1, 1, TickPriority::Normal));
        queue.push(event(2, 4, TickPriority::Normal));
        queue.begin_tick();
        assert_eq!(queue.pop_ready().unwrap().pos.x, 1);
        queue.push(event(3, 0, TickPriority::Highest));
        // New zero-delay work stays behind the existing non-due entry until
        // the next tick's sort. A heap that runs it immediately is different.
        assert!(queue.pop_ready().is_none());
        queue.begin_tick();
        assert_eq!(queue.pop_ready().unwrap().pos.x, 3);
        assert!(queue.pop_ready().is_none());
        queue.begin_tick();
        queue.begin_tick();
        assert_eq!(queue.pop_ready().unwrap().pos.x, 2);
        queue.push(event(4, 0, TickPriority::Normal));
        queue.push(event(5, 0, TickPriority::Highest));
        assert_eq!(queue.pop_ready().unwrap().pos.x, 4);
        assert_eq!(queue.pop_ready().unwrap().pos.x, 5);
        assert!(queue.is_empty());
    }

    #[test]
    fn predecrement_priority_and_ties_survive_wrapped_deque_storage() {
        let mut queue = PendingTicks::default();
        // Advance the deque head, then populate enough entries to wrap it.
        for position in 0..32 { queue.push(event(position, 0, TickPriority::Normal)); }
        for _ in 0..25 { queue.pop_ready().unwrap(); }
        let mut reference: Vec<_> = queue.entries.iter().cloned().collect();
        for position in 0..24 {
            let entry = event(position, (position % 3) as u32, match position % 4 {
                0 => TickPriority::Highest, 1 => TickPriority::Higher,
                2 => TickPriority::High, _ => TickPriority::Normal,
            });
            reference.push(entry.clone()); queue.push(entry);
        }
        assert!(!queue.entries.as_slices().0.is_empty());
        assert!(!queue.entries.as_slices().1.is_empty(), "this case must exercise wrapped storage");
        for _ in 0..4 {
            begin_reference(&mut reference); queue.begin_tick(); check(&queue, &reference);
            while reference.first().is_some_and(|entry| entry.ticks_left == 0) {
                assert_eq!(queue.pop_ready().unwrap(), reference.remove(0));
                check(&queue, &reference);
            }
            assert!(queue.pop_ready().is_none());
        }
        assert!(queue.is_empty());
    }

    #[test]
    fn zero_and_one_delays_keep_their_predecrement_order_before_callback_appends() {
        let mut queue = PendingTicks::default();
        queue.push(event(1, 1, TickPriority::Highest));
        queue.push(event(0, 0, TickPriority::Normal));
        queue.begin_tick();
        // Both become ready, but the delay-zero event was ordered first before
        // decrement. Re-sorting ready work by priority would reverse them.
        assert_eq!(queue.pop_ready().unwrap().pos.x, 0);
        assert!(!queue.contains(event(0, 0, TickPriority::Normal).pos));
        queue.push(event(0, 0, TickPriority::Highest));
        assert!(queue.contains(event(0, 0, TickPriority::Normal).pos));
        assert_eq!(queue.pop_ready().unwrap().pos.x, 1);
        assert_eq!(queue.pop_ready().unwrap().pos.x, 0);
        assert!(queue.is_empty());
        assert!(queue.positions.is_empty());
    }

    #[test]
    fn membership_uses_every_coordinate_and_preserves_maximum_delay() {
        let mut queue = PendingTicks::default();
        let base = BlockPos::new(-17, 64, -17);
        let higher = BlockPos::new(-17, 65, -17);
        let adjacent = BlockPos::new(-17, 64, -16);
        let last = BlockPos::new(0, 383, 0);
        for (pos, ticks_left) in [(base, 0), (higher, 1), (adjacent, 2), (base, 3), (last, u32::MAX)] {
            queue.push(TickEntry { pos, ticks_left, tick_priority: TickPriority::Normal });
        }
        assert!(!queue.contains(BlockPos::new(-16, 64, -17)));
        queue.begin_tick();
        assert_eq!(queue.pop_ready().unwrap().pos, base);
        assert!(queue.contains(base), "the duplicate at this exact position remains pending");
        assert_eq!(queue.pop_ready().unwrap().pos, higher);
        assert!(!queue.contains(higher));
        assert!(queue.contains(adjacent));
        assert!(queue.contains(last));
        assert!(queue.pop_ready().is_none());
        queue.begin_tick();
        assert_eq!(queue.pop_ready().unwrap().pos, adjacent);
        assert!(!queue.contains(adjacent));
        assert!(queue.contains(base));
        queue.begin_tick();
        assert_eq!(queue.pop_ready().unwrap().pos, base);
        assert!(!queue.contains(base));
        assert!(queue.pop_ready().is_none());
        assert_eq!(queue.len(), 1);
        assert_eq!(queue.entries.front().unwrap().ticks_left, u32::MAX - 3);
        assert_eq!(queue.positions.len(), 1);
        assert_eq!(queue.positions[&last], 1);
    }

    #[test]
    fn mixed_scheduled_callbacks_match_the_original_vector_at_every_step() {
        let mut queue = PendingTicks::default();
        let mut reference = Vec::<TickEntry>::new();
        let mut seed = 0x35a15ce7_u64;
        let mut random = || {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            (seed >> 32) as u32
        };
        for _ in 0..400 {
            for _ in 0..(random() % 7 + 1) {
                let value = random();
                let entry = event((value % 32) as i32, (value >> 5) % 9, match (value >> 10) % 4 {
                    0 => TickPriority::Highest, 1 => TickPriority::Higher,
                    2 => TickPriority::High, _ => TickPriority::Normal,
                });
                reference.push(entry.clone()); queue.push(entry);
            }
            begin_reference(&mut reference); queue.begin_tick(); check(&queue, &reference);
            let mut callbacks = 0;
            while reference.first().is_some_and(|entry| entry.ticks_left == 0) {
                let expected = reference.remove(0);
                assert_eq!(queue.pop_ready().unwrap(), expected);
                check(&queue, &reference);
                let value = random();
                if value % 3 == 0 && callbacks < 100 {
                    let entry = event((value % 32) as i32, (value >> 7) % 5, TickPriority::Highest);
                    reference.push(entry.clone()); queue.push(entry); callbacks += 1;
                    check(&queue, &reference);
                }
            }
            assert!(queue.pop_ready().is_none());
        }
    }
}

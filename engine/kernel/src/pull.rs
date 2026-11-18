use std::collections::VecDeque;

use crate::{Solid, boundary::lateral_axes, valid_coordinate};

impl Solid {
    /// Moves a planar face along its outward normal, in millimetres.
    pub fn push_pull(&self, face_id: usize, distance: f64) -> Result<Self, &'static str> {
        let faces = self.faces();
        let face = faces.get(face_id).ok_or("Select a valid planar face.")?;
        if !valid_coordinate(distance) {
            return Err("Enter a finite distance within the modeling range.");
        }
        if distance == 0. {
            return Ok(self.clone());
        }

        let axis = face.axis;
        let (u, v) = lateral_axes(axis);
        let width = self.size()[u];
        let height = self.size()[v];
        let mut covered = vec![true; width * height];
        let mut selected = vec![false; width * height];
        let mut back = if face.positive { 0 } else { self.size()[axis] };
        for patch in &face.patches {
            selected[patch[u] * height + patch[v]] = true;
            let mut cell = *patch;
            cell[axis] -= usize::from(face.positive);
            let (start, end) = self.column(cell, axis);
            back = if face.positive {
                back.max(start)
            } else {
                back.min(end)
            };
        }
        // The enclosed cells are the footprints of bosses, recesses and holes.
        let mut queue = VecDeque::new();
        for x in 0..width {
            queue.extend([(x, 0), (x, height - 1)]);
        }
        for y in 0..height {
            queue.extend([(0, y), (width - 1, y)]);
        }
        while let Some((x, y)) = queue.pop_front() {
            if x >= width || y >= height {
                continue;
            }
            let i = x * height + y;
            if selected[i] || !std::mem::replace(&mut covered[i], false) {
                continue;
            }
            queue.extend([
                (x.wrapping_sub(1), y),
                (x + 1, y),
                (x, y.wrapping_sub(1)),
                (x, y + 1),
            ]);
        }

        let sign = if face.positive { 1. } else { -1. };
        let mut edits = Vec::new();
        let mut planes = self.planes.clone();
        for (index, covered) in covered.into_iter().enumerate() {
            if !covered {
                continue;
            }
            let mut cell = [0; 3];
            cell[u] = index / height;
            cell[v] = index % height;
            cell[axis] = back - usize::from(!face.positive);
            if !self.occupied(cell) {
                continue;
            }
            let (start, end) = self.column(cell, axis);
            let (front, opposite) = if face.positive {
                (end, start)
            } else {
                (start, end)
            };
            let old = self.planes[axis][front];
            let next = old + distance * sign;
            if !valid_coordinate(next) || (next - self.planes[axis][opposite]) * sign <= 0. {
                return Err("That push would go through this wall.");
            }
            if distance > 0. {
                let mut beyond = cell;
                let limit = self.size()[axis];
                for layer in if face.positive { end..limit } else { 0..start } {
                    beyond[axis] = layer;
                    let edge = self.planes[axis][layer + usize::from(!face.positive)];
                    if (next - edge) * sign >= 0. && self.occupied(beyond) {
                        return Err("That pull would collide with another part of this solid.");
                    }
                }
            }
            planes[axis].push(next);
            edits.push((cell, old, next));
        }

        let mut result = Self::empty(planes)?;
        result.cells = self.cells_on(&result);
        for (mut cell, old, next) in edits {
            let start = result.plane_index(axis, old.min(next));
            let end = result.plane_index(axis, old.max(next));
            for layer in start..end {
                cell[axis] = layer;
                let i = result.index(cell);
                result.cells[i] = distance > 0.;
            }
        }
        let result = result.compacted()?;
        if result.solid_count() > self.solid_count() {
            return Err("That push would split the solid.");
        }
        Ok(result)
    }

    fn column(&self, mut cell: [usize; 3], axis: usize) -> (usize, usize) {
        let mut start = cell[axis];
        let mut end = start + 1;
        while start > 0 {
            cell[axis] = start - 1;
            if !self.occupied(cell) {
                break;
            }
            start -= 1;
        }
        loop {
            cell[axis] = end;
            if !self.occupied(cell) {
                break;
            }
            end += 1;
        }
        (start, end)
    }
}

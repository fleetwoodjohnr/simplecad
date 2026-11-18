//! Exact coordinate-plane solids for the first native modeling operations.
//!
//! Occupied cells use the model's input planes; their size is independent of
//! display resolution. Boundary faces merge adjacent, coplanar cell sides.
mod boundary;
pub mod examples;
pub mod project;
mod pull;

use std::collections::VecDeque;

pub use boundary::{Face, Mesh};

const MAX_CELLS: usize = 1_000_000;
const MAX_COORDINATE: f64 = 1e9;

#[derive(Clone, Copy, Debug)]
pub struct Bounds {
    pub min: [f64; 3],
    pub max: [f64; 3],
}

#[derive(Clone, Copy)]
pub enum Boolean {
    Union,
    Subtract,
    Intersect,
}

#[derive(Clone, Debug)]
pub struct Solid {
    planes: [Vec<f64>; 3],
    cells: Vec<bool>,
}

impl Solid {
    pub fn cuboid(bounds: Bounds) -> Result<Self, &'static str> {
        for (min, max) in bounds.min.into_iter().zip(bounds.max) {
            if !valid_coordinate(min) || !valid_coordinate(max) || max <= min {
                return Err("A box needs three finite, positive extents.");
            }
        }
        Ok(Self {
            planes: std::array::from_fn(|axis| vec![bounds.min[axis], bounds.max[axis]]),
            cells: vec![true],
        })
    }

    fn empty(mut planes: [Vec<f64>; 3]) -> Result<Self, &'static str> {
        for axis in &mut planes {
            axis.sort_unstable_by(f64::total_cmp);
            axis.dedup();
        }
        let count = planes
            .iter()
            .try_fold(1usize, |count, axis| {
                count
                    .checked_mul(axis.len() - 1)
                    .filter(|&next| next <= MAX_CELLS)
            })
            .ok_or("This edit would create too many solid regions.")?;
        Ok(Self {
            planes,
            cells: vec![false; count],
        })
    }

    pub fn boolean(&self, other: &Self, operation: Boolean) -> Result<Self, &'static str> {
        let mut result = Self::empty(std::array::from_fn(|axis| {
            self.planes[axis]
                .iter()
                .chain(&other.planes[axis])
                .copied()
                .collect()
        }))?;
        let left = self.cells_on(&result);
        let right = other.cells_on(&result);
        for (cell, (left, right)) in result.cells.iter_mut().zip(left.into_iter().zip(right)) {
            *cell = match operation {
                Boolean::Union => left || right,
                Boolean::Subtract => left && !right,
                Boolean::Intersect => left && right,
            };
        }
        result.compacted()
    }

    pub fn contains(&self, point: [f64; 3]) -> bool {
        let cell = std::array::from_fn(|axis| {
            self.planes[axis]
                .partition_point(|&plane| plane <= point[axis])
                .wrapping_sub(1)
        });
        self.occupied(cell)
    }

    pub fn volume(&self) -> f64 {
        self.filled()
            .map(|cell| {
                (0..3)
                    .map(|axis| self.planes[axis][cell[axis] + 1] - self.planes[axis][cell[axis]])
                    .product::<f64>()
            })
            .sum()
    }

    pub fn solid_count(&self) -> usize {
        let mut remaining = self.cells.clone();
        let mut queue = VecDeque::new();
        let mut count = 0;
        let size = self.size();
        for seed in 0..remaining.len() {
            if !std::mem::replace(&mut remaining[seed], false) {
                continue;
            }
            count += 1;
            queue.push_back(self.coordinates(seed));
            while let Some(cell) = queue.pop_front() {
                for axis in 0..3 {
                    for neighbour in neighbours(cell, axis) {
                        if neighbour[axis] < size[axis]
                            && std::mem::replace(&mut remaining[self.index(neighbour)], false)
                        {
                            queue.push_back(neighbour);
                        }
                    }
                }
            }
        }
        count
    }

    fn size(&self) -> [usize; 3] {
        self.planes.each_ref().map(|axis| axis.len() - 1)
    }

    fn index(&self, [x, y, z]: [usize; 3]) -> usize {
        let [_, height, depth] = self.size();
        (x * height + y) * depth + z
    }

    fn coordinates(&self, index: usize) -> [usize; 3] {
        let [_, height, depth] = self.size();
        [
            index / (height * depth),
            (index / depth) % height,
            index % depth,
        ]
    }

    fn occupied(&self, cell: [usize; 3]) -> bool {
        cell.into_iter()
            .zip(self.size())
            .all(|(index, size)| index < size)
            && self.cells[self.index(cell)]
    }

    fn filled(&self) -> impl Iterator<Item = [usize; 3]> + '_ {
        self.cells
            .iter()
            .enumerate()
            .filter(|(_, full)| **full)
            .map(|(index, _)| self.coordinates(index))
    }

    fn cells_on(&self, target: &Self) -> Vec<bool> {
        let map: [Vec<usize>; 3] = std::array::from_fn(|axis| {
            target.planes[axis][..target.size()[axis]]
                .iter()
                .map(|&plane| {
                    self.planes[axis]
                        .partition_point(|&source| source <= plane)
                        .wrapping_sub(1)
                })
                .collect()
        });
        (0..target.cells.len())
            .map(|index| {
                let cell = target.coordinates(index);
                self.occupied(std::array::from_fn(|axis| map[axis][cell[axis]]))
            })
            .collect()
    }

    fn compacted(self) -> Result<Self, &'static str> {
        let mut keep = self.planes.each_ref().map(|axis| vec![false; axis.len()]);
        for cell in self.filled() {
            for (axis, flags) in keep.iter_mut().enumerate() {
                for (side, neighbour) in neighbours(cell, axis).into_iter().enumerate() {
                    if !self.occupied(neighbour) {
                        flags[cell[axis] + side] = true;
                    }
                }
            }
        }
        if !keep[0].contains(&true) {
            return Err("That operation would leave no solid.");
        }
        if keep.iter().all(|axis| axis.iter().all(|&needed| needed)) {
            self.validate()?;
            return Ok(self);
        }
        let mut result = Self::empty(std::array::from_fn(|axis| {
            self.planes[axis]
                .iter()
                .copied()
                .zip(&keep[axis])
                .filter_map(|(plane, &needed)| needed.then_some(plane))
                .collect()
        }))?;
        result.cells = self.cells_on(&result);
        result.validate()?;
        Ok(result)
    }

    fn plane_index(&self, axis: usize, value: f64) -> usize {
        self.planes[axis].partition_point(|&plane| plane < value)
    }
}

fn valid_coordinate(value: f64) -> bool {
    value.is_finite() && value.abs() <= MAX_COORDINATE
}

fn neighbours(cell: [usize; 3], axis: usize) -> [[usize; 3]; 2] {
    let mut before = cell;
    let mut after = cell;
    before[axis] = before[axis].wrapping_sub(1);
    after[axis] += 1;
    [before, after]
}

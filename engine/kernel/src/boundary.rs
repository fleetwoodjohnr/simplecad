use std::collections::{BTreeMap, BTreeSet};

use crate::{Solid, neighbours};

type Point = [usize; 3];
type Edge = (Point, Point);

#[derive(Debug)]
pub struct Face {
    pub axis: usize,
    pub positive: bool,
    pub plane: f64,
    pub center: [f64; 3],
    pub area: f64,
    pub(crate) patches: Vec<Point>,
}

#[derive(Default)]
pub struct Mesh {
    pub positions: Vec<f32>,
    pub normals: Vec<f32>,
    pub indices: Vec<u32>,
    pub face_tri_start: Vec<u32>,
    pub edge_points: Vec<f32>,
    pub edge_start: Vec<u32>,
}

impl Solid {
    pub fn faces(&self) -> Vec<Face> {
        let mut planes: BTreeMap<(usize, usize, bool), BTreeSet<Point>> = BTreeMap::new();
        for cell in self.filled() {
            for axis in 0..3 {
                for (side, neighbour) in neighbours(cell, axis).into_iter().enumerate() {
                    if !self.occupied(neighbour) {
                        let mut patch = cell;
                        patch[axis] += side;
                        planes
                            .entry((axis, patch[axis], side == 1))
                            .or_default()
                            .insert(patch);
                    }
                }
            }
        }

        let mut faces = Vec::new();
        for ((axis, plane_index, positive), mut patches) in planes {
            let (u, v) = lateral_axes(axis);
            while let Some(seed) = patches.pop_first() {
                let mut pending = vec![seed];
                let mut group = Vec::new();
                let mut center = [0.; 3];
                let mut area = 0.;
                while let Some(patch) = pending.pop() {
                    group.push(patch);
                    let [a, _, b, _] = self.corners(patch, axis).map(|corner| self.point(corner));
                    let patch_area = (b[u] - a[u]) * (b[v] - a[v]);
                    area += patch_area;
                    for i in 0..3 {
                        center[i] += (a[i] + b[i]) * 0.5 * patch_area;
                    }
                    for next in neighbours(patch, u).into_iter().chain(neighbours(patch, v)) {
                        if patches.remove(&next) {
                            pending.push(next);
                        }
                    }
                }
                faces.push(Face {
                    axis,
                    positive,
                    plane: self.planes[axis][plane_index],
                    patches: group,
                    center: center.map(|value| value / area),
                    area,
                });
            }
        }
        faces
    }

    pub fn boundary_mesh(&self) -> (Vec<Face>, Mesh) {
        let faces = self.faces();
        let mut mesh = Mesh::default();
        let mut edges = BTreeMap::new();
        mesh.face_tri_start.push(0);
        mesh.edge_start.push(0);
        for (id, face) in faces.iter().enumerate() {
            let mut normal = [0.; 3];
            normal[face.axis] = if face.positive { 1. } else { -1. };
            let mut vertices = BTreeMap::new();
            for &patch in &face.patches {
                let mut corners = self.corners(patch, face.axis);
                if !face.positive {
                    corners.swap(1, 3);
                }
                let [a, b, c, d] = corners.map(|corner| {
                    *vertices.entry(corner).or_insert_with(|| {
                        let index = (mesh.positions.len() / 3) as u32;
                        mesh.positions
                            .extend(self.point(corner).map(|value| value as f32));
                        mesh.normals.extend(normal);
                        index
                    })
                });
                mesh.indices.extend([a, b, c, a, c, d]);
                for (start, end) in patch_edges(corners) {
                    let first = edges.entry((start, end)).or_insert(id);
                    if *first != id {
                        mesh.edge_points
                            .extend(self.point(start).map(|value| value as f32));
                        mesh.edge_points
                            .extend(self.point(end).map(|value| value as f32));
                        mesh.edge_start.push((mesh.edge_points.len() / 3) as u32);
                    }
                }
            }
            mesh.face_tri_start.push((mesh.indices.len() / 3) as u32);
        }
        (faces, mesh)
    }

    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        if !self.cells.contains(&true) {
            return Err("That operation would leave no solid.");
        }
        let mut edge_incidence = BTreeMap::<Edge, Vec<usize>>::new();
        let mut vertex_incidence = BTreeMap::<Point, Vec<usize>>::new();
        let mut patch_id = 0;
        for face in self.faces() {
            for patch in face.patches {
                let corners = self.corners(patch, face.axis);
                for edge in patch_edges(corners) {
                    edge_incidence.entry(edge).or_default().push(patch_id);
                }
                for point in corners {
                    vertex_incidence.entry(point).or_default().push(patch_id);
                }
                patch_id += 1;
            }
        }
        let mut fans = BTreeMap::<Point, Vec<[usize; 2]>>::new();
        for ((a, b), patches) in edge_incidence {
            let [left, right] = patches[..] else {
                return Err("That operation would connect solids along a single edge.");
            };
            for point in [a, b] {
                fans.entry(point).or_default().push([left, right]);
            }
        }
        for (point, patches) in vertex_incidence {
            let mut seen = BTreeSet::new();
            let mut pending = vec![patches[0]];
            while let Some(patch) = pending.pop() {
                if seen.insert(patch) {
                    for &[a, b] in &fans[&point] {
                        if patch == a {
                            pending.push(b);
                        }
                        if patch == b {
                            pending.push(a);
                        }
                    }
                }
            }
            if seen.len() != patches.len() {
                return Err("That operation would connect solids at a single point.");
            }
        }
        Ok(())
    }

    fn point(&self, point: Point) -> [f64; 3] {
        std::array::from_fn(|axis| self.planes[axis][point[axis]])
    }

    fn corners(&self, a: Point, axis: usize) -> [Point; 4] {
        let (u, v) = lateral_axes(axis);
        let mut b = a;
        b[u] += 1;
        let mut c = b;
        c[v] += 1;
        let mut d = a;
        d[v] += 1;
        [a, b, c, d]
    }
}

fn patch_edges(corners: [Point; 4]) -> [Edge; 4] {
    std::array::from_fn(|i| {
        let a = corners[i];
        let b = corners[(i + 1) % 4];
        (a.min(b), a.max(b))
    })
}

pub(crate) fn lateral_axes(axis: usize) -> (usize, usize) {
    ((axis + 1) % 3, (axis + 2) % 3)
}

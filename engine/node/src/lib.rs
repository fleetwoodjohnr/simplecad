//! SimpleCAD's geometry engine as a Node addon: OCCT shapes through the `occt`
//! shim, and the `.scad3` archive entries the engine reads them from.

use napi::bindgen_prelude::*;
use napi_derive::napi;
use occt::ffi;
use std::io::{Cursor, Read};

/// Chord deflection (mm) and angle (radians) the Python app meshes with.
const DEFLECTION: f64 = 0.05;
const ANGLE: f64 = 0.35;

#[napi]
pub struct Shape {
    inner: occt::UniquePtr<ffi::Shape>,
}

#[napi(object)]
pub struct FaceInfo {
    pub planar: bool,
    pub center: Vec<f64>,
    /// A point on the face itself, for the pull handle: the centre of a face
    /// with a hole in the middle is in the hole.
    pub anchor: Vec<f64>,
    pub normal: Vec<f64>,
    pub area: f64,
}

#[napi(object)]
pub struct DisplayShape {
    pub volume: f64,
    pub faces: Vec<FaceInfo>,
    pub positions: Float32Array,
    pub normals: Float32Array,
    pub indices: Uint32Array,
    pub face_tri_start: Uint32Array,
    pub edge_points: Float32Array,
    pub edge_start: Uint32Array,
}

fn wrap(
    result: std::result::Result<occt::UniquePtr<ffi::Shape>, occt::Exception>,
) -> Result<Shape> {
    result
        .map(|inner| Shape { inner })
        .map_err(|e| Error::from_reason(e.what()))
}

fn reason(e: occt::Exception) -> Error {
    Error::from_reason(e.what())
}

/// The centroid of the face's largest triangle: on the face, unlike its centre of mass.
fn anchor(mesh: &ffi::MeshBuffers, face: usize) -> Option<Vec<f64>> {
    let point = |i: u32| {
        let i = i as usize * 3;
        [0, 1, 2].map(|k| f64::from(mesh.positions[i + k]))
    };
    let (first, last) = (
        mesh.face_tri_start[face] as usize,
        mesh.face_tri_start[face + 1] as usize,
    );
    (first..last)
        .map(|t| {
            let [a, b, c] = [0, 1, 2].map(|k| point(mesh.indices[t * 3 + k]));
            let u = [0, 1, 2].map(|k| b[k] - a[k]);
            let v = [0, 1, 2].map(|k| c[k] - a[k]);
            let cross = [
                u[1] * v[2] - u[2] * v[1],
                u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0],
            ];
            let area = cross.iter().map(|x| x * x).sum::<f64>();
            (area, [0, 1, 2].map(|k| (a[k] + b[k] + c[k]) / 3.0))
        })
        .max_by(|x, y| x.0.total_cmp(&y.0))
        .map(|(_, centre)| centre.to_vec())
}

#[napi]
impl Shape {
    #[napi(factory)]
    pub fn make_box(dx: f64, dy: f64, dz: f64) -> Result<Self> {
        wrap(ffi::make_box(dx, dy, dz))
    }

    /// A shape from OCCT's text B-Rep, as `.scad3` files store bodies.
    #[napi(factory)]
    pub fn from_brep(data: Buffer) -> Result<Self> {
        wrap(ffi::read_brep(&data))
    }

    #[napi]
    pub fn push_pull(&self, face: u32, distance: f64) -> Result<Shape> {
        wrap(ffi::push_pull(&self.inner, face, distance))
    }

    #[napi]
    pub fn face_count(&self) -> Result<u32> {
        Ok(ffi::faces(&self.inner).map_err(reason)?.len() as u32)
    }

    #[napi]
    pub fn display(&self) -> Result<DisplayShape> {
        let mesh = ffi::mesh(&self.inner, DEFLECTION, ANGLE).map_err(reason)?;
        let faces = ffi::faces(&self.inner)
            .map_err(reason)?
            .into_iter()
            .enumerate()
            .map(|(i, f)| FaceInfo {
                planar: f.planar,
                center: f.center.to_vec(),
                anchor: anchor(&mesh, i).unwrap_or_else(|| f.center.to_vec()),
                normal: f.normal.to_vec(),
                area: f.area,
            })
            .collect();
        Ok(DisplayShape {
            volume: ffi::volume(&self.inner),
            faces,
            positions: mesh.positions.into(),
            normals: mesh.normals.into(),
            indices: mesh.indices.into(),
            face_tri_start: mesh.face_tri_start.into(),
            edge_points: mesh.edge_points.into(),
            edge_start: mesh.edge_start.into(),
        })
    }
}

/// The named entries of a zip archive; `null` for one that is missing or
/// damaged, which the caller skips, as the Python app does.
#[napi]
pub fn read_archive(data: Buffer, names: Vec<String>) -> Result<Vec<Option<Buffer>>> {
    let mut archive = zip::ZipArchive::new(Cursor::new(&data[..])).map_err(|_| {
        Error::from_reason("This is not a SimpleCAD project, or the file is damaged.")
    })?;
    Ok(names
        .iter()
        .map(|name| {
            let mut entry = archive.by_name(name).ok()?;
            let mut bytes = Vec::with_capacity(entry.size().min(1 << 30) as usize);
            entry.read_to_end(&mut bytes).ok()?;
            Some(bytes.into())
        })
        .collect())
}

/// Crashes the engine process for real, so the isolation can be drilled.
#[napi]
pub fn crash() {
    ffi::crash();
}

//! SimpleCAD's geometry engine as a Node addon: OCCT shapes through the `occt`
//! shim, and `.scad3` projects through the `scad` core.

use napi::bindgen_prelude::*;
use napi_derive::napi;
use occt::ffi;
use std::sync::Arc;

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

#[napi(object, object_from_js = false)]
pub struct OpenedBody {
    pub name: String,
    pub visible: bool,
    /// `None` when the file holds no readable geometry for this body.
    pub shape: Option<Shape>,
}

#[napi(object, object_from_js = false)]
pub struct OpenedProject {
    pub bodies: Vec<OpenedBody>,
    pub notes: Vec<String>,
    /// What saving edits.
    pub project: Option<Project>,
}

/// A project at one point of the session: its model, and the file it came from,
/// whose geometry and assets every save carries over.
#[napi]
pub struct Project {
    model: scad::document::Model,
    original: Arc<Vec<u8>>,
}

#[napi(object, object_from_js = false)]
pub struct Pulled {
    pub project: Project,
    pub shape: Shape,
}

/// A distance as the Pull tool stores it: the number as typed (`50.75`, `5`).
fn expression(distance: f64) -> String {
    format!("{distance}")
}

#[napi]
impl Project {
    /// Push/pull face `face` of `shape` (the body `body` as shown) by `distance`
    /// mm: the new shape, and this project with the step recorded as the Pull tool would.
    #[napi]
    pub fn push_pull(
        &self,
        body: String,
        shape: &Shape,
        face: u32,
        distance: f64,
    ) -> Result<Pulled> {
        let f = ffi::face_fingerprint(&shape.inner, face).map_err(reason)?;
        let pulled = ffi::push_pull(&shape.inner, face, distance).map_err(reason)?;
        let fingerprint = serde_json::json!({
            "kind": "face", "geometry": f.geometry, "measure": f.measure, "center": f.center,
            "direction": if f.has_direction { serde_json::json!(f.direction) } else { serde_json::Value::Null },
            "radius": if f.has_radius { serde_json::json!(f.radius) } else { serde_json::Value::Null },
            "neighbours": [],
        });
        let mut model = self.model.clone();
        let reference = model.face_ref(&body, face, fingerprint);
        let brep = ffi::write_brep(&pulled).map_err(reason)?;
        model
            .push_pull(&body, reference, &expression(distance), brep)
            .map_err(Error::from_reason)?;
        Ok(Pulled {
            project: Project {
                model,
                original: self.original.clone(),
            },
            shape: Shape { inner: pulled },
        })
    }

    /// The `.scad3` of this point of the session.
    #[napi]
    pub fn save(&self) -> Result<Buffer> {
        self.model
            .save(&self.original)
            .map(Buffer::from)
            .map_err(Error::from_reason)
    }
}

/// Every body of a `.scad3`, shown or hidden, as the Python app loads it.
#[napi]
pub fn open_project(data: Buffer) -> Result<OpenedProject> {
    let project = scad::project::read(&data).map_err(Error::from_reason)?;
    let saveable = Some(Project {
        model: scad::document::Model::open(&project),
        original: Arc::new(data.to_vec()),
    });
    let mut notes = project.notes;
    let bodies = project
        .bodies
        .into_iter()
        .map(|body| {
            let shape = body.brep.and_then(|brep| {
                let shape = ffi::read_brep(&brep).and_then(|shape| match &body.location {
                    Some(matrix) => ffi::located(&shape, matrix),
                    None => Ok(shape),
                });
                shape
                    .map_err(|_| {
                        notes.push(format!(
                            "\"{}\" could not be read from this file.",
                            body.name
                        ))
                    })
                    .ok()
                    .map(|inner| Shape { inner })
            });
            OpenedBody {
                name: body.name,
                visible: body.visible,
                shape,
            }
        })
        .collect();
    Ok(OpenedProject {
        bodies,
        notes,
        project: saveable,
    })
}

/// Crashes the engine process for real, so the isolation can be drilled.
#[napi]
pub fn crash() {
    ffi::crash();
}

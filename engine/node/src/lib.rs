//! SimpleCAD's geometry engine as a Node addon: OCCT shapes through the `occt`
//! shim, and `.scad3` projects through the `scad` core.

use napi::bindgen_prelude::*;
use napi_derive::napi;
use occt::ffi;
use std::collections::{BTreeSet, HashMap};
use std::rc::Rc;
use std::sync::Arc;

/// Chord deflection (mm) and angle (radians) the Python app meshes with.
const DEFLECTION: f64 = 0.05;
const ANGLE: f64 = 0.35;

#[napi]
pub struct Shape {
    inner: scad::rebuild::Shape,
}

impl Shape {
    fn new(inner: occt::UniquePtr<ffi::Shape>) -> Self {
        Self {
            inner: Rc::new(inner),
        }
    }
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

#[napi(object)]
pub struct MeshPart {
    pub name: String,
    pub positions: Float32Array,
    pub indices: Uint32Array,
}

#[napi]
pub fn export_mesh(format: String, parts: Vec<MeshPart>) -> Result<Buffer> {
    let parts: Vec<_> = parts
        .iter()
        .map(|part| scad::export::Part {
            name: &part.name,
            positions: &part.positions,
            indices: &part.indices,
        })
        .collect();
    scad::export::encode(&format, &parts)
        .map(Buffer::from)
        .map_err(Error::from_reason)
}

#[napi]
pub fn export_step(parts: Vec<ClassInstance<Shape>>) -> Result<Buffer> {
    let mut shapes = ffi::shapes_new();
    for part in parts {
        ffi::shapes_push(shapes.pin_mut(), &part.inner);
    }
    ffi::write_step(&shapes).map(Buffer::from).map_err(reason)
}

fn wrap(
    result: std::result::Result<occt::UniquePtr<ffi::Shape>, occt::Exception>,
) -> Result<Shape> {
    result
        .map(Shape::new)
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
    pub id: Option<String>,
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

#[napi(object, object_to_js = false)]
pub struct ShownBody<'a> {
    pub id: Option<String>,
    pub name: String,
    pub shape: ClassInstance<'a, Shape>,
}

#[napi(object)]
pub struct HistoryDepth {
    pub undo: u32,
    pub redo: u32,
}

/// A project at one point of the session: its model, and the file it came from,
/// whose geometry and assets every save carries over.
#[napi]
pub struct Project {
    model: scad::document::Model,
    original: Arc<Vec<u8>>,
}

/// A step of the model: its name, type (`push_pull`, `box`...) and state
/// (`ok`, `suppressed`, `failed`, `needs_attention`) as last built.
#[napi(object, object_from_js = false)]
pub struct Step {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub state: String,
    pub message: Option<String>,
    pub missing_input: Option<MissingInput>,
    pub face_settings: Option<HashMap<String, String>>,
    pub shape_settings: Option<HashMap<String, String>>,
    pub matching_thread: Option<HashMap<String, String>>,
    pub construction_plane: Option<HashMap<String, Vec<f64>>>,
}

#[napi(object, object_from_js = false)]
pub struct MissingInput {
    pub name: String,
    pub object_id: String,
    pub face: bool,
}

#[napi(object, object_to_js = false)]
pub struct ReferenceRepair {
    pub step_id: String,
    pub missing_id: String,
    pub replacement_id: Option<String>,
    pub replacement_name: String,
    pub face: Option<u32>,
}

#[napi(object, object_from_js = false)]
pub struct Pulled {
    pub project: Project,
    pub shape: Shape,
}

#[napi(object, object_from_js = false)]
pub struct CreatedShape {
    pub project: Project,
    pub name: String,
    pub shape: Shape,
}

#[napi(object, object_from_js = false)]
pub struct ImportedModel {
    pub project: Project,
    pub bodies: Vec<OpenedBody>,
}

#[napi(object, object_from_js = false)]
pub struct SketchSolution {
    pub recipe: String,
    pub message: String,
    pub solved: bool,
}

#[napi(object, object_to_js = false)]
pub struct SketchDraftRequest {
    pub id: String,
    pub creating: bool,
    pub recipe: String,
}

#[napi(object, object_to_js = false)]
pub struct SketchSolidRequest {
    pub kind: String,
    pub profiles: Vec<String>,
    pub inputs: String,
}

#[napi(object)]
pub struct StackRequest {
    pub moving_name: String,
    pub moving_face: u32,
    pub target_name: String,
    pub target_face: u32,
    pub operation: String,
    pub offset: f64,
    pub rotation: f64,
    pub flip: bool,
    pub horizontal: String,
    pub vertical: String,
    pub horizontal_gap: f64,
    pub vertical_gap: f64,
    pub view_direction: Option<Vec<f64>>,
    pub view_up: Option<Vec<f64>>,
}

#[napi(object)]
pub struct TransformRequest {
    pub kind: String,
    pub values: Vec<String>,
    pub pivot: Option<Vec<f64>>,
    pub axis: Option<Vec<f64>>,
}

#[napi(object, object_to_js = false)]
pub struct FaceFeatureRequest<'a> {
    pub kind: String,
    pub body: String,
    pub face: u32,
    pub inputs: HashMap<String, String>,
    pub position: Option<Vec<f64>>,
    pub target: Option<ShownBody<'a>>,
}

/// A distance as the Pull tool stores it: the number as typed (`50.75`, `5`).
fn expression(distance: f64) -> String {
    format!("{distance}")
}

fn bounds_for<'a>(shapes: impl IntoIterator<Item = &'a ffi::Shape>) -> Result<[f64; 6]> {
    let mut low = [f64::INFINITY; 3];
    let mut high = [f64::NEG_INFINITY; 3];
    for shape in shapes {
        let bounds = ffi::bounds(shape).map_err(reason)?;
        for axis in 0..3 {
            low[axis] = low[axis].min(bounds[axis]);
            high[axis] = high[axis].max(bounds[axis + 3]);
        }
    }
    Ok([low[0], low[1], low[2], high[0], high[1], high[2]])
}

fn import_offset(current: [f64; 6], incoming: [f64; 6]) -> [f64; 3] {
    let gap = ((incoming[3] - incoming[0]) * 0.15).max(10.0);
    [
        current[3] + gap - incoming[0],
        current[1] - incoming[1],
        current[2] - incoming[2],
    ]
    .map(|axis| (axis * 10_000.0).round() / 10_000.0)
}

impl Project {
    fn import_native(
        &self,
        bytes: &[u8],
        limit: u32,
        visible: &[ClassInstance<Shape>],
    ) -> Result<ImportedModel> {
        let source = scad::project::read(bytes).map_err(Error::from_reason)?;
        if source.bodies.is_empty() {
            return Err(Error::from_reason("The source project contains no bodies."));
        }
        if source.bodies.len() > limit as usize {
            return Err(Error::from_reason(
                "This import would exceed the project's body limit.",
            ));
        }
        let geometry = source
            .bodies
            .iter()
            .map(|body| {
                let brep = body.brep.as_deref().ok_or_else(|| {
                    Error::from_reason(format!(
                        "\"{}\" has no saved geometry. Repair the source project first.",
                        body.name
                    ))
                })?;
                let shape = ffi::read_brep(brep)
                    .and_then(|shape| match &body.location {
                        Some(matrix) => ffi::located(&shape, matrix),
                        None => Ok(shape),
                    })
                    .map_err(reason)?;
                Ok(scad::rebuild::Body {
                    id: body.id.clone(),
                    name: body.name.clone(),
                    shape: Some(Rc::new(shape)),
                })
            })
            .collect::<Result<Vec<_>>>()?;
        let offset = if visible.is_empty() {
            [0.0; 3]
        } else {
            import_offset(
                bounds_for(visible.iter().map(|part| &**part.inner))?,
                bounds_for(
                    geometry
                        .iter()
                        .filter_map(|body| body.shape.as_ref().map(|shape| &***shape)),
                )?,
            )
        };
        let (model, parts) = self
            .model
            .import_project(&source, bytes, geometry, offset)
            .map_err(Error::from_reason)?;
        let bodies = parts
            .into_iter()
            .zip(&source.bodies)
            .map(|(body, source)| OpenedBody {
                id: None,
                name: body.name,
                visible: source.visible,
                shape: body.shape.map(|inner| Shape { inner }),
            })
            .collect();
        Ok(ImportedModel {
            project: Project {
                model,
                original: self.original.clone(),
            },
            bodies,
        })
    }

    fn shown_shapes(&self, shown: Vec<ShownBody>) -> HashMap<String, scad::rebuild::Shape> {
        let shown: HashMap<_, _> = shown
            .into_iter()
            .map(|body| (body.id.unwrap_or(body.name), body.shape.inner.clone()))
            .collect();
        self.model.payload["bodies"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|body| {
                let id = body["id"].as_str()?;
                let prior = shown
                    .get(id)
                    .or_else(|| shown.get(body["name"].as_str()?))?;
                Some((id.to_owned(), prior.clone()))
            })
            .collect()
    }

    fn rebuilt_model(
        &self,
        model: scad::document::Model,
        report: scad::rebuild::Report,
    ) -> OpenedProject {
        let hidden: std::collections::HashSet<_> = model.payload["bodies"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|body| body["visible"].as_bool() == Some(false))
            .filter_map(|body| body["id"].as_str())
            .collect();
        let bodies = report
            .bodies
            .into_iter()
            .map(|body| OpenedBody {
                name: body.name,
                visible: !hidden.contains(body.id.as_str()),
                id: Some(body.id),
                shape: body.shape.map(|inner| Shape { inner }),
            })
            .collect();
        OpenedProject {
            bodies,
            notes: Vec::new(),
            project: Some(Project {
                model,
                original: self.original.clone(),
            }),
        }
    }
}

#[napi]
impl Project {
    #[napi]
    pub fn import_model(
        &self,
        bytes: Uint8Array,
        name: String,
        format: String,
        limit: u32,
        visible: Vec<ClassInstance<Shape>>,
    ) -> Result<ImportedModel> {
        if format == "scad3" {
            return self.import_native(&bytes, limit, &visible);
        }
        let mut parts = match format.as_str() {
            "step" => ffi::read_step(&bytes, &name).map_err(reason),
            "brep" => ffi::read_brep_parts(&bytes, &name).map_err(reason),
            "iges" => scad::iges::read(&bytes, &name).map_err(Error::from_reason),
            "stl" | "obj" | "3mf" | "gltf" | "glb" => {
                scad::mesh_import::read(&format, &bytes, &name).map_err(Error::from_reason)
            }
            _ => Err(Error::from_reason("Choose a supported model file.")),
        }?;
        if parts.len() > limit as usize {
            return Err(Error::from_reason(
                "This import would exceed the project's body limit.",
            ));
        }
        let offset = if visible.is_empty() {
            [0.0; 3]
        } else {
            let current = bounds_for(visible.iter().map(|part| &**part.inner))?;
            let incoming = bounds_for(parts.iter().map(|part| &*part.shape))?;
            import_offset(current, incoming)
        };
        let shift = ffi::trsf_translation(&offset);
        let assets = parts
            .iter_mut()
            .map(|part| {
                let asset = ffi::write_brep(&part.shape).map_err(reason)?;
                let placed = if offset == [0.0; 3] {
                    asset.clone()
                } else {
                    part.shape = ffi::transform_shape(&part.shape, &shift, true).map_err(reason)?;
                    ffi::write_brep(&part.shape).map_err(reason)?
                };
                Ok(scad::document::ImportedPart {
                    name: part.name.clone(),
                    asset,
                    placed,
                    note: part.note.clone(),
                })
            })
            .collect::<Result<Vec<_>>>()?;
        let mut model = self.model.clone();
        let names = model
            .add_imports(assets, offset)
            .map_err(Error::from_reason)?;
        let bodies = names
            .into_iter()
            .zip(parts)
            .map(|(name, part)| OpenedBody {
                id: None,
                name,
                visible: true,
                shape: Some(Shape::new(part.shape)),
            })
            .collect();
        Ok(ImportedModel {
            project: Project {
                model,
                original: self.original.clone(),
            },
            bodies,
        })
    }

    #[napi]
    pub fn add_shape(
        &self,
        kind: String,
        inputs: std::collections::HashMap<String, String>,
    ) -> Result<CreatedShape> {
        let mut model = self.model.clone();
        let (name, inner) =
            scad::rebuild::create_primitive(&mut model, &kind, serde_json::json!(inputs))
                .map_err(Error::from_reason)?;
        Ok(CreatedShape {
            project: Project {
                model,
                original: self.original.clone(),
            },
            name,
            shape: Shape::new(inner),
        })
    }
    #[napi]
    pub fn place(&self, moving: &Shape, target: &Shape, edit: StackRequest) -> Result<Pulled> {
        use scad::align::PickedFace;
        let mut model = self.model.clone();
        let mut inputs = serde_json::json!({
            "operation": edit.operation, "offset": expression(edit.offset),
            "rotation": expression(edit.rotation), "flip": edit.flip,
        });
        if edit.operation != "concentric" {
            inputs["placement_mode"] = serde_json::json!("edge_gap");
            inputs["horizontal"] = serde_json::json!(edit.horizontal);
            inputs["vertical"] = serde_json::json!(edit.vertical);
            inputs["horizontal_gap"] = serde_json::json!(expression(edit.horizontal_gap));
            inputs["vertical_gap"] = serde_json::json!(expression(edit.vertical_gap));
            if let (Some(direction), Some(up)) = (edit.view_direction, edit.view_up) {
                inputs["view_direction"] = serde_json::json!(direction);
                inputs["view_up"] = serde_json::json!(up);
            }
        }
        let placed = scad::align::place(
            &mut model,
            PickedFace {
                name: &edit.moving_name,
                shape: &moving.inner,
                index: edit.moving_face,
            },
            PickedFace {
                name: &edit.target_name,
                shape: &target.inner,
                index: edit.target_face,
            },
            inputs,
        )
        .map_err(Error::from_reason)?;
        Ok(Pulled {
            project: Project {
                model,
                original: self.original.clone(),
            },
            shape: Shape::new(placed),
        })
    }

    #[napi]
    pub fn transform(&self, body: String, shape: &Shape, edit: TransformRequest) -> Result<Pulled> {
        let vec3 = |v: &[f64]| v.len() == 3 && v.iter().all(|n| n.is_finite() && n.abs() <= 1e9);
        let invalid = || Error::from_reason("Enter a valid move or rotation.");
        if edit.pivot.as_deref().is_some_and(|v| !vec3(v))
            || edit
                .axis
                .as_deref()
                .is_some_and(|v| !vec3(v) || v.iter().all(|n| n.abs() <= 1e-9))
        {
            return Err(invalid());
        }
        let (label, inputs) = match (edit.kind.as_str(), edit.values.as_slice(), edit.axis) {
            ("move", [x, y, z], None) => ("Move", serde_json::json!({ "dx": x, "dy": y, "dz": z })),
            ("rotate", [x, y, z], None) => (
                "Rotate",
                serde_json::json!({
                    "rx": x, "ry": y, "rz": z, "order": "xyz", "pivot": edit.pivot,
                }),
            ),
            ("rotate", [angle], Some(axis)) => (
                "Rotate",
                serde_json::json!({
                    "axis": axis, "angle": angle, "pivot": edit.pivot,
                }),
            ),
            _ => return Err(invalid()),
        };
        let mut model = self.model.clone();
        let moved = scad::rebuild::create_transform(&mut model, &body, &shape.inner, label, inputs)
            .map_err(Error::from_reason)?;
        Ok(Pulled {
            project: Project {
                model,
                original: self.original.clone(),
            },
            shape: Shape::new(moved),
        })
    }

    #[napi]
    pub fn add_offset_plane(
        &self,
        body: String,
        face: u32,
        distance: String,
        shown: Vec<ShownBody>,
    ) -> Result<OpenedProject> {
        let mut model = self.model.clone();
        let report = model
            .add_offset_plane(
                &body,
                face,
                &distance,
                &self.original,
                &self.shown_shapes(shown),
            )
            .map_err(Error::from_reason)?;
        Ok(self.rebuilt_model(model, report))
    }

    /// `inputs` is the step's settings as JSON: expressions as strings, directions as `[x, y, z]`.
    #[napi]
    pub fn add_body_operation(
        &self,
        kind: String,
        body: String,
        inputs: String,
        shown: Vec<ShownBody>,
    ) -> Result<OpenedProject> {
        let inputs = serde_json::from_str(&inputs)
            .map_err(|_| Error::from_reason("The operation settings could not be read."))?;
        let mut model = self.model.clone();
        let report = model
            .add_body_operation(
                &kind,
                &body,
                inputs,
                &self.original,
                &self.shown_shapes(shown),
            )
            .map_err(Error::from_reason)?;
        Ok(self.rebuilt_model(model, report))
    }

    #[napi]
    pub fn add_face_feature(&self, shape: &Shape, request: FaceFeatureRequest) -> Result<Pulled> {
        let FaceFeatureRequest {
            kind,
            body,
            face,
            inputs,
            position,
            target,
        } = request;
        let mut model = self.model.clone();
        let mut inputs = serde_json::json!(inputs);
        for key in [
            "counterbore_diameter",
            "counterbore_depth",
            "countersink_diameter",
        ] {
            if inputs[key].as_str() == Some("") {
                inputs.as_object_mut().unwrap().remove(key);
            }
        }
        for flag in ["left_hand", "resize"] {
            if let Some(value) = inputs[flag].as_str() {
                inputs[flag] = serde_json::json!(value == "true");
            }
        }
        if let Some(position) = position {
            if position.len() != 3 || position.iter().any(|value| !value.is_finite()) {
                return Err(Error::from_reason("Select a position on the face."));
            }
            inputs["position"] = serde_json::json!(position);
        }
        let inner = scad::rebuild::create_face_feature(
            &mut model,
            scad::align::PickedFace {
                name: &body,
                shape: &shape.inner,
                index: face,
            },
            &kind,
            inputs,
            target.as_ref().map(|target| scad::rebuild::FaceTarget {
                name: &target.name,
                shape: &target.shape.inner,
            }),
        )
        .map_err(Error::from_reason)?;
        Ok(Pulled {
            project: Project {
                model,
                original: self.original.clone(),
            },
            shape: Shape::new(inner),
        })
    }

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
            shape: Shape::new(pulled),
        })
    }

    /// Every step of the model, in order, as the History panel lists them.
    #[napi]
    pub fn steps(&self) -> Vec<Step> {
        self.model
            .steps()
            .into_iter()
            .map(|step| Step {
                id: step.id,
                name: step.name,
                kind: step.kind,
                state: step.state,
                message: step.message,
                missing_input: step.missing_input.map(|reference| MissingInput {
                    name: reference.name,
                    object_id: reference.object_id,
                    face: reference.face,
                }),
                face_settings: step.face_settings,
                shape_settings: step.shape_settings,
                matching_thread: step.matching_thread,
                construction_plane: step.construction_plane,
            })
            .collect()
    }

    #[napi]
    pub fn history_depth(&self) -> HistoryDepth {
        let count = |stack| {
            self.model.payload["history"][stack]
                .as_array()
                .map_or(0, |actions| actions.len() as u32)
        };
        HistoryDepth {
            undo: count("undo"),
            redo: count("redo"),
        }
    }

    /// Move through actions saved in the project, keeping the inverse patches
    /// for redo and using the same rebuild as the CLI history check.
    #[napi]
    pub fn move_history(&self, offset: i32, shown: Vec<ShownBody>) -> Result<OpenedProject> {
        let count = offset.unsigned_abs();
        let depth = self.history_depth();
        let redo = offset > 0;
        if count == 0 || count > if redo { depth.redo } else { depth.undo } {
            return Err(Error::from_reason("There is no such history point."));
        }
        let old_shapes = self.shown_shapes(shown);
        let mut model = self.model.clone();
        let mut stale = BTreeSet::new();
        for _ in 0..count {
            stale.extend(model.move_history(redo).map_err(Error::from_reason)?);
        }
        let report = scad::rebuild::rebuild(&mut model, &self.original, &old_shapes, &stale)
            .map_err(Error::from_reason)?;
        Ok(self.rebuilt_model(model, report))
    }

    #[napi]
    pub fn repair_input(
        &self,
        request: ReferenceRepair,
        shown: Vec<ShownBody>,
    ) -> Result<OpenedProject> {
        let body_id = request
            .replacement_id
            .as_deref()
            .or_else(|| {
                self.model.payload["bodies"]
                    .as_array()?
                    .iter()
                    .find(|body| body["name"] == request.replacement_name)?["id"]
                    .as_str()
            })
            .ok_or_else(|| Error::from_reason("Select an existing replacement body."))?;
        let old_shapes = self.shown_shapes(shown);
        let mut model = self.model.clone();
        let report = model
            .repair_reference(
                &request.step_id,
                &request.missing_id,
                body_id,
                request.face,
                &self.original,
                &old_shapes,
            )
            .map_err(Error::from_reason)?;
        Ok(self.rebuilt_model(model, report))
    }

    #[napi]
    pub fn edit_step_settings(
        &self,
        step_id: String,
        changes: HashMap<String, String>,
        shown: Vec<ShownBody>,
    ) -> Result<OpenedProject> {
        let old_shapes = self.shown_shapes(shown);
        let mut model = self.model.clone();
        let report = model
            .edit_step_settings(&step_id, changes, &self.original, &old_shapes)
            .map_err(Error::from_reason)?;
        Ok(self.rebuilt_model(model, report))
    }

    #[napi]
    pub fn sketch_draft(&self, id: String) -> Result<String> {
        let draft = self.model.sketch_draft(&id).map_err(Error::from_reason)?;
        Ok(draft.to_string())
    }

    #[napi]
    pub fn solve_sketch(&self, recipe: String) -> Result<SketchSolution> {
        let value = serde_json::from_str(&recipe)
            .map_err(|_| Error::from_reason("The sketch could not be read."))?;
        let solution = self
            .model
            .solve_sketch_draft(&value)
            .map_err(Error::from_reason)?;
        Ok(SketchSolution {
            recipe: solution.recipe.to_string(),
            message: solution.message(),
            solved: solution.state != scad::sketch::State::Conflicting,
        })
    }

    #[napi]
    pub fn apply_sketch_draft(
        &self,
        request: SketchDraftRequest,
        shown: Vec<ShownBody>,
    ) -> Result<OpenedProject> {
        let recipe = serde_json::from_str(&request.recipe)
            .map_err(|_| Error::from_reason("The sketch could not be read."))?;
        let mut model = self.model.clone();
        let report = model
            .apply_sketch_draft(
                &request.id,
                request.creating,
                recipe,
                &self.original,
                &self.shown_shapes(shown),
            )
            .map_err(Error::from_reason)?;
        Ok(self.rebuilt_model(model, report))
    }

    #[napi]
    pub fn apply_sketch_solid(
        &self,
        request: SketchSolidRequest,
        shown: Vec<ShownBody>,
    ) -> Result<OpenedProject> {
        let inputs = serde_json::from_str(&request.inputs)
            .map_err(|_| Error::from_reason("The sketch settings could not be read."))?;
        let mut model = self.model.clone();
        let report = model
            .apply_sketch_solid(
                &request.kind,
                &request.profiles,
                inputs,
                &self.original,
                &self.shown_shapes(shown),
            )
            .map_err(Error::from_reason)?;
        Ok(self.rebuilt_model(model, report))
    }

    /// The `.scad3` of this point of the session with its newest `undone` steps
    /// taken back: they stay in the file as its redo steps.
    #[napi]
    pub fn save(&self, undone: u32) -> Result<Buffer> {
        let mut model = self.model.clone();
        for _ in 0..undone {
            model.undo().map_err(Error::from_reason)?;
        }
        model
            .save(&self.original)
            .map(Buffer::from)
            .map_err(Error::from_reason)
    }
}

#[napi(object, object_to_js = false)]
pub struct MeasureSelection<'a> {
    pub shape: ClassInstance<'a, Shape>,
    pub face: Option<u32>,
}

#[napi(object, object_from_js = false)]
pub struct Measurement {
    pub label: String,
    pub values: Vec<f64>,
    pub unit: String,
}

#[napi]
pub fn measure_shapes(picks: Vec<MeasureSelection>) -> Result<Vec<Measurement>> {
    let choices: Vec<_> = picks
        .iter()
        .map(|pick| (&**pick.shape.inner, pick.face))
        .collect();
    Ok(scad::measure::measure(&choices)
        .map_err(reason)?
        .into_iter()
        .map(|reading| Measurement {
            label: reading.label.into(),
            values: reading.values,
            unit: reading.unit.into(),
        })
        .collect())
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
                    .map(Shape::new)
            });
            OpenedBody {
                id: Some(body.id),
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

/// A new project holding one box, as the Python app's Shape panel makes it.
#[napi]
pub fn new_project() -> Result<OpenedProject> {
    let shape = ffi::make_box(40.0, 30.0, 20.0).map_err(reason)?;
    let brep = ffi::write_brep(&shape).map_err(reason)?;
    let model = scad::document::Model::new_box(brep).map_err(Error::from_reason)?;
    Ok(OpenedProject {
        bodies: vec![OpenedBody {
            id: None,
            name: "Box".into(),
            visible: true,
            shape: Some(Shape::new(shape)),
        }],
        notes: vec![],
        project: Some(Project {
            model,
            original: Arc::new(Vec::new()),
        }),
    })
}

/// Crashes the engine process for real, so the isolation can be drilled.
#[napi]
pub fn crash() {
    ffi::crash();
}

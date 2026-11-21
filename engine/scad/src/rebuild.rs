//! Rebuilding a model as the Python app's `Rebuilder` does (`simplecad/core/rebuild.py`):
//! demand each final body, take a step's saved result when its signature still
//! matches, and run only the steps that have none or were edited. A step that
//! fails keeps the last good geometry for itself and everything after it.
//!
//! Each step type is ported in turn (`execute`). One not ported yet is only ever
//! taken from its saved result; when it would have to run, it fails in words and
//! nothing downstream of it changes.

use crate::document::Model;
use crate::evaluate::{Dimension, evaluate, resolve};
use crate::graph::output_id;
use crate::project::CACHE_VERSION;
use crate::signatures::{signatures, upstream};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs::File;
use std::io::{Cursor, Read};
use std::path::Path;
use std::rc::Rc;
use std::sync::Arc;

/// A shape, shared between the steps and bodies that hold it.
pub type Shape = Rc<occt::UniquePtr<occt::Shape>>;

/// A step's output: the shape, and its blob's sha when it is stored as one.
#[derive(Clone)]
struct Part {
    value: Geometry,
    sha: Option<String>,
}

#[derive(Clone)]
enum Geometry {
    Brep(Shape),
    Sketch(Rc<Value>),
    Datum(Rc<Value>),
}

impl Part {
    fn shape(&self) -> Option<&Shape> {
        match &self.value {
            Geometry::Brep(shape) => Some(shape),
            Geometry::Sketch(_) | Geometry::Datum(_) => None,
        }
    }
}

type Outputs = BTreeMap<String, Part>;

#[derive(Default)]
struct BoundInputs {
    bodies: HashMap<String, Shape>,
    sketches: HashMap<String, Rc<Value>>,
}

/// Why a step has no result: `CadError`, or `ReferenceLost` when `lost`.
#[derive(Clone, Debug)]
pub struct StepError {
    pub message: String,
    pub lost: bool,
}

pub(crate) fn failed(message: impl Into<String>) -> StepError {
    StepError {
        message: message.into(),
        lost: false,
    }
}

fn lost(message: impl Into<String>) -> StepError {
    StepError {
        message: message.into(),
        lost: true,
    }
}

pub(crate) fn kernel(e: occt::Exception) -> StepError {
    failed(e.what())
}

/// A final body as the rebuild settles it: name, shape and producing step.
type Final = (String, Option<Shape>, String);

/// A body after a rebuild.
pub struct Body {
    pub id: String,
    pub name: String,
    pub shape: Option<Shape>,
}

pub struct Report {
    /// The bodies, in the order the Python app then holds them.
    pub bodies: Vec<Body>,
    /// Each failed step's message, by step id.
    pub failures: BTreeMap<String, String>,
    pub rebuilt: Vec<String>,
}

/// What a step reads when it runs (`BuildContext`).
pub struct Context<'a> {
    feature: &'a Value,
    features: Option<&'a HashMap<String, Value>>,
    bodies: HashMap<String, Shape>,
    sketches: HashMap<String, Rc<Value>>,
    parameters: &'a HashMap<String, f64>,
    /// What the step says about its result (`feature.message`).
    message: std::cell::RefCell<String>,
}

impl Context<'_> {
    pub(crate) fn placement<'a>(
        feature: &'a Value,
        name: &str,
        body: Shape,
        parameters: &'a HashMap<String, f64>,
    ) -> Context<'a> {
        Context {
            feature,
            features: None,
            bodies: HashMap::from([(name.into(), body)]),
            sketches: HashMap::new(),
            parameters,
            message: Default::default(),
        }
    }

    pub(crate) fn input(&self, key: &str) -> Option<&Value> {
        self.feature["inputs"].get(key)
    }

    pub(crate) fn sketch(&self, key: &str) -> Result<&Value, StepError> {
        let name = self
            .input(key)
            .and_then(Value::as_str)
            .filter(|name| !name.is_empty())
            .ok_or_else(|| failed("This feature has no sketch. Select a sketch first."))?;
        self.named_sketch(name)
    }

    pub(crate) fn named_sketch(&self, name: &str) -> Result<&Value, StepError> {
        self.sketches
            .get(&format!("__sketch__{name}"))
            .map(Rc::as_ref)
            .ok_or_else(|| {
                lost(format!(
                    "The sketch '{name}' this feature needs is unavailable."
                ))
            })
    }

    pub(crate) fn set_message(&self, message: String) {
        *self.message.borrow_mut() = message;
    }

    pub(crate) fn into_message(self) -> String {
        self.message.into_inner()
    }

    /// `ctx.shape`: the body an input names.
    pub fn shape(&self, key: &str) -> Result<Shape, StepError> {
        let name = match self.input(key) {
            Some(Value::Object(map)) => map
                .get("__body__")
                .and_then(Value::as_str)
                .map(String::from),
            Some(Value::String(s)) if !s.is_empty() => Some(s.clone()),
            _ => None,
        }
        .ok_or_else(|| {
            failed(
                "This feature is missing one of its inputs. Re-select the body it should act on.",
            )
        })?;
        self.named_shape(&name)
    }

    pub(crate) fn named_shape(&self, name: &str) -> Result<Shape, StepError> {
        self.bodies.get(name).cloned().ok_or_else(|| {
            failed(format!("The body '{name}' this feature needs is not available. It may have been deleted. Re-select the body."))
        })
    }

    /// `ctx.value`: a number input, which may be an expression, in mm or degrees.
    pub fn value(&self, key: &str, default: f64, dimension: Dimension) -> Result<f64, StepError> {
        self.number(self.input(key), default, dimension)
    }

    pub(crate) fn number(
        &self,
        input: Option<&Value>,
        default: f64,
        dimension: Dimension,
    ) -> Result<f64, StepError> {
        match input {
            None => Ok(default),
            Some(Value::Number(n)) => Ok(n.as_f64().unwrap_or(default)),
            Some(Value::Bool(b)) => Ok(f64::from(u8::from(*b))),
            Some(Value::String(text)) => {
                evaluate(text, self.parameters, dimension).map_err(|e| failed(e.to_string()))
            }
            Some(other) => evaluate(&other.to_string(), self.parameters, dimension)
                .map_err(|e| failed(e.to_string())),
        }
    }

    pub(crate) fn ancestors(&self) -> Vec<&Value> {
        let Some(features) = self.features else {
            return Vec::new();
        };
        let mut seen = BTreeSet::new();
        let id = self.feature["id"].as_str().unwrap_or("");
        let mut pending: Vec<_> = upstream(self.feature, id).into_iter().collect();
        let mut result = Vec::new();
        while let Some(id) = pending.pop() {
            if seen.insert(id.clone())
                && let Some(feature) = features.get(&id)
            {
                pending.extend(upstream(feature, &id));
                result.push(feature);
            }
        }
        result
    }

    /// `ctx.resolve` of a list of face references: each one's body and index.
    pub fn faces(&self, key: &str) -> Result<Vec<(Shape, u32)>, StepError> {
        let refs = match self.input(key) {
            Some(Value::Array(list)) => list.clone(),
            Some(one) => vec![one.clone()],
            None => {
                return Err(failed(
                    "This feature has lost the geometry it was built on. Re-select the face or edge for this feature.",
                ));
            }
        };
        refs.iter()
            .map(|r| {
                let reference = r.get("__ref__").ok_or_else(|| failed("This feature has lost the geometry it was built on."))?;
                let body = reference["body"].as_str().unwrap_or("");
                let shape = self.bodies.get(body).cloned().ok_or_else(|| {
                    failed(format!("The body '{body}' this feature needs is not available. It may have been deleted or renamed."))
                })?;
                let index = resolve_face(reference, &shape)?;
                Ok((shape, index))
            })
            .collect()
    }

    /// `ctx.resolve` of a face reference: its body, and the face's index there.
    pub fn face(&self, key: &str) -> Result<(Shape, u32), StepError> {
        let (reference, shape) = self.referenced_shape(self.input(key))?;
        let index = resolve_face(reference, &shape)?;
        Ok((shape, index))
    }

    fn referenced_shape<'a>(
        &self,
        input: Option<&'a Value>,
    ) -> Result<(&'a Value, Shape), StepError> {
        let reference = input.and_then(|v| v.get("__ref__")).ok_or_else(|| {
            failed("This feature has lost the geometry it was built on. Re-select the face or edge for this feature.")
        })?;
        let body = reference["body"].as_str().unwrap_or("");
        let shape = self.bodies.get(body).cloned().ok_or_else(|| {
            failed(format!("The body '{body}' this feature needs is not available. It may have been deleted or renamed."))
        })?;
        Ok((reference, shape))
    }

    pub(crate) fn edge_axis(&self, key: &str) -> Result<occt::EdgeAxis, StepError> {
        let (reference, shape) = self.referenced_shape(self.input(key))?;
        let edges = occt::edge_fingerprints(&shape).map_err(kernel)?;
        let index = resolve_edge(reference, &edges)?;
        occt::edge_axis(&shape, index).map_err(kernel)
    }

    pub(crate) fn vertices(&self, key: &str) -> Result<Vec<[f64; 3]>, StepError> {
        let input = self
            .input(key)
            .ok_or_else(|| failed("A plane needs three points."))?;
        let references = input
            .as_array()
            .map_or_else(|| std::slice::from_ref(input), Vec::as_slice);
        references
            .iter()
            .take(3)
            .map(|input| {
                let (reference, shape) = self.referenced_shape(Some(input))?;
                let vertices = occt::vertex_fingerprints(&shape).map_err(kernel)?;
                let scores: Vec<_> = vertices
                    .iter()
                    .map(|vertex| similarity(&reference["fingerprint"], vertex, "vertex", None))
                    .collect();
                let index = resolve_scores(reference, &scores, "vertex")?;
                Ok(vertices[index as usize].center)
            })
            .collect()
    }
}

/// A body alias as `str()` of a decoded input gives it.
pub(crate) fn alias(value: &Value) -> String {
    match value {
        Value::Object(map) => map
            .get("__body__")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// `naming.similarity` for saved sub-shape references.
fn similarity(
    left: &Value,
    right: &occt::FaceFingerprint,
    kind: &str,
    neighbours: Option<f64>,
) -> f64 {
    let closeness = |a: f64, b: f64, scale: f64| 1.0 / (1.0 + (a - b).abs() / scale.max(1e-9));
    if left["kind"] != kind || left["geometry"].as_str() != Some(right.geometry.as_str()) {
        return 0.0;
    }
    let center: Vec<f64> = left["center"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_f64)
        .collect();
    if center.len() != 3 {
        return 0.0;
    }
    let distance = (0..3)
        .map(|k| (center[k] - right.center[k]).powi(2))
        .sum::<f64>()
        .sqrt();
    let measure = left["measure"].as_f64().unwrap_or(0.0);
    let scale = measure.max(right.measure).max(1.0).sqrt().max(1.0);
    let mut scores = vec![(closeness(distance, 0.0, scale), 3.0)];
    let biggest = measure.abs().max(right.measure.abs());
    let ratio = if biggest > 1e-12 {
        measure.abs().min(right.measure.abs()) / biggest
    } else {
        1.0
    };
    scores.push((ratio, 3.0));
    let direction: Vec<f64> = left["direction"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_f64)
        .collect();
    if direction.len() == 3 && right.has_direction {
        let dot: f64 = (0..3).map(|k| direction[k] * right.direction[k]).sum();
        // An edge's curve can be reparameterized in the opposite direction
        // while its geometry stays put. Face normals still carry orientation.
        let dot = if kind == "edge" { dot.abs() } else { dot };
        let aligned = ((dot + 1.0) / 2.0).clamp(0.0, 1.0);
        scores.push((aligned * aligned, 2.0));
    }
    if let (Some(radius), true) = (left["radius"].as_f64(), right.has_radius) {
        scores.push((closeness(radius, right.radius, radius.max(1.0)), 1.5));
    }
    if let Some(score) = neighbours {
        scores.push((score, 2.0));
    }
    let weight: f64 = scores.iter().map(|(_, w)| w).sum();
    scores.iter().map(|(s, w)| s * w).sum::<f64>() / weight
}

/// `naming.resolve` for a face: the hinted face when it is certainly the one,
/// else the one best match, never a doubtful one.
fn resolve_face(reference: &Value, shape: &Shape) -> Result<u32, StepError> {
    let count = occt::faces(shape).map_err(kernel)?.len() as u32;
    let fingerprint = &reference["fingerprint"];
    let scores: Vec<f64> = (0..count)
        .map(|i| {
            occt::face_fingerprint(shape, i)
                .map(|f| similarity(fingerprint, &f, "face", None))
                .map_err(kernel)
        })
        .collect::<Result<_, _>>()?;
    resolve_scores(reference, &scores, "face")
}

fn resolve_edge(reference: &Value, edges: &[occt::EdgeFingerprint]) -> Result<u32, StepError> {
    let fingerprint = &reference["fingerprint"];
    let neighbours: Vec<&str> = fingerprint["neighbours"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    let summary = |face: &occt::FaceFingerprint| {
        if !face.has_direction {
            return face.geometry.clone();
        }
        let [x, y, z] = face
            .direction
            .map(|v| (v * 1000.0).round_ties_even() / 1000.0 + 0.0);
        format!("{}:{x:?},{y:?},{z:?}", face.geometry)
    };
    let scores: Vec<_> = edges
        .iter()
        .map(|edge| {
            let neighbour_score =
                (!neighbours.is_empty() && !edge.neighbours.is_empty()).then(|| {
                    let right: HashSet<_> = edge.neighbours.iter().map(summary).collect();
                    let shared: HashSet<_> =
                        neighbours.iter().filter(|s| right.contains(**s)).collect();
                    shared.len() as f64 / neighbours.len().max(edge.neighbours.len()) as f64
                });
            similarity(fingerprint, &edge.curve, "edge", neighbour_score)
        })
        .collect();
    resolve_scores(reference, &scores, "edge")
}

fn resolve_scores(reference: &Value, scores: &[f64], kind: &str) -> Result<u32, StepError> {
    const MATCH_THRESHOLD: f64 = 0.55;
    const CERTAIN_SCORE: f64 = 0.999;
    if scores.is_empty() {
        return Err(lost(format!(
            "The {kind} this feature depends on no longer exists. Re-select the geometry for this feature."
        )));
    }
    let hint = reference["index_hint"].as_i64().unwrap_or(-1);
    if (0..scores.len() as i64).contains(&hint) {
        let hint = hint as usize;
        if scores[hint] >= CERTAIN_SCORE
            && !scores
                .iter()
                .enumerate()
                .any(|(i, s)| i != hint && *s >= CERTAIN_SCORE)
        {
            return Ok(hint as u32);
        }
    }
    let (mut best, mut best_score, mut runner_up) = (None, 0.0, 0.0);
    for (i, &score) in scores.iter().enumerate() {
        if score > best_score {
            (best, runner_up, best_score) = (Some(i), best_score, score);
        } else if score > runner_up {
            runner_up = score;
        }
    }
    match best {
        Some(i) if best_score >= MATCH_THRESHOLD && best_score - runner_up > 1e-6 => Ok(i as u32),
        _ => Err(lost(format!(
            "The {kind} this feature was built on can no longer be found. The shape changed too much. Re-select the {kind} to repair this feature."
        ))),
    }
}

/// The step types `execute` runs.
pub const PORTED: &[&str] = &[
    "box",
    "cylinder",
    "sphere",
    "cone",
    "torus",
    "tube",
    "wedge",
    "polygon_prism",
    "oval",
    "pill",
    "star",
    "heart",
    "cross",
    "crescent",
    "lightning",
    "push_pull",
    "resize_round",
    "move",
    "move_many",
    "scale",
    "fillet",
    "chamfer",
    "edge_finish",
    "shell",
    "align",
    "arrange",
    "vent_plate",
    "vent_cut",
    "boolean",
    "split",
    "hole",
    "thread",
    "matching_bolt",
    "matching_nut",
    "matching_thread",
    "threaded_connection",
    "text",
    "face_texture",
    "section_replace",
    "clip_joint",
    "draft",
    "mirror",
    "rectangular_pattern",
    "circular_pattern",
    "extrude",
    "revolve",
    "sweep",
    "loft",
    "path_pattern",
    "embedded_import",
    "import",
];

/// The Shape panel creates the same recipe that the evaluator can rebuild.
pub fn create_primitive(
    model: &mut Model,
    kind: &str,
    mut inputs: Value,
) -> Result<(String, occt::UniquePtr<occt::Shape>), String> {
    let (kind, label, sides) = match kind {
        "matching_bolt" => (kind, "Bolt", None),
        "matching_nut" => (kind, "Nut", None),
        "box" => (kind, "Box", None),
        "cylinder" => (kind, "Cylinder", None),
        "sphere" => (kind, "Sphere", None),
        "cone" => (kind, "Cone", None),
        "torus" => (kind, "Torus", None),
        "tube" => (kind, "Tube", None),
        "wedge" => (kind, "Wedge", None),
        "oval" => (kind, "Oval", None),
        "pill" => (kind, "Pill", None),
        "polygon_prism" => (kind, "Polygon", None),
        "star" => (kind, "Star", None),
        "heart" => (kind, "Heart", None),
        "cross" => (kind, "Cross", None),
        "crescent" => (kind, "Crescent", None),
        "lightning" => (kind, "Lightning", None),
        "triangle" => ("polygon_prism", "Triangle", Some(3)),
        "pentagon" => ("polygon_prism", "Pentagon", Some(5)),
        "hexagon" => ("polygon_prism", "Hexagon", Some(6)),
        "octagon" => ("polygon_prism", "Octagon", Some(8)),
        _ => return Err("Choose a shape to create.".into()),
    };
    if !inputs.is_object() {
        return Err("The shape dimensions could not be read.".into());
    }
    if let Some(sides) = sides {
        inputs["sides"] = json!(sides);
    }
    if matches!(kind, "matching_bolt" | "matching_nut") {
        inputs["thread_modelled"] = json!(true);
        inputs["thread_internal"] = json!(kind == "matching_nut");
        inputs["left_hand"] = json!(inputs["left_hand"] == "true");
        if let Some(clearance) = inputs["clearance"]
            .as_str()
            .and_then(|text| text.parse::<f64>().ok())
            .filter(|value| value.is_finite())
        {
            inputs["clearance"] = json!(clearance);
        }
    }
    let (parameters, _) = resolve(
        model.payload["parameters"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or(&[]),
    );
    let feature = json!({ "inputs": inputs, "name": label });
    let ctx = Context {
        feature: &feature,
        features: None,
        bodies: HashMap::new(),
        sketches: HashMap::new(),
        parameters: &parameters,
        message: Default::default(),
    };
    let shape = execute(kind, &ctx, None)
        .ok_or("This shape cannot be created yet.")?
        .map_err(|e| e.message)?
        .remove(label)
        .and_then(|shape| Rc::try_unwrap(shape).ok())
        .ok_or("This shape produced no geometry.")?;
    let brep = occt::write_brep(&shape).map_err(|e| e.what().to_string())?;
    let name = model.add_primitive(kind, label, inputs, brep)?;
    Ok((name, shape))
}

pub struct FaceTarget<'a> {
    pub name: &'a str,
    pub shape: &'a occt::Shape,
}

/// Build and record an operation picked on a viewport face.
pub fn create_face_feature(
    model: &mut Model,
    picked: crate::align::PickedFace<'_>,
    kind: &str,
    mut inputs: Value,
    target: Option<FaceTarget<'_>>,
) -> Result<occt::UniquePtr<occt::Shape>, String> {
    let label = match kind {
        "text" => "Text",
        "face_texture" => "Face Texture",
        "hole" => "Hole",
        "thread" => "Thread",
        _ => return Err("Choose a face operation.".into()),
    };
    if !inputs.is_object() {
        return Err("The face settings could not be read.".into());
    }
    let kernel = |error: occt::Exception| error.what().to_owned();
    let mark = occt::face_fingerprint(picked.shape, picked.index).map_err(kernel)?;
    let face = model.face_ref(picked.name, picked.index, json!({
        "kind": "face", "geometry": mark.geometry, "measure": mark.measure, "center": mark.center,
        "direction": mark.has_direction.then_some(mark.direction),
        "radius": mark.has_radius.then_some(mark.radius), "neighbours": [],
    }));
    inputs["body"] = json!({ "__body__": picked.name });
    inputs["face"] = json!({ "__ref__": face });
    if let Some(target) = &target {
        inputs["until"] = json!({ "__body__": target.name });
    }
    let mut feature = json!({ "type": kind, "inputs": inputs });
    let parameters = model.payload["parameters"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or(&[]);
    let (parameters, _) = resolve(parameters);
    let shape = occt::transform_shape(picked.shape, &occt::trsf_translation(&[0.0; 3]), false)
        .map_err(kernel)?;
    let features: HashMap<String, Value> = model.payload["features"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|value| Some((value["id"].as_str()?.into(), value.clone())))
        .collect();
    let mut ctx = Context::placement(&feature, picked.name, Rc::new(shape), &parameters);
    ctx.features = Some(&features);
    if let Some(target) = target {
        let shape = occt::transform_shape(target.shape, &occt::trsf_translation(&[0.0; 3]), false)
            .map_err(kernel)?;
        ctx.bodies.insert(target.name.to_owned(), Rc::new(shape));
    }
    let mut outputs = execute(kind, &ctx, None)
        .ok_or("This face operation is unavailable.")?
        .map_err(|error| error.message)?;
    let geometry = outputs
        .remove(picked.name)
        .and_then(|shape| Rc::try_unwrap(shape).ok())
        .ok_or("The face operation produced no geometry.")?;
    let brep = occt::write_brep(&geometry).map_err(kernel)?;
    let threaded_hole =
        kind == "hole" && ctx.input("style").and_then(Value::as_str) == Some("threaded");
    let thread_metadata = if threaded_hole || kind == "thread" {
        let (diameter, internal) = if threaded_hole {
            (
                ctx.value("diameter", 6.0, Dimension::Length)
                    .map_err(|error| error.message)?,
                true,
            )
        } else {
            let face = occt::sub_face(picked.shape, picked.index).map_err(kernel)?;
            let shaft = occt::cylinder_data(&face).map_err(kernel)?;
            (shaft.radius * 2.0, shaft.internal)
        };
        let thread =
            crate::thread::Thread::read(&ctx, diameter, internal).map_err(|error| error.message)?;
        Some((thread.designation().to_owned(), internal))
    } else {
        None
    };
    let message = ctx.into_message();
    if let Some((designation, internal)) = thread_metadata {
        feature["inputs"]["designation"] = json!(designation);
        feature["inputs"]["thread_internal"] = json!(internal);
        feature["inputs"]["thread_modelled"] = json!(true);
    }
    model.record_body_feature(picked.name, feature, label, brep, &message)?;
    Ok(geometry)
}

fn move_geometry(
    ctx: &Context,
    body: &occt::Shape,
) -> Result<occt::UniquePtr<occt::Shape>, StepError> {
    let numbers = |key: &str| -> Option<Vec<f64>> {
        ctx.input(key)
            .and_then(Value::as_array)
            .map(|l| l.iter().map(|v| v.as_f64().unwrap_or(f64::NAN)).collect())
    };
    let axis = match ctx.input("axis") {
        None | Some(Value::Null) => None,
        Some(_) => {
            let axis = numbers("axis").unwrap_or_default();
            if axis.len() != 3 || axis.iter().map(|v| v * v).sum::<f64>().sqrt() < 1e-9 {
                return Err(failed("This rotation has no axis to turn about."));
            }
            Some(axis)
        }
    };
    let pivot = match numbers("pivot").filter(|p| !p.is_empty()) {
        Some(pivot) => pivot,
        None => {
            // Turned about the body's own centre.
            let b = occt::bounds(body).map_err(kernel)?;
            (0..3).map(|k| (b[k] + b[k + 3]) / 2.0).collect()
        }
    };
    let mut m = Vec::with_capacity(15);
    for key in ["dx", "dy", "dz"] {
        m.push(ctx.value(key, 0.0, Dimension::Length)?);
    }
    for key in ["rx", "ry", "rz"] {
        m.push(ctx.value(key, 0.0, Dimension::Angle)?);
    }
    m.extend(pivot.iter().take(3));
    let order = ctx.input("order").map(|o| {
        o.as_str()
            .map(String::from)
            .unwrap_or_else(|| o.to_string())
    });
    m.push(f64::from(u8::from(order.as_deref() == Some("xyz"))));
    match axis {
        Some(axis) => {
            m.push(1.0);
            m.extend(axis);
            m.push(ctx.value("angle", 0.0, Dimension::Angle)?);
        }
        None => m.extend([0.0; 5]),
    }
    occt::move_shape(body, &m).map_err(kernel)
}

/// Evaluate a desktop transform with the same recipe used to rebuild saved moves.
pub fn create_transform(
    model: &mut Model,
    body_name: &str,
    shape: &occt::Shape,
    label: &str,
    mut inputs: Value,
) -> Result<occt::UniquePtr<occt::Shape>, String> {
    inputs["body"] = json!({ "__body__": body_name });
    let feature = json!({ "type": "move", "inputs": inputs });
    let (parameters, _) = resolve(
        model.payload["parameters"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or(&[]),
    );
    let ctx = Context {
        feature: &feature,
        features: None,
        bodies: HashMap::new(),
        sketches: HashMap::new(),
        parameters: &parameters,
        message: Default::default(),
    };
    let moved = move_geometry(&ctx, shape).map_err(|error| error.message)?;
    let brep = occt::write_brep(&moved).map_err(|error| error.what().to_owned())?;
    model.record_body_feature(body_name, feature, label, brep, "")?;
    Ok(moved)
}

/// The steps this build can run, by type; `None` for the others.
fn execute(
    kind: &str,
    ctx: &Context,
    store: Option<&mut Store<'_>>,
) -> Option<Result<BTreeMap<String, Shape>, StepError>> {
    let name = || -> Result<String, StepError> {
        let outputs = ctx.feature["outputs"].as_array();
        let first = outputs.and_then(|o| o.first()).and_then(Value::as_str);
        Ok(first
            .or(ctx.feature["name"].as_str())
            .unwrap_or("")
            .to_string())
    };
    let body_name = || -> Result<String, StepError> {
        match ctx.input("body") {
            Some(Value::Object(map)) => map
                .get("__body__")
                .and_then(Value::as_str)
                .map(String::from),
            Some(Value::String(s)) if !s.is_empty() => Some(s.clone()),
            _ => None,
        }
        .ok_or_else(|| failed("This feature has no body to work on."))
    };
    let run = || -> Result<BTreeMap<String, Shape>, StepError> {
        match kind {
            "extrude" | "revolve" | "sweep" | "loft" => {
                let shape = crate::sketch::modeling::build(kind, ctx)?;
                Ok(BTreeMap::from([(name()?, Rc::new(shape))]))
            }
            "import" => {
                let path = ctx
                    .input("path")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .ok_or_else(|| failed("This import has no file to read."))?;
                let index = ctx.input("index").and_then(Value::as_u64).unwrap_or(0);
                let parts = store
                    .ok_or_else(|| failed("The import source is unavailable."))?
                    .import_source(path)?;
                let index = usize::try_from(index).unwrap_or(usize::MAX);
                let body = parts.get(index).ok_or_else(|| {
                    failed(format!(
                        "'{}' no longer has a body {} — it now holds {}.",
                        source_name(path),
                        index.saturating_add(1),
                        parts.len()
                    ))
                })?;
                Ok(BTreeMap::from([(name()?, import_offset(ctx, body)?)]))
            }
            "embedded_import" => {
                if let Some(note) = ctx.input("mesh_note").and_then(Value::as_str) {
                    ctx.set_message(note.into());
                }
                let asset = ctx.input("asset").and_then(Value::as_str).unwrap_or("");
                let feature = ctx.feature["name"].as_str().unwrap_or("");
                let blob = store.and_then(|store| store.model_blobs.get(asset).cloned()
                    .or_else(|| store.archive_blob("assets", asset))).ok_or_else(|| {
                    failed(format!("The embedded geometry for '{feature}' is missing. Restore the project from a complete .scad3 file."))
                })?;
                let shape = occt::read_brep(&blob).map_err(|_| {
                    failed(format!("The embedded geometry for '{feature}' is damaged."))
                })?;
                Ok(BTreeMap::from([(
                    name()?,
                    import_offset(ctx, &Rc::new(shape))?,
                )]))
            }
            "box" | "cylinder" | "sphere" | "cone" | "torus" | "tube" | "wedge" => {
                // `_Primitive._positive`: the input, refused unless above zero.
                let positive = |key: &str, default: f64, what: &str| -> Result<f64, StepError> {
                    let value = ctx.value(key, default, Dimension::Length)?;
                    if value <= 0.0 {
                        return Err(failed(format!(
                            "The {what} must be greater than zero. Enter a positive {what}."
                        )));
                    }
                    Ok(value)
                };
                let value = |key: &str, default: f64| ctx.value(key, default, Dimension::Length);
                let shape = match kind {
                    "box" => occt::make_box(positive("width", 40.0, "width")?, positive("depth", 30.0, "depth")?, positive("height", 20.0, "height")?),
                    "cylinder" => occt::make_cylinder(positive("radius", 12.0, "radius")?, positive("height", 30.0, "height")?),
                    "sphere" => occt::make_sphere(positive("radius", 15.0, "radius")?),
                    "cone" => {
                        let (bottom, top) = (value("bottom_radius", 15.0)?, value("top_radius", 0.0)?);
                        let height = positive("height", 30.0, "height")?;
                        if bottom < 0.0 || top < 0.0 || (bottom == 0.0 && top == 0.0) {
                            return Err(failed("A cone needs at least one radius greater than zero. Set the bottom or top radius above zero."));
                        }
                        occt::make_cone(bottom, top, height)
                    }
                    "torus" => {
                        let major = positive("major_radius", 25.0, "ring radius")?;
                        let minor = positive("minor_radius", 6.0, "tube radius")?;
                        if minor >= major {
                            return Err(failed(format!("The tube is too thick for this ring radius. Use a tube radius below {major:.2} mm.")));
                        }
                        occt::make_torus(major, minor)
                    }
                    "tube" => {
                        let outer = positive("outer_radius", 15.0, "outer radius")?;
                        let inner = positive("inner_radius", 10.0, "inner radius")?;
                        let height = positive("height", 30.0, "height")?;
                        if inner >= outer {
                            return Err(failed(format!("The bore is as wide as the tube. Use an inner radius below {outer:.2} mm.")));
                        }
                        occt::make_tube(outer, inner, height)
                    }
                    _ => {
                        let (w, d, h) = (positive("width", 40.0, "width")?, positive("depth", 30.0, "depth")?, positive("height", 20.0, "height")?);
                        occt::make_wedge(w, h, d, value("top_width", 0.0)?.max(0.0))
                    }
                }
                .map_err(kernel)?;
                Ok(BTreeMap::from([(name()?, Rc::new(placed(shape, ctx)?))]))
            }
            "polygon_prism" | "oval" | "pill" | "star" | "heart" | "cross" | "crescent"
            | "lightning" => {
                let shape = crate::profiles::profile(kind, ctx)?;
                Ok(BTreeMap::from([(name()?, Rc::new(placed(shape, ctx)?))]))
            }
            "move" => {
                let body = ctx.shape("body")?;
                let moved = move_geometry(ctx, &body)?;
                Ok(BTreeMap::from([(body_name()?, Rc::new(moved))]))
            }
            "move_many" => {
                let names: Vec<String> = ctx
                    .input("bodies")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .map(alias)
                    .collect();
                if names.is_empty() {
                    return Err(failed("Move needs at least one body."));
                }
                let mut m = Vec::with_capacity(15);
                for key in ["dx", "dy", "dz"] {
                    m.push(ctx.value(key, 0.0, Dimension::Length)?);
                }
                m.extend([0.0; 12]);
                let mut out = BTreeMap::new();
                for name in names {
                    let shape = ctx.bodies.get(&name).cloned().ok_or_else(|| {
                        failed(format!("The body '{name}' this feature needs is not available. It may have been deleted. Re-select the body."))
                    })?;
                    out.insert(name, Rc::new(occt::move_shape(&shape, &m).map_err(kernel)?));
                }
                Ok(out)
            }
            "scale" => {
                let body = ctx.shape("body")?;
                let value = |key, default| ctx.value(key, default, Dimension::Scalar);
                let uniform = value("factor", 1.0)?;
                let factors = [
                    value("sx", uniform)?,
                    value("sy", uniform)?,
                    value("sz", uniform)?,
                ];
                if factors.iter().any(|f| !f.is_finite() || *f <= 0.0) {
                    return Err(failed(
                        "A scale factor has to be greater than zero. Use 0.5 to halve the part, or 2 to double it.",
                    ));
                }
                if factors.iter().all(|f| (f - 1.0).abs() < 1e-12) {
                    return Ok(BTreeMap::from([(body_name()?, body)]));
                }
                let pivot = if let Some(values) = ctx
                    .input("pivot")
                    .and_then(Value::as_array)
                    .filter(|p| !p.is_empty())
                {
                    let coordinates: Vec<_> = values.iter().filter_map(Value::as_f64).collect();
                    <[f64; 3]>::try_from(coordinates)
                        .map_err(|_| failed("The scale pivot needs three coordinates."))?
                } else {
                    let bounds = occt::bounds(&body).map_err(kernel)?;
                    std::array::from_fn(|i| (bounds[i] + bounds[i + 3]) / 2.0)
                };
                let rounded = factors.map(|factor| (factor * 1e12).round_ties_even());
                let uniform = rounded[0] == rounded[1] && rounded[1] == rounded[2];
                let scaled = occt::scale_shape(&body, &pivot, &factors, uniform).map_err(kernel)?;
                Ok(BTreeMap::from([(body_name()?, Rc::new(scaled))]))
            }
            "fillet" | "chamfer" => {
                let body = ctx.shape("body")?;
                let fillet = kind == "fillet";
                let (key, default) = if fillet {
                    ("radius", 2.0)
                } else {
                    ("distance", 1.0)
                };
                let size = ctx.value(key, default, Dimension::Length)?;
                if size <= 0.0 {
                    return Err(failed(format!("A {kind} needs a {key} greater than zero.")));
                }
                let refs = match ctx.input("edges") {
                    Some(Value::Array(list)) => list.as_slice(),
                    Some(one) => std::slice::from_ref(one),
                    None => &[],
                };
                if refs.is_empty() {
                    return Err(failed(format!("No edges are selected for this {kind}.")));
                }
                let edges = occt::edge_fingerprints(&body).map_err(kernel)?;
                let indices = refs
                    .iter()
                    .map(|value| {
                        let reference = value.get("__ref__").ok_or_else(|| {
                            failed("This feature has lost the geometry it was built on.")
                        })?;
                        resolve_edge(reference, &edges)
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                let finished = occt::finish_edges(&body, &indices, size, fillet).map_err(kernel)?;
                Ok(BTreeMap::from([(body_name()?, Rc::new(finished))]))
            }
            "edge_finish" => Ok(BTreeMap::from([(body_name()?, edge_finish(ctx)?)])),
            "vent_plate" => Ok(BTreeMap::from([(name()?, crate::vent::build(ctx, false)?)])),
            "vent_cut" => Ok(BTreeMap::from([(
                body_name()?,
                crate::vent::build(ctx, true)?,
            )])),
            "shell" => {
                let body = ctx.shape("body")?;
                let thickness = ctx.value("thickness", 2.0, Dimension::Length)?;
                if thickness <= 0.0 {
                    return Err(failed("A shell needs a wall thickness greater than zero."));
                }
                let faces = ctx.faces("faces")?;
                if faces.is_empty() {
                    return Err(failed(
                        "Select the face or faces to open before shelling. Pick the face that should become the opening.",
                    ));
                }
                let b = occt::bounds(&body).map_err(kernel)?;
                let limit = (0..3)
                    .map(|k| b[k + 3] - b[k])
                    .fold(f64::INFINITY, f64::min)
                    / 2.0;
                if thickness >= limit {
                    return Err(failed(format!(
                        "A {thickness:.2} mm wall is too thick to hollow this body. Use a wall below {limit:.2} mm."
                    )));
                }
                let indices: Vec<u32> = faces.iter().map(|(_, i)| *i).collect();
                match occt::shell(&body, &indices, thickness) {
                    Ok(hollow) => Ok(BTreeMap::from([(body_name()?, Rc::new(hollow))])),
                    // ponytail: the Python app then hollows a single flat profile by a 2-D
                    // offset (shapely); that comes with the sketcher's offsets (phase 7).
                    Err(_) if indices.len() == 1 => Err(failed(
                        "This shell needs a construction this version cannot build yet. Edit it in SimpleCAD 0.4.",
                    )),
                    Err(_) => Err(failed(
                        "The selected faces did not produce a hollow body. Try opening one flat face or using a thinner wall.",
                    )),
                }
            }
            "push_pull" => {
                let body = ctx.shape("body")?;
                let distance = ctx.value("distance", 5.0, Dimension::Length)?;
                let (face_body, face) = ctx.face("face")?;
                // The reference names the body as it was given to this step.
                let shape = if Rc::ptr_eq(&face_body, &body) {
                    body
                } else {
                    face_body
                };
                let pulled = occt::push_pull(&shape, face, distance).map_err(kernel)?;
                Ok(BTreeMap::from([(body_name()?, Rc::new(pulled))]))
            }
            "resize_round" => {
                let body = ctx.shape("body")?;
                let delta = ctx.value("delta", 0.0, Dimension::Length)?;
                let (_, face) = ctx.face("face")?;
                let resized = occt::resize_round(&body, face, delta).map_err(kernel)?;
                Ok(BTreeMap::from([(body_name()?, Rc::new(resized))]))
            }
            "align" => {
                let (name, placed) = crate::align::execute(ctx)?;
                Ok(BTreeMap::from([(name, placed)]))
            }
            "arrange" => crate::advanced::arrange_row(ctx),
            "hole" => Ok(BTreeMap::from([(body_name()?, drill(ctx)?)])),
            "thread" | "matching_thread" => Ok(BTreeMap::from([(
                body_name()?,
                crate::thread::build(ctx, kind == "matching_thread")?,
            )])),
            "matching_bolt" | "matching_nut" => Ok(BTreeMap::from([(
                name()?,
                crate::thread::fasteners::build(kind, ctx)?,
            )])),
            "threaded_connection" => crate::thread::fasteners::connection(ctx),
            "text" => Ok(BTreeMap::from([(body_name()?, crate::text::build(ctx)?)])),
            "face_texture" => {
                let name = body_name()?;
                let shape = crate::texture::build(ctx, &name)?;
                Ok(BTreeMap::from([(name, shape)]))
            }
            "section_replace" => {
                let (target, result) = crate::section_replace::build(ctx)?;
                Ok(BTreeMap::from([(target, result)]))
            }
            "clip_joint" => crate::clip::build(ctx),
            "draft" | "mirror" | "rectangular_pattern" | "circular_pattern" | "path_pattern" => Ok(
                BTreeMap::from([(body_name()?, crate::advanced::build(kind, ctx)?)]),
            ),
            "boolean" => combine(ctx),
            "split" => {
                let coordinates = |key, default| -> Result<[f64; 3], StepError> {
                    let Some(items) = ctx
                        .input(key)
                        .and_then(Value::as_array)
                        .filter(|items| !items.is_empty())
                    else {
                        return Ok(default);
                    };
                    let values: Vec<_> = items.iter().filter_map(Value::as_f64).collect();
                    values.try_into().map_err(|_| {
                        failed(format!("The split plane {key} needs three coordinates."))
                    })
                };
                let body = ctx.shape("body")?;
                let origin = coordinates("origin", [0.0; 3])?;
                let direction = coordinates("normal", [1.0, 0.0, 0.0])?;
                let norm = direction.iter().map(|v| v * v).sum::<f64>().sqrt();
                if norm < 1e-12 || !norm.is_finite() {
                    return Err(failed(
                        "The split plane has no direction. Choose X, Y, Z or a flat face to cut along.",
                    ));
                }
                let normal = direction.map(|v| v / norm);
                let position = ctx.value("position", 0.0, Dimension::Length)?;
                let point: [f64; 3] = std::array::from_fn(|i| origin[i] + normal[i] * position);
                let source = body_name()?;
                let names = ctx.feature["outputs"]
                    .as_array()
                    .filter(|names| names.len() == 2)
                    .or_else(|| {
                        ctx.input("names")
                            .and_then(Value::as_array)
                            .filter(|names| names.len() == 2)
                    });
                let (first, second) = names.map_or_else(
                    || (format!("{source} A"), format!("{source} B")),
                    |names| (alias(&names[0]), alias(&names[1])),
                );
                let parts = occt::split_solid(&body, &point, &normal).map_err(kernel)?;
                ctx.set_message(format!("{first} and {second}, cut at {position:.2} mm"));
                Ok(BTreeMap::from([
                    (first, Rc::new(parts.below)),
                    (second, Rc::new(parts.above)),
                ]))
            }
            _ => unreachable!(),
        }
    };
    PORTED.contains(&kind).then(run)
}

fn edge_finish(ctx: &Context) -> Result<Shape, StepError> {
    let mut shape = ctx.shape("body")?;
    let specs = ctx
        .input("edge_specs")
        .and_then(Value::as_array)
        .filter(|specs| !specs.is_empty())
        .ok_or_else(|| failed("This edge finish has no edges."))?;
    for group in specs.chunk_by(|a, b| a["type"] == b["type"]) {
        let kind = group[0]["type"].as_str().unwrap_or("missing");
        let fillet = match kind {
            "fillet" => true,
            "chamfer" => false,
            _ => return Err(failed(format!("Unknown edge finish type: {kind}."))),
        };
        let edges = occt::edge_fingerprints(&shape).map_err(kernel)?;
        let finishes = group
            .iter()
            .map(|spec| {
                let number = |key, default, dimension| {
                    ctx.number(spec.get(key).filter(|v| !v.is_null()), default, dimension)
                };
                let reference = spec["edge"]
                    .get("__ref__")
                    .ok_or_else(|| failed("This feature has lost the geometry it was built on."))?;
                let index = resolve_edge(reference, &edges)?;
                let (key, default) = if fillet {
                    ("radius", 2.0)
                } else {
                    ("distance", 1.0)
                };
                let first = number(key, default, Dimension::Length)?;
                if first <= 0.0 {
                    return Err(failed(format!("A {kind} needs a {key} greater than zero.")));
                }
                let mut finish = occt::EdgeFinishSpec {
                    index,
                    first,
                    mode: 0,
                    second: 0.0,
                    side: -1,
                    flip: false,
                };
                let mode = spec["mode"].as_str().unwrap_or("equal");
                if fillet || mode == "equal" {
                    return Ok(finish);
                }
                if let Some(side) = spec["side"].get("__ref__") {
                    finish.side = i32::try_from(resolve_face(side, &shape)?)
                        .map_err(|_| failed("The chamfer's reference face is missing."))?;
                }
                finish.flip = spec["flip"].as_bool().unwrap_or(false);
                match mode {
                    "two_distances" => {
                        finish.mode = 1;
                        finish.second = number("distance2", first, Dimension::Length)?;
                        if finish.second <= 0.0 {
                            return Err(failed("Both chamfer distances must be positive."));
                        }
                    }
                    "distance_angle" => {
                        finish.mode = 2;
                        let angle = number("angle", 45.0, Dimension::Angle)?;
                        if !(0.0 < angle && angle < 90.0) {
                            return Err(failed(
                                "A chamfer angle must be between 0 and 90 degrees.",
                            ));
                        }
                        finish.second = angle.to_radians();
                    }
                    _ => return Err(failed(format!("Unknown chamfer setting: {mode}."))),
                }
                Ok(finish)
            })
            .collect::<Result<Vec<_>, StepError>>()?;
        shape = Rc::new(occt::finish_edge_specs(&shape, &finishes, fillet).map_err(kernel)?);
    }
    Ok(shape)
}

fn drill(ctx: &Context) -> Result<Shape, StepError> {
    let shape = ctx.shape("body")?;
    let (source, face) = ctx.face("face")?;
    let face = occt::sub_face(&source, face).map_err(kernel)?;
    let plane = occt::plane_frame(&face).map_err(kernel)?;
    if !plane.planar {
        return Err(failed(
            "Holes are placed on flat faces. Select a planar face to drill into.",
        ));
    }
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let diameter = length("diameter", 6.0)?;
    if diameter <= 0.0 {
        return Err(failed("A hole needs a diameter greater than zero."));
    }
    let style = ctx
        .input("style")
        .and_then(Value::as_str)
        .unwrap_or("simple");
    let thread = (style == "threaded")
        .then(|| crate::thread::Thread::read(ctx, diameter, true))
        .transpose()?;
    let diameter = thread
        .as_ref()
        .map_or(diameter, |thread| diameter.max(thread.bore_diameter()));
    let position = if let Some(values) = ctx
        .input("position")
        .and_then(Value::as_array)
        .filter(|v| !v.is_empty())
    {
        let values: Vec<_> = values.iter().filter_map(Value::as_f64).collect();
        <[f64; 3]>::try_from(values)
            .map_err(|_| failed("The drill point needs three coordinates."))?
    } else {
        plane.center
    };
    let inward = plane.normal.map(|n| -n);
    let bounds = occt::bounds(&shape).map_err(kernel)?;
    let span = (0..3)
        .map(|i| (bounds[i + 3] - bounds[i]).powi(2))
        .sum::<f64>()
        .sqrt()
        + 10.0;
    let depth = match ctx
        .input("depth_mode")
        .and_then(Value::as_str)
        .unwrap_or("through")
    {
        "through" => span,
        "to_object" => {
            let name = ctx.input("until").map(alias).unwrap_or_default();
            if let Some(target) = ctx.bodies.get(&name) {
                let hit = occt::first_ray_hit(target, &position, &inward).map_err(kernel)?;
                if hit > 0.0 {
                    hit
                } else {
                    ctx.set_message(format!(
                        "The hole never reaches '{name}', so it goes all the way through."
                    ));
                    span
                }
            } else {
                ctx.set_message(
                    "No body was given to stop at, so the hole goes all the way through.".into(),
                );
                span
            }
        }
        _ => length("depth", 10.0)?,
    };
    let (cap, cap_depth, tapered) = match style {
        "counterbore" => (
            length("counterbore_diameter", diameter * 1.8)?,
            length("counterbore_depth", diameter * 0.6)?,
            false,
        ),
        "countersink" => {
            let angle = ctx.value("countersink_angle", 90.0, Dimension::Angle)?;
            let cap = length("countersink_diameter", diameter * 2.0)?;
            let cap_depth = ((cap - diameter) * 0.5 / (angle * 0.5).to_radians().tan()).max(1e-3);
            (cap, cap_depth, true)
        }
        _ => (0.0, 0.0, false),
    };
    if matches!(style, "counterbore" | "countersink") && (cap <= 0.0 || cap_depth <= 0.0) {
        return Err(failed("The hole's recess needs positive dimensions."));
    }
    let spec = occt::HoleCut {
        position,
        inward,
        diameter,
        depth,
        cap_diameter: cap,
        cap_depth,
        tapered,
    };
    let cut = occt::hole_cut(&shape, &spec).map_err(kernel)?;
    match thread {
        Some(thread) => thread.drill(ctx, &cut, &spec),
        None => Ok(Rc::new(cut)),
    }
}

/// `BooleanFeature`: Join, Cut or Intersect, one target body against one or more tools.
fn combine(ctx: &Context) -> Result<BTreeMap<String, Shape>, StepError> {
    let text = |value: Option<&Value>, default: &str| match value {
        None => default.to_string(),
        Some(v) => alias(v),
    };
    let operation = text(ctx.input("operation"), "join");
    let code = match operation.as_str() {
        "join" => 0,
        "cut" => 1,
        "intersect" => 2,
        _ => return Err(failed(format!("'{operation}' is not a combine operation."))),
    };
    let did_not = || {
        failed(
            "The combine operation did not succeed on this geometry. Try adjusting the inputs slightly, or undo and take a different approach.",
        )
    };
    let name = ctx.input("body").map(alias).ok_or_else(did_not)?;
    // `tool_names`: `tools`, or a project's single `tool` from before several were allowed.
    let tool_names: Vec<String> = match ctx.input("tools") {
        Some(Value::Array(tools)) if !tools.is_empty() => tools.iter().map(alias).collect(),
        _ => match ctx.input("tool") {
            Some(tool) if !matches!(tool, Value::Null) && !alias(tool).is_empty() => {
                vec![alias(tool)]
            }
            _ => Vec::new(),
        },
    };
    if tool_names.is_empty() {
        return Err(failed("Combine needs a second body to work with."));
    }
    let target = ctx.shape("body")?;
    let mut tools = Vec::with_capacity(tool_names.len());
    for tool in &tool_names {
        tools.push(ctx.bodies.get(tool).cloned().ok_or_else(|| {
            failed(format!("The body '{tool}' this feature needs is not available. It may have been deleted. Re-select the body."))
        })?);
    }
    let kernel = |e: occt::Exception| {
        let what = e.what();
        if what.starts_with("The operation could not be completed") {
            failed(what)
        } else {
            failed(format!(
                "The {operation} operation did not succeed on this geometry. Try adjusting the inputs slightly, or undo and take a different approach."
            ))
        }
    };
    let run = |fuzzy: f64| -> Result<occt::UniquePtr<occt::Shape>, StepError> {
        let mut list = occt::shapes_new();
        for tool in &tools {
            occt::shapes_push(list.pin_mut(), tool);
        }
        occt::combine(code, &target, &list, fuzzy).map_err(kernel)
    };
    let valid = |shape: &occt::Shape| occt::is_valid(shape).unwrap_or(false);
    let mut result = run(0.0)?;
    if code == 0 {
        // A Join must make one connected solid: parts that touch, or are within a
        // measured gap of 0.2 mm, are joined with just enough fuzziness to bridge it.
        let mut connected = occt::distinct_solids(&result) == 1;
        let mut checked = false;
        let mut bridged = None;
        if occt::distinct_solids(&result) > 1 {
            let gap = occt::join_bridge_gap(&result);
            if gap < 0.0 {
                return Err(failed(
                    "Join cannot make one connected solid: the parts do not make solid contact within 0.2 mm. Move them closer with Align & Stack, then try Join again.",
                ));
            }
            let fuzzies: Vec<f64> = if gap > JOIN_CONTACT_MM {
                vec![gap + 1.0e-6]
            } else {
                [1.0e-4, 1.0e-3].into_iter().filter(|v| *v <= 0.2).collect()
            };
            for fuzzy in fuzzies {
                let candidate = run(fuzzy)?;
                if occt::distinct_solids(&candidate) == 1 && valid(&candidate) {
                    result = candidate;
                    connected = true;
                    checked = true;
                    bridged = Some(gap);
                    break;
                }
            }
        }
        if !connected || (!checked && !valid(&result)) {
            return Err(failed(
                "Join could not make one valid connected solid. Adjust the parts so their faces overlap, then try again.",
            ));
        }
        if let Some(gap) = bridged.filter(|g| *g > JOIN_CONTACT_MM) {
            ctx.set_message(format!(
                "Join bridged a {gap:.3} mm gap; the parts did not originally touch."
            ));
        }
    } else if occt::solid_count(&result) == 0 || !valid(&result) {
        let title = if code == 1 { "Cut" } else { "Intersect" };
        return Err(failed(format!(
            "{title} could not make a valid solid. Check that the target and tools are valid solids, then adjust their overlap."
        )));
    }
    let mut outputs = BTreeMap::from([(name.clone(), Rc::new(result))]);
    if crate::align::truthy(ctx.input("keep_tool")) {
        for (tool, shape) in tool_names.iter().zip(&tools) {
            if *tool != name {
                outputs.insert(tool.clone(), shape.clone());
            }
        }
    }
    Ok(outputs)
}

/// Below this a measured gap is contact, not a gap (`JOIN_CONTACT_MM`).
const JOIN_CONTACT_MM: f64 = 1.0e-6;

/// A primitive's placement (`_Primitive._placement` then `_finish`): moved to
/// `x y z`, then turned about X, Y and Z by `rx ry rz` degrees.
fn placed(
    shape: occt::UniquePtr<occt::Shape>,
    ctx: &Context,
) -> Result<occt::UniquePtr<occt::Shape>, StepError> {
    let mut placement = Vec::with_capacity(6);
    for key in ["x", "y", "z"] {
        placement.push(ctx.value(key, 0.0, Dimension::Length)?);
    }
    for key in ["rx", "ry", "rz"] {
        placement.push(ctx.value(key, 0.0, Dimension::Angle)?);
    }
    occt::place(&shape, &placement).map_err(kernel)
}

/// `Feature.consumed_bodies`: bodies a step destroys in making its outputs.
fn consumed_bodies(feature: &Value) -> Vec<String> {
    let inputs = &feature["inputs"];
    let alias = |v: &Value| match v {
        Value::Object(map) => map
            .get("__body__")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    };
    match feature["type"].as_str().unwrap_or("") {
        "split" => inputs
            .get("body")
            .filter(|b| !b.is_null())
            .map(alias)
            .into_iter()
            .collect(),
        "section_replace" => inputs.get("replacement").map(alias).into_iter().collect(),
        "hinge" if inputs.get("type").map(alias).as_deref() == Some("living") => {
            vec![inputs.get("body_b").map(alias).unwrap_or_default()]
        }
        "boolean" if !inputs["keep_tool"].as_bool().unwrap_or(false) => {
            let target = inputs.get("body").map(alias).unwrap_or_default();
            let tools: Vec<String> = match inputs.get("tools") {
                Some(Value::Array(list)) => list.iter().map(alias).collect(),
                Some(tool) => vec![alias(tool)],
                None => inputs.get("tool").map(alias).into_iter().collect(),
            };
            tools.into_iter().filter(|t| *t != target).collect()
        }
        _ => vec![],
    }
}

fn source_name(path: &str) -> &str {
    path.rsplit(['/', '\\']).next().unwrap_or(path)
}

fn import_offset(ctx: &Context<'_>, shape: &Shape) -> Result<Shape, StepError> {
    let offset = [
        ctx.value("dx", 0.0, Dimension::Length)?,
        ctx.value("dy", 0.0, Dimension::Length)?,
        ctx.value("dz", 0.0, Dimension::Length)?,
    ];
    if offset.iter().all(|value| value.abs() <= 1e-12) {
        return Ok(shape.clone());
    }
    occt::transform_shape(shape, &occt::trsf_translation(&offset), true)
        .map(Rc::new)
        .map_err(kernel)
}

/// Where saved results and import sources are read from during a rebuild.
struct Store<'a> {
    original: Option<zip::ZipArchive<Cursor<&'a [u8]>>>,
    model_blobs: &'a BTreeMap<String, Arc<[u8]>>,
    decoded: HashMap<String, Shape>,
    import_sources: HashMap<String, Vec<Shape>>,
}

impl Store<'_> {
    fn import_source(&mut self, path: &str) -> Result<&[Shape], StepError> {
        if !self.import_sources.contains_key(path) {
            let parts = read_import_source(path)?;
            if self.import_sources.len() >= 4 {
                self.import_sources.clear();
            }
            self.import_sources.insert(path.into(), parts);
        }
        Ok(&self.import_sources[path])
    }

    fn blob(&mut self, sha: &str) -> Option<Arc<[u8]>> {
        if let Some(blob) = self.model_blobs.get(sha) {
            return Some(blob.clone());
        }
        self.archive_blob("geometry", sha)
    }

    fn archive_blob(&mut self, folder: &str, sha: &str) -> Option<Arc<[u8]>> {
        if sha.len() != 64
            || !sha
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        {
            return None;
        }
        let archive = self.original.as_mut()?;
        let mut entry = archive.by_name(&format!("{folder}/{sha}.brep")).ok()?;
        if entry.size() > MAX_IMPORT_BYTES {
            return None;
        }
        let mut bytes = Vec::new();
        entry.read_to_end(&mut bytes).ok()?;
        (format!("{:x}", Sha256::digest(&bytes)) == sha).then(|| bytes.into())
    }

    fn decode(&mut self, sha: &str) -> Option<Shape> {
        if let Some(shape) = self.decoded.get(sha) {
            return Some(shape.clone());
        }
        let shape = Rc::new(occt::read_brep(&self.blob(sha)?).ok()?);
        self.decoded.insert(sha.to_string(), shape.clone());
        Some(shape)
    }

    /// `ResultStore.get`: a step's saved outputs by name, if all of them decode.
    fn get(&mut self, entry: &Value, feature: &Value) -> Option<Outputs> {
        if entry["version"] != CACHE_VERSION || entry["feature_id"] != feature["id"] {
            return None;
        }
        let mut decoded = HashMap::new();
        for (oid, output) in entry["outputs"].as_object()? {
            let part = match output["kind"].as_str()? {
                "brep" => {
                    let sha = output["sha256"].as_str()?;
                    Part {
                        value: Geometry::Brep(self.decode(sha)?),
                        sha: Some(sha.to_string()),
                    }
                }
                "located" => {
                    let matrix: Vec<f64> = output["location"]
                        .as_array()?
                        .iter()
                        .filter_map(Value::as_f64)
                        .collect();
                    let base = self.decode(output["sha256"].as_str()?)?;
                    Part {
                        value: Geometry::Brep(Rc::new(occt::located(&base, &matrix).ok()?)),
                        sha: None,
                    }
                }
                "sketch" if feature["type"] == "sketch" && output["data"].is_object() => Part {
                    value: Geometry::Sketch(Rc::new(output["data"].clone())),
                    sha: None,
                },
                kind if crate::construction::Kind::from_feature(feature["type"].as_str()?)
                    .is_some_and(|feature_kind| kind == feature_kind.cache_kind())
                    && output["data"].is_object() =>
                {
                    Part {
                        value: Geometry::Datum(Rc::new(output.clone())),
                        sha: None,
                    }
                }
                _ => return None,
            };
            decoded.insert(oid.clone(), part);
        }
        let owned: HashMap<&str, &str> = feature["output_ids"]
            .as_object()?
            .iter()
            .filter_map(|(name, oid)| Some((oid.as_str()?, name.as_str())))
            .collect();
        if !decoded.keys().all(|oid| owned.contains_key(oid.as_str())) {
            return None;
        }
        Some(
            decoded
                .into_iter()
                .map(|(oid, part)| (owned[oid.as_str()].to_string(), part))
                .collect(),
        )
    }
}

pub(crate) const MAX_IMPORT_BYTES: u64 = 256 * 1024 * 1024;

fn read_import_source(path: &str) -> Result<Vec<Shape>, StepError> {
    let name = source_name(path);
    let disk_path = Path::new(path);
    if !disk_path.exists() {
        return Err(failed(format!(
            "The imported file '{name}' is no longer there."
        )));
    }
    let extension = disk_path
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let format = match extension.as_str() {
        "step" | "stp" => "STEP",
        "brep" => "B-Rep",
        "iges" | "igs" => "IGES",
        "stl" => "STL",
        "obj" => "OBJ",
        "3mf" => "3MF",
        "gltf" | "glb" => "glTF",
        _ => {
            return Err(failed(format!(
                "This import has to be rebuilt, and this version cannot build it yet: '{name}'."
            )));
        }
    };
    let unreadable = |_| failed(format!("'{name}' is not a readable {format} file."));
    let info = disk_path.metadata().map_err(unreadable)?;
    if !info.is_file() || info.len() > MAX_IMPORT_BYTES {
        return Err(failed(format!(
            "The imported file '{name}' must be a regular {format} file no larger than 256 MB."
        )));
    }
    let mut bytes = Vec::new();
    File::open(path)
        .map_err(unreadable)?
        .take(MAX_IMPORT_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(unreadable)?;
    if bytes.len() as u64 > MAX_IMPORT_BYTES {
        return Err(failed(format!(
            "The imported file '{name}' exceeds 256 MB."
        )));
    }
    let fallback = disk_path
        .file_stem()
        .and_then(|stem| stem.to_str())
        .unwrap_or("Imported");
    let error = |_| failed(format!("'{name}' is not a readable {format} file."));
    let parts = match extension.as_str() {
        "brep" => occt::read_brep_parts(&bytes, fallback).map_err(error)?,
        "iges" | "igs" => crate::iges::read(&bytes, fallback).map_err(failed)?,
        "stl" | "obj" | "3mf" | "gltf" | "glb" => {
            crate::mesh_import::read(&extension, &bytes, fallback).map_err(failed)?
        }
        _ => occt::read_step(&bytes, fallback).map_err(error)?,
    };
    Ok(parts.into_iter().map(|part| Rc::new(part.shape)).collect())
}

struct Rebuilder<'a> {
    features: HashMap<String, Value>,
    signatures: BTreeMap<String, String>,
    entries: Map<String, Value>,
    store: Store<'a>,
    parameters: HashMap<String, f64>,
    invalid: HashSet<String>,
    cache: HashMap<String, Outputs>,
    failed: HashSet<String>,
    evaluating: HashSet<String>,
    states: HashMap<String, (String, String)>,
    failures: BTreeMap<String, String>,
    rebuilt: Vec<String>,
    new_entries: Map<String, Value>,
    new_blobs: BTreeMap<String, Arc<[u8]>>,
}

impl Rebuilder<'_> {
    /// Display fallback only. These results never enter the evaluator's cache.
    fn last_good(&mut self, fid: &str, oid: &str) -> Option<Shape> {
        let mut feature = self.features.get(fid)?;
        let output = |feature: &Value| {
            feature["output_ids"]
                .as_object()?
                .iter()
                .find(|(_, id)| id.as_str() == Some(oid))
                .map(|(name, _)| name.clone())
        };
        if let Some(name) = output(feature) {
            for entry in self.entries.values().rev() {
                if entry["feature_id"] == fid
                    && let Some(part) = self
                        .store
                        .get(entry, feature)
                        .and_then(|mut outputs| outputs.remove(&name))
                {
                    return part.shape().cloned();
                }
            }
        }
        let mut seen = HashSet::new();
        while seen.insert(feature["id"].as_str()?) {
            let binding = feature["bindings"]
                .as_object()?
                .values()
                .find(|r| r["object_id"].as_str() == Some(oid))?;
            let source = binding["feature_id"].as_str()?;
            feature = self.features.get(source)?;
            let saved = self.cache.get(source).cloned().or_else(|| {
                if self.failed.contains(source) {
                    return None;
                }
                let entry = self.entries.get(self.signatures.get(source)?)?;
                self.store.get(entry, feature)
            });
            if let Some(part) = saved.and_then(|mut outputs| outputs.remove(&output(feature)?)) {
                return part.shape().cloned();
            }
        }
        None
    }

    /// `_get`: the step's outputs, its uncached ancestors run first, without recursion.
    fn get(&mut self, fid: &str) -> Result<Outputs, StepError> {
        let mut pending = vec![(fid.to_string(), false)];
        let mut scheduled = HashSet::new();
        while let Some((current, ready)) = pending.pop() {
            if self.cache.contains_key(&current) || self.failed.contains(&current) {
                continue;
            }
            let saved = !self.invalid.contains(&current)
                && self.entries.contains_key(&self.signatures[&current]);
            if ready || saved {
                let _ = self.evaluate(&current);
                continue;
            }
            if !scheduled.insert(current.clone()) {
                continue;
            }
            pending.push((current.clone(), true));
            let bindings: Vec<String> = self.features[&current]["bindings"]
                .as_object()
                .into_iter()
                .flatten()
                .filter_map(|(_, r)| r["feature_id"].as_str().map(String::from))
                .collect();
            for source in bindings.into_iter().rev() {
                if self.features.contains_key(&source) {
                    pending.push((source, false));
                }
            }
        }
        self.evaluate(fid)
    }

    /// `_evaluate`: a step's saved result, or the step run on its inputs.
    fn evaluate(&mut self, fid: &str) -> Result<Outputs, StepError> {
        if self.failed.contains(fid) {
            return Err(lost(
                "An earlier feature failed; keeping the last valid model.",
            ));
        }
        if let Some(outputs) = self.cache.get(fid) {
            return Ok(outputs.clone());
        }
        if !self.evaluating.insert(fid.to_string()) {
            return Err(lost("A feature refers back to itself."));
        }
        let result = self.run(fid);
        self.evaluating.remove(fid);
        if let Err(error) = &result {
            let state = if error.lost {
                "needs_attention"
            } else {
                "failed"
            };
            self.states
                .insert(fid.to_string(), (state.into(), error.message.clone()));
            self.failures.insert(fid.to_string(), error.message.clone());
            self.failed.insert(fid.to_string());
        }
        result
    }

    fn run(&mut self, fid: &str) -> Result<Outputs, StepError> {
        let feature = self.features[fid].clone();
        let suppressed = feature["suppressed"].as_bool().unwrap_or(false);
        let signature = self.signatures[fid].clone();
        if !self.invalid.contains(fid)
            && let Some(entry) = self.entries.get(&signature).cloned()
            && let Some(outputs) = self.store.get(&entry, &feature)
        {
            let message = entry["message"].as_str().unwrap_or("").to_string();
            let state = if suppressed { "suppressed" } else { "ok" };
            self.states.insert(fid.to_string(), (state.into(), message));
            self.cache.insert(fid.to_string(), outputs.clone());
            return Ok(outputs);
        }
        let inputs = self.inputs(&feature)?;
        self.finish(fid, feature, signature, inputs, suppressed)
    }

    /// `_input_context`: each bound name, from the step that wrote it.
    fn inputs(&mut self, feature: &Value) -> Result<BoundInputs, StepError> {
        let Some(bindings) = feature["bindings"].as_object() else {
            return Ok(BoundInputs::default());
        };
        // A deleted input blocks this step even if another input also fails.
        // Name the deleted object so the user can repair the saved reference.
        if let Some((name, _)) = bindings.iter().find(|(_, reference)| {
            let source = reference["feature_id"].as_str().unwrap_or("");
            !self.features.contains_key(source)
        }) {
            return Err(lost(format!(
                "The object '{name}' this feature needs was deleted. Undo the deletion or re-select its input."
            )));
        }
        let mut inputs = BoundInputs::default();
        for (name, reference) in bindings {
            let source_id = reference["feature_id"].as_str().unwrap_or("");
            let outputs = self.get(source_id)?;
            let source = &self.features[source_id];
            let object = reference["object_id"].as_str().unwrap_or("");
            let source_name = source["output_ids"]
                .as_object()
                .into_iter()
                .flatten()
                .find(|(_, oid)| oid.as_str() == Some(object))
                .map(|(n, _)| n.clone());
            match source_name.and_then(|n| outputs.get(&n).cloned()) {
                Some(Part {
                    value: Geometry::Brep(shape),
                    ..
                }) => {
                    inputs.bodies.insert(name.clone(), shape);
                }
                Some(Part {
                    value: Geometry::Sketch(sketch),
                    ..
                }) => {
                    inputs.sketches.insert(name.clone(), sketch);
                }
                Some(Part {
                    value: Geometry::Datum(_),
                    ..
                })
                | None => {
                    return Err(lost(format!(
                        "The output '{name}' this feature needs is unavailable."
                    )));
                }
            };
        }
        Ok(inputs)
    }

    /// The step run on its inputs, and its result stored.
    fn finish(
        &mut self,
        fid: &str,
        feature: Value,
        signature: String,
        inputs: BoundInputs,
        suppressed: bool,
    ) -> Result<Outputs, StepError> {
        let names: Vec<String> = feature["outputs"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|n| n.as_str().map(String::from))
            .collect();
        let mut outputs: BTreeMap<String, Geometry> = if suppressed {
            self.states
                .insert(fid.to_string(), ("suppressed".into(), String::new()));
            names
                .iter()
                .filter_map(|n| Some((n.clone(), Geometry::Brep(inputs.bodies.get(n)?.clone()))))
                .collect()
        } else if feature["type"] == "sketch" {
            let solved = crate::sketch::solve(&feature["inputs"]["sketch"], &self.parameters)?;
            let message = solved.message();
            if solved.state == crate::sketch::State::Conflicting {
                return Err(failed(message));
            }
            let name = names
                .first()
                .ok_or_else(|| failed("This sketch has no output."))?;
            self.states.insert(fid.to_string(), ("ok".into(), message));
            self.rebuilt.push(fid.to_string());
            BTreeMap::from([(name.clone(), Geometry::Sketch(Rc::new(solved.recipe)))])
        } else {
            let kind = feature["type"].as_str().unwrap_or("");
            let ctx = Context {
                feature: &feature,
                features: Some(&self.features),
                bodies: inputs.bodies,
                sketches: inputs.sketches,
                parameters: &self.parameters,
                message: Default::default(),
            };
            let made = if let Some(datum) = crate::construction::Kind::from_feature(kind) {
                let name = feature["name"].as_str().unwrap_or_default();
                let output = datum.execute(&ctx, name)?;
                BTreeMap::from([(
                    format!("__construction__{name}"),
                    Geometry::Datum(Rc::new(output)),
                )])
            } else {
                let shapes = execute(kind, &ctx, Some(&mut self.store)).ok_or_else(|| {
                    failed("This step has to be rebuilt, and this version cannot build it yet. Edit it in SimpleCAD 0.4.")
                })??;
                if shapes.is_empty() {
                    return Err(failed("This feature produced no geometry."));
                }
                shapes
                    .into_iter()
                    .map(|(name, shape)| (name, Geometry::Brep(shape)))
                    .collect()
            };
            self.states
                .insert(fid.to_string(), ("ok".into(), ctx.message.take()));
            self.rebuilt.push(fid.to_string());
            made
        };
        // `keep`: what the user deleted is not emitted.
        let dropped: Vec<&str> = feature["dropped"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect();
        outputs.retain(|name, _| !dropped.contains(&name.as_str()));
        // Outputs given ids as the rebuild first meets them.
        let mut output_ids = feature["output_ids"]
            .as_object()
            .cloned()
            .unwrap_or_default();
        for name in outputs.keys() {
            if !output_ids.contains_key(name) {
                let source = feature["bindings"][name]["object_id"]
                    .as_str()
                    .map(String::from);
                output_ids.insert(
                    name.clone(),
                    json!(source.unwrap_or_else(|| output_id(fid, name))),
                );
            }
        }
        self.features.get_mut(fid).unwrap()["output_ids"] = Value::Object(output_ids.clone());
        // `ResultStore.put`.
        let mut parts = Outputs::new();
        let mut encoded = Map::new();
        for (name, value) in outputs {
            let (data, sha) = match &value {
                Geometry::Brep(shape) => {
                    let blob = occt::write_brep(shape).map_err(kernel)?;
                    let sha = format!("{:x}", Sha256::digest(&blob));
                    self.new_blobs.insert(sha.clone(), blob.into());
                    (json!({ "kind": "brep", "sha256": sha }), Some(sha))
                }
                Geometry::Sketch(sketch) => (json!({ "kind": "sketch", "data": &**sketch }), None),
                Geometry::Datum(datum) => ((**datum).clone(), None),
            };
            encoded.insert(output_ids[&name].as_str().unwrap_or("").to_string(), data);
            parts.insert(name, Part { value, sha });
        }
        let message = self
            .states
            .get(fid)
            .map(|s| s.1.clone())
            .unwrap_or_default();
        self.new_entries.insert(
            signature,
            json!({ "feature_id": fid, "outputs": encoded, "message": message, "version": CACHE_VERSION }),
        );
        self.cache.insert(fid.to_string(), parts.clone());
        self.invalid.remove(fid);
        Ok(parts)
    }
}

/// A rebuilder over `payload`'s steps, saved results and blobs.
fn setup<'a>(
    order: &[Value],
    payload: &Value,
    signatures_now: BTreeMap<String, String>,
    parameters: HashMap<String, f64>,
    blobs: &'a BTreeMap<String, Arc<[u8]>>,
    original: &'a [u8],
    stale: &BTreeSet<String>,
) -> Rebuilder<'a> {
    let archive = if original.is_empty() {
        None
    } else {
        zip::ZipArchive::new(Cursor::new(original)).ok()
    };
    Rebuilder {
        features: order
            .iter()
            .filter_map(|f| Some((f["id"].as_str()?.to_string(), f.clone())))
            .collect(),
        signatures: signatures_now,
        entries: payload["cache"]["entries"]
            .as_object()
            .cloned()
            .unwrap_or_default(),
        store: Store {
            original: archive,
            model_blobs: blobs,
            decoded: HashMap::new(),
            import_sources: HashMap::new(),
        },
        parameters,
        invalid: stale.iter().cloned().collect(),
        cache: HashMap::new(),
        failed: HashSet::new(),
        evaluating: HashSet::new(),
        states: HashMap::new(),
        failures: BTreeMap::new(),
        rebuilt: Vec::new(),
        new_entries: Map::new(),
        new_blobs: BTreeMap::new(),
    }
}

/// Recover only already-built geometry for bodies whose saved steps failed.
pub(crate) fn recover_saved(
    model: &Value,
    original: &[u8],
    bodies: &[(&str, &str)],
) -> HashMap<String, Vec<u8>> {
    let Some(order) = model["features"].as_array() else {
        return HashMap::new();
    };
    let blobs = BTreeMap::new();
    let mut rebuilder = setup(
        order,
        model,
        signatures(model).unwrap_or_default(),
        HashMap::new(),
        &blobs,
        original,
        &BTreeSet::new(),
    );
    bodies
        .iter()
        .filter_map(|&(oid, fid)| {
            let shape = rebuilder.last_good(fid, oid)?;
            Some((oid.to_string(), occt::write_brep(&shape).ok()?))
        })
        .collect()
}

/// Each step of type `kind` solved again on its saved inputs, for checking a
/// port against the Python app: Align's transform (3×4, row by row) and
/// description, the definition of a construction datum, or the error it gives.
pub fn check_steps(model: &Model, original: &[u8], kind: &str) -> Result<Vec<Value>, String> {
    let unreadable = || "This project's steps could not be read.".to_string();
    let order: Vec<Value> = model.payload["features"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let signatures_now = signatures(&model.payload).ok_or_else(unreadable)?;
    let (parameters, _) = resolve(
        model.payload["parameters"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or(&[]),
    );
    let blobs = model.blobs.clone();
    let mut rebuilder = setup(
        &order,
        &model.payload,
        signatures_now,
        parameters,
        &blobs,
        original,
        &BTreeSet::new(),
    );
    let mut found = Vec::new();
    for feature in order.iter().filter(|f| f["type"] == kind) {
        let name = feature["name"].clone();
        let outcome = rebuilder.inputs(feature).and_then(|inputs| {
            let ctx = Context { feature, features: Some(&rebuilder.features), bodies: inputs.bodies, sketches: inputs.sketches, parameters: &rebuilder.parameters, message: Default::default() };
            match kind {
                "align" => crate::align::solve(&ctx).map(|(_, _, p)| {
                    json!({ "transform": occt::trsf_values(&p.transform), "description": p.description })
                }),
                _ => crate::construction::Kind::from_feature(kind)
                    .ok_or_else(|| failed("not a checked step type"))?
                    .execute(&ctx, name.as_str().unwrap_or_default()),
            }
        });
        found.push(match outcome {
            Ok(value) => json!({ "id": feature["id"], "name": name, "result": value }),
            Err(error) => json!({ "id": feature["id"], "name": name, "error": error.message }),
        });
    }
    Ok(found)
}

/// `DependencyGraph.dependents`: `ids` and everything downstream of them.
fn dependents(order: &[Value], ids: &HashSet<String>) -> HashSet<String> {
    let mut found = ids.clone();
    for feature in order {
        let id = feature["id"].as_str().unwrap_or("");
        if upstream(feature, id).iter().any(|u| found.contains(u)) {
            found.insert(id.to_string());
        }
    }
    found
}

/// Rebuilds `model`: `shown` holds the bodies as they are now, by object id,
/// `stale` the steps to run whatever is saved for them (all of them, for a
/// rebuild from the recipe). The model's bodies, results, geometry and step
/// states are updated as the Python app's would be, and saved with it.
pub fn rebuild(
    model: &mut Model,
    original: &[u8],
    shown: &HashMap<String, Shape>,
    stale: &BTreeSet<String>,
) -> Result<Report, String> {
    let unreadable = || "This project's steps could not be read.".to_string();
    let order: Vec<Value> = model.payload["features"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let signatures_now = signatures(&model.payload).ok_or_else(unreadable)?;
    let (parameters, _) = resolve(
        model.payload["parameters"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or(&[]),
    );
    let blobs = model.blobs.clone();
    let mut rebuilder = setup(
        &order,
        &model.payload,
        signatures_now.clone(),
        parameters,
        &blobs,
        original,
        stale,
    );

    // The graph's heads: each object's newest writer, in the order first written.
    let mut heads: Vec<(String, String, String)> = Vec::new();
    for feature in &order {
        let fid = feature["id"].as_str().unwrap_or("");
        for name in feature["outputs"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            let Some(oid) = feature["output_ids"][name].as_str() else {
                continue;
            };
            match heads.iter_mut().find(|(o, _, _)| o == oid) {
                Some(head) => *head = (oid.into(), fid.into(), name.into()),
                None => heads.push((oid.into(), fid.into(), name.into())),
            }
        }
    }
    let previous: HashMap<String, (String, String)> = model.payload["bodies"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|b| {
            Some((
                b["id"].as_str()?.to_string(),
                (
                    b["name"].as_str()?.to_string(),
                    b["producer"].as_str().unwrap_or("").to_string(),
                ),
            ))
        })
        .collect();
    let saved_names: HashSet<&str> = previous.values().map(|(name, _)| name.as_str()).collect();
    let consumed: HashSet<String> = order
        .iter()
        .filter(|f| !f["suppressed"].as_bool().unwrap_or(false))
        .flat_map(|f| {
            consumed_bodies(f)
                .into_iter()
                .filter_map(|n| f["bindings"][&n]["object_id"].as_str().map(String::from))
        })
        .collect();
    // Final bodies by object id: name, shape, producer.
    let mut last: Vec<(String, Final)> = Vec::new();
    let keep_previous = |last: &mut Vec<(String, Final)>, oid: &str| {
        let body = previous
            .get(oid)
            .map(|(name, producer)| (name.clone(), shown.get(oid).cloned(), producer.clone()));
        last.retain(|(o, _)| o != oid);
        if let Some(body) = body {
            last.push((oid.to_string(), body));
        }
    };
    let keep_last = |rebuilder: &mut Rebuilder,
                     last: &mut Vec<(String, Final)>,
                     oid: &str,
                     fid: &str,
                     name: &str,
                     still_consumed: bool| {
        keep_previous(last, oid);
        if shown.contains_key(oid) && previous.contains_key(oid) {
            return;
        }
        let (producer, output) = heads
            .iter()
            .find(|(object, _, _)| object == oid)
            .map_or((fid, name), |(_, head, output)| {
                (head.as_str(), output.as_str())
            });
        // An output absent from the saved document cannot take a name already
        // owned by a different object.
        if !previous.contains_key(oid)
            && (still_consumed
                || saved_names.contains(output)
                || rebuilder.features[producer]["dropped"]
                    .as_array()
                    .is_some_and(|dropped| dropped.iter().any(|name| name == output)))
        {
            return;
        }
        if let Some(shape) = rebuilder.last_good(producer, oid) {
            last.retain(|(object, _)| object != oid);
            let name = previous.get(oid).map_or(name, |(old_name, _)| old_name);
            last.push((oid.into(), (name.into(), Some(shape), producer.into())));
        }
    };
    for (oid, fid, name) in &heads {
        if name.starts_with("__") || consumed.contains(oid) {
            continue;
        }
        let feature = &rebuilder.features[fid];
        if feature["dropped"]
            .as_array()
            .is_some_and(|d| d.contains(&json!(name)))
        {
            continue;
        }
        match rebuilder.get(fid) {
            Ok(outputs) => {
                if let Some(shape) = outputs.get(name).and_then(Part::shape) {
                    last.retain(|(o, _)| o != oid);
                    last.push((
                        oid.clone(),
                        (name.clone(), Some(shape.clone()), fid.clone()),
                    ));
                }
            }
            Err(_) => {
                keep_last(&mut rebuilder, &mut last, oid, fid, name, false);
            }
        }
    }
    for (_, fid, name) in &heads {
        if name.starts_with("__") {
            let _ = rebuilder.get(fid);
        }
    }
    let failed_ids = rebuilder.failed.clone();
    let protected = dependents(&order, &failed_ids);
    let mut still_consumed = consumed;
    for feature in &order {
        if feature["suppressed"].as_bool().unwrap_or(false) {
            continue;
        }
        let fid = feature["id"].as_str().unwrap_or("");
        let blocked = protected.contains(fid);
        for name in consumed_bodies(feature) {
            let Some(oid) = feature["bindings"][&name]["object_id"].as_str() else {
                continue;
            };
            if blocked {
                still_consumed.remove(oid);
            } else {
                still_consumed.insert(oid.to_string());
            }
            if blocked && previous.contains_key(oid) {
                keep_previous(&mut last, oid);
            } else {
                last.retain(|(o, _)| o != oid);
            }
        }
    }
    for fid in &protected {
        if !failed_ids.contains(fid) {
            rebuilder.states.insert(
                fid.clone(),
                (
                    "needs_attention".into(),
                    "An earlier feature failed; keeping the last valid model.".into(),
                ),
            );
        }
        let outputs: Vec<(String, String)> = rebuilder.features[fid]["output_ids"]
            .as_object()
            .into_iter()
            .flatten()
            .filter_map(|(name, oid)| Some((name.clone(), oid.as_str()?.into())))
            .collect();
        for (name, oid) in outputs {
            keep_last(
                &mut rebuilder,
                &mut last,
                &oid,
                fid,
                &name,
                still_consumed.contains(&oid),
            );
        }
    }

    // The model as the rebuild leaves it: ids given to new outputs, results, bodies, states.
    let features: Vec<Value> = order
        .iter()
        .map(|f| rebuilder.features[f["id"].as_str().unwrap_or("")].clone())
        .collect();
    model.payload["features"] = json!(features);
    let entries = model.payload["cache"]
        .as_object_mut()
        .map(|c| c.entry("entries").or_insert_with(|| json!({})).clone());
    let mut entries = entries
        .and_then(|e| e.as_object().cloned())
        .unwrap_or_default();
    entries.extend(rebuilder.new_entries.clone());
    model.payload["cache"] = json!({ "version": CACHE_VERSION, "entries": entries });
    model.blobs.extend(rebuilder.new_blobs.clone());
    let old_bodies: HashMap<String, Value> = model.payload["bodies"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|b| Some((b["id"].as_str()?.to_string(), b.clone())))
        .collect();
    let bodies: Vec<Value> = last
        .iter()
        .map(|(oid, (name, _, producer))| {
            let mut body = old_bodies.get(oid).cloned().unwrap_or_else(|| json!({ "id": oid, "visible": true, "color": null, "metadata": {} }));
            body["name"] = json!(name);
            body["producer"] = json!(producer);
            json!({ "id": oid, "name": name, "visible": body["visible"], "color": body["color"], "producer": producer, "metadata": body["metadata"] })
        })
        .collect();
    model.payload["bodies"] = json!(bodies);
    let actual = signatures(&model.payload).ok_or_else(unreadable)?;
    let mut diagnostics = model.payload["diagnostics"]
        .as_object()
        .cloned()
        .unwrap_or_default();
    for (fid, (state, message)) in &rebuilder.states {
        diagnostics.insert(fid.clone(), json!({ "state": state, "message": message }));
    }
    let mut geometry = Map::new();
    for (oid, (_, shape, producer)) in &last {
        let Some(shape) = shape else { continue };
        let part_sha = rebuilder.cache.get(producer).and_then(|o| {
            o.values()
                .find(|p| {
                    p.shape()
                        .is_some_and(|candidate| Rc::ptr_eq(candidate, shape))
                })
                .and_then(|p| p.sha.clone())
        });
        let sha = match part_sha {
            Some(sha) => sha,
            None => match model.payload["geometry"][oid]["sha256"].as_str() {
                // The geometry it already had, unchanged.
                Some(sha) if shown.get(oid).is_some_and(|s| Rc::ptr_eq(s, shape)) => {
                    sha.to_string()
                }
                _ => {
                    let blob = occt::write_brep(shape).map_err(|e| e.what().to_string())?;
                    let sha = format!("{:x}", Sha256::digest(&blob));
                    model.blobs.insert(sha.clone(), blob.into());
                    sha
                }
            },
        };
        let accepted = (!protected.contains(producer))
            .then(|| actual.get(producer).cloned())
            .flatten();
        let ok = diagnostics.get(producer).is_none_or(|d| d["state"] == "ok");
        let current = accepted.is_some() && ok;
        geometry.insert(oid.clone(), json!({ "sha256": sha, "feature_id": producer, "signature": accepted, "current": current }));
    }
    model.payload["geometry"] = Value::Object(geometry);
    model.payload["diagnostics"] = Value::Object(diagnostics);
    Ok(Report {
        bodies: last
            .into_iter()
            .map(|(id, (name, shape, _))| Body { id, name, shape })
            .collect(),
        failures: rebuilder.failures,
        rebuilt: rebuilder.rebuilt,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn construction_planes_are_rebuilt_and_cached_without_display_bodies() {
        let feature = json!({
            "id": "plane", "type": "plane_through_points", "name": "Plane through Points",
            "inputs": { "points": [[5, 0, 2], [5, 10, 2], [5, 0, 12]] },
            "outputs": ["__construction__Plane through Points"],
            "output_ids": { "__construction__Plane through Points": "plane-output" },
            "bindings": {}, "suppressed": false, "dropped": []
        });
        let mut model = Model::new(json!({"features": [feature], "bodies": []}));
        let report = rebuild(&mut model, &[], &HashMap::new(), &BTreeSet::new()).unwrap();
        assert!(report.failures.is_empty());
        assert!(report.bodies.is_empty());
        assert_eq!(report.rebuilt, ["plane"]);
        let signature = &signatures(&model.payload).unwrap()["plane"];
        assert_eq!(
            model.payload["cache"]["entries"][signature]["outputs"]["plane-output"],
            json!({ "kind": "ConstructionPlane", "data": {
                "name": "Plane through Points", "origin": [5.0, 0.0, 2.0],
                "normal": [1.0, 0.0, 0.0], "x_axis": [0.0, 1.0, 0.0]
            }})
        );
        let cached = rebuild(&mut model, &[], &HashMap::new(), &BTreeSet::new()).unwrap();
        assert!(cached.rebuilt.is_empty());
        assert!(cached.failures.is_empty());
        model.payload["features"][0]["inputs"]["points"][2] = json!([5, 20, 2]);
        let invalid = rebuild(&mut model, &[], &HashMap::new(), &BTreeSet::new()).unwrap();
        assert_eq!(
            invalid.failures["plane"],
            "Those three points are in a line."
        );
    }

    #[test]
    fn an_explicitly_deleted_input_remains_actionable_when_another_input_failed() {
        let upstream = json!({"id": "upstream"});
        let align = json!({"id": "align", "bindings": {
            "Moving": {"feature_id": "upstream", "object_id": "moving"},
            "Deleted tool": {"feature_id": "", "object_id": "deleted"},
        }});
        let blobs = BTreeMap::new();
        let mut rebuilder = setup(
            &[upstream, align.clone()],
            &Value::Null,
            BTreeMap::new(),
            HashMap::new(),
            &blobs,
            &[],
            &BTreeSet::new(),
        );
        rebuilder.failed.insert("upstream".into());
        let error = rebuilder.inputs(&align).err().unwrap();
        assert!(error.lost);
        assert_eq!(
            error.message,
            "The object 'Deleted tool' this feature needs was deleted. Undo the deletion or re-select its input."
        );
    }

    #[test]
    fn saved_shaft_and_through_hole_rebuild_as_physical_threads() {
        let cases = [
            (
                "thread",
                occt::make_cylinder(15.0, 30.0).unwrap(),
                "cylinder",
                json!({"designation": "M30", "length": "10 mm", "from_end": "top"}),
                20_578.554_906,
                18,
            ),
            (
                "hole",
                occt::make_box(40.0, 40.0, 12.0).unwrap(),
                "plane",
                json!({"designation": "M30", "diameter": 30.0, "style": "threaded",
                    "depth_mode": "through", "position": [20.0, 20.0, 12.0]}),
                11_534.333_073,
                25,
            ),
        ];
        for (kind, blank, geometry, mut inputs, volume, faces) in cases {
            let (index, print) = (0..occt::faces(&blank).unwrap().len() as u32)
                .map(|index| (index, occt::face_fingerprint(&blank, index).unwrap()))
                .filter(|(_, print)| print.geometry == geometry)
                .max_by(|(_, a), (_, b)| a.center[2].total_cmp(&b.center[2]))
                .unwrap();
            inputs["body"] = json!({"__body__": "Blank"});
            inputs["face"] = json!({"__ref__": {
                "body": "Blank", "kind": "face", "index_hint": index,
                "fingerprint": {"kind": "face", "geometry": print.geometry, "center": print.center,
                    "direction": print.direction, "measure": print.measure,
                    "radius": print.has_radius.then_some(print.radius)}
            }});
            let feature =
                json!({"id": "test", "type": kind, "outputs": ["Blank"], "inputs": inputs});
            let parameters = HashMap::new();
            let ctx = Context::placement(&feature, "Blank", Rc::new(blank), &parameters);
            let result = execute(kind, &ctx, None).unwrap().unwrap();
            let built = &result["Blank"];
            let actual = occt::volume(built);
            // The reference volume is from Fedora. OCCT's integration over
            // swept B-splines differs across compilers: confirm the physical
            // material change everywhere and exact Fedora parity on Linux.
            let tolerance = if cfg!(target_os = "linux") {
                0.001
            } else {
                1.0
            };
            assert!(
                (actual - volume).abs() < tolerance,
                "{kind}: {actual} vs {volume}"
            );
            assert_eq!(occt::solid_count(built), 1, "{kind}");
            assert_eq!(occt::face_occurrences(built), faces, "{kind}");
        }
    }

    #[test]
    fn vents_cut_the_same_hex_grid_into_a_plate_and_a_picked_face() {
        let base = Rc::new(occt::make_box(30.0, 20.0, 3.0).unwrap());
        let index = occt::faces(&base)
            .unwrap()
            .iter()
            .position(|face| face.planar && face.normal[2] > 0.99)
            .unwrap();
        let fingerprint = occt::face_fingerprint(&base, index as u32).unwrap();
        let feature = json!({ "name": "Vent", "outputs": ["Vent"], "inputs": {
            "body": {"__body__": "Base"}, "width": "3 cm", "depth": "20 mm", "thickness": 3,
            "across_flats": 5, "wall": 1, "margin": 2,
            "face": {"__ref__": {"body": "Base", "index_hint": index, "fingerprint": {
                "kind": "face", "geometry": fingerprint.geometry, "measure": fingerprint.measure,
                "center": fingerprint.center, "direction": fingerprint.direction
            }}}
        }});
        let ctx = Context {
            feature: &feature,
            features: None,
            bodies: HashMap::from([("Base".into(), base)]),
            sketches: HashMap::new(),
            parameters: &HashMap::new(),
            message: Default::default(),
        };
        let expected = 30.0 * 20.0 * 3.0 - 3.0 * 25.0 * 3.0_f64.sqrt() / 2.0 * 3.0;
        for (step, body) in [("vent_plate", "Vent"), ("vent_cut", "Base")] {
            let result = execute(step, &ctx, None).unwrap().unwrap();
            assert!(occt::is_valid(&result[body]).unwrap());
            assert!((occt::volume(&result[body]) - expected).abs() < 1e-6);
        }
    }

    #[test]
    fn resolves_a_shell_floor_edge_when_its_curve_direction_reverses() {
        let blank = occt::make_box(80.0, 100.0, 8.0).unwrap();
        let top = occt::faces(&blank)
            .unwrap()
            .iter()
            .position(|face| face.planar && face.normal[2] > 0.99)
            .unwrap() as u32;
        let tray = occt::shell(&blank, &[top], 3.0).unwrap();
        let edges = occt::edge_fingerprints(&tray).unwrap();
        let floor = edges
            .iter()
            .position(|edge| {
                edge.curve
                    .center
                    .iter()
                    .zip([3.0, 50.0, 3.0])
                    .all(|(actual, expected)| (actual - expected).abs() < 1e-6)
            })
            .unwrap();
        let curve = &edges[floor].curve;
        let reference = json!({
            "index_hint": floor,
            "fingerprint": {
                "kind": "edge", "geometry": curve.geometry, "measure": curve.measure,
                "center": curve.center, "direction": curve.direction.map(|axis| -axis),
                "neighbours": ["plane:0.0,0.0,1.0", "plane:1.0,0.0,0.0"]
            }
        });
        let resolved = resolve_edge(&reference, &edges).unwrap();
        assert_eq!(resolved as usize, floor);
        let rounded = occt::finish_edges(&tray, &[resolved], 3.0, true).unwrap();
        assert!(occt::is_valid(&rounded).unwrap());
        assert!(occt::volume(&rounded) > occt::volume(&tray));
    }

    #[test]
    fn saved_arrange_rows_use_world_axes_and_keep_the_group_offset() {
        for (axis, key) in ["x", "y", "z"].into_iter().enumerate() {
            for (mode, positions, gap) in [
                ("fixed_gap", [0.0, 5.0, 12.0], 3),
                ("equal_gaps", [0.0, 9.0, 20.0], 7),
            ] {
                let names = ["A", "B", "C"];
                let bodies = names
                    .into_iter()
                    .enumerate()
                    .map(|(i, name)| {
                        let width = 2.0 * (i + 1) as f64;
                        let shape = occt::make_box(width, width, width).unwrap();
                        let mut origin = [3.0 * i as f64, 6.0 * i as f64, 9.0 * i as f64];
                        origin[axis] = [0.0, 5.0, 20.0][i];
                        let shape =
                            occt::transform_shape(&shape, &occt::trsf_translation(&origin), true)
                                .unwrap();
                        (name.into(), Rc::new(shape))
                    })
                    .collect();
                let offset = [1.0, -2.0, 4.0];
                let feature = json!({"inputs": {
                    "bodies": [{"__body__": "B"}, {"__body__": "C"}, {"__body__": "A"}],
                    "mode": mode, "axis": key, "gap": "3 mm",
                    "dx": offset[0], "dy": offset[1], "dz": offset[2],
                }});
                let ctx = Context {
                    feature: &feature,
                    features: None,
                    bodies,
                    sketches: HashMap::new(),
                    parameters: &HashMap::new(),
                    message: Default::default(),
                };
                let output = execute("arrange", &ctx, None)
                    .expect("saved row implemented")
                    .unwrap();
                assert_eq!(output.len(), names.len());
                for (i, name) in names.into_iter().enumerate() {
                    let bounds = occt::bounds(&output[name]).unwrap();
                    let center = (bounds[axis] + bounds[axis + 3]) / 2.0;
                    let expected = positions[i] + (i + 1) as f64 + offset[axis];
                    assert!((center - expected).abs() < 1e-6, "{mode} {key} {name}");
                    for cross in (0..3).filter(|&cross| cross != axis) {
                        let center = (bounds[cross] + bounds[cross + 3]) / 2.0;
                        assert!((center - [5.0, 8.0, 11.0][cross] - offset[cross]).abs() < 1e-6);
                    }
                }
                assert_eq!(
                    ctx.message.into_inner(),
                    format!("Arranged 3 objects with {gap} mm gaps.")
                );
            }
        }
    }

    #[test]
    fn arrange_patterns_and_target_rows_still_report_unported() {
        for inputs in [
            json!({"pattern": "grid"}),
            json!({"target_face": {"__ref__": {}}}),
        ] {
            let feature = json!({"inputs": inputs});
            let ctx = Context {
                feature: &feature,
                features: None,
                bodies: HashMap::new(),
                sketches: HashMap::new(),
                parameters: &HashMap::new(),
                message: Default::default(),
            };
            let error = execute("arrange", &ctx, None)
                .expect("Arrange dispatch")
                .err()
                .unwrap();
            assert!(error.message.contains("cannot build it yet"));
        }
    }
}

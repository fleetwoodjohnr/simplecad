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
use std::io::{Cursor, Read};
use std::rc::Rc;

/// A shape, shared between the steps and bodies that hold it.
pub type Shape = Rc<occt::UniquePtr<occt::Shape>>;

/// A step's output: the shape, and its blob's sha when it is stored as one.
#[derive(Clone)]
pub struct Part {
    pub shape: Shape,
    sha: Option<String>,
}

type Outputs = BTreeMap<String, Part>;

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

fn kernel(e: occt::Exception) -> StepError {
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
    bodies: HashMap<String, Shape>,
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
            bodies: HashMap::from([(name.into(), body)]),
            parameters,
            message: Default::default(),
        }
    }

    pub(crate) fn input(&self, key: &str) -> Option<&Value> {
        self.feature["inputs"].get(key)
    }

    pub(crate) fn set_message(&self, message: String) {
        *self.message.borrow_mut() = message;
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
        self.bodies.get(&name).cloned().ok_or_else(|| {
            failed(format!("The body '{name}' this feature needs is not available. It may have been deleted. Re-select the body."))
        })
    }

    /// `ctx.value`: a number input, which may be an expression, in mm or degrees.
    pub fn value(&self, key: &str, default: f64, dimension: Dimension) -> Result<f64, StepError> {
        match self.input(key) {
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
        let reference = self.input(key).and_then(|v| v.get("__ref__")).ok_or_else(|| {
            failed("This feature has lost the geometry it was built on. Re-select the face or edge for this feature.")
        })?;
        let body = reference["body"].as_str().unwrap_or("");
        let shape = self.bodies.get(body).cloned().ok_or_else(|| {
            failed(format!("The body '{body}' this feature needs is not available. It may have been deleted or renamed."))
        })?;
        let index = resolve_face(reference, &shape)?;
        Ok((shape, index))
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

/// `naming.similarity` for faces, whose fingerprints carry no neighbours.
fn similarity(left: &Value, right: &occt::FaceFingerprint) -> f64 {
    let closeness = |a: f64, b: f64, scale: f64| 1.0 / (1.0 + (a - b).abs() / scale.max(1e-9));
    if left["kind"] != "face" || left["geometry"].as_str() != Some(right.geometry.as_str()) {
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
        let aligned = ((dot + 1.0) / 2.0).clamp(0.0, 1.0);
        scores.push((aligned * aligned, 2.0));
    }
    if let (Some(radius), true) = (left["radius"].as_f64(), right.has_radius) {
        scores.push((closeness(radius, right.radius, radius.max(1.0)), 1.5));
    }
    let weight: f64 = scores.iter().map(|(_, w)| w).sum();
    scores.iter().map(|(s, w)| s * w).sum::<f64>() / weight
}

/// `naming.resolve` for a face: the hinted face when it is certainly the one,
/// else the one best match, never a doubtful one.
fn resolve_face(reference: &Value, shape: &Shape) -> Result<u32, StepError> {
    const MATCH_THRESHOLD: f64 = 0.55;
    const CERTAIN_SCORE: f64 = 0.999;
    let count = occt::faces(shape).map_err(kernel)?.len() as u32;
    if count == 0 {
        return Err(lost(
            "The face this feature depends on no longer exists. Re-select the geometry for this feature.",
        ));
    }
    let fingerprint = &reference["fingerprint"];
    let scores: Vec<f64> = (0..count)
        .map(|i| {
            occt::face_fingerprint(shape, i)
                .map(|f| similarity(fingerprint, &f))
                .map_err(kernel)
        })
        .collect::<Result<_, _>>()?;
    let hint = reference["index_hint"].as_i64().unwrap_or(-1);
    if (0..i64::from(count)).contains(&hint) {
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
        _ => Err(lost(
            "The face this feature was built on can no longer be found. The shape changed too much. Re-select the face to repair this feature.",
        )),
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
    "push_pull",
    "move",
    "move_many",
    "shell",
    "align",
    "boolean",
];

/// The steps this build can run, by type; `None` for the others.
fn execute(kind: &str, ctx: &Context) -> Option<Result<BTreeMap<String, Shape>, StepError>> {
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
            "move" => {
                let body = ctx.shape("body")?;
                let numbers = |key: &str| -> Option<Vec<f64>> {
                    ctx.input(key)
                        .and_then(Value::as_array)
                        .map(|l| l.iter().map(|v| v.as_f64().unwrap_or(f64::NAN)).collect())
                };
                let axis = match ctx.input("axis") {
                    None | Some(Value::Null) => None,
                    Some(_) => {
                        let axis = numbers("axis").unwrap_or_default();
                        if axis.len() != 3 || axis.iter().map(|v| v * v).sum::<f64>().sqrt() < 1e-9
                        {
                            return Err(failed("This rotation has no axis to turn about."));
                        }
                        Some(axis)
                    }
                };
                let pivot = match numbers("pivot").filter(|p| !p.is_empty()) {
                    Some(pivot) => pivot,
                    None => {
                        // Turned about the body's own centre.
                        let b = occt::bounds(&body).map_err(kernel)?;
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
                let moved = occt::move_shape(&body, &m).map_err(kernel)?;
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
            "align" => {
                let (name, placed) = crate::align::execute(ctx)?;
                Ok(BTreeMap::from([(name, placed)]))
            }
            "boolean" => combine(ctx),
            _ => unreachable!(),
        }
    };
    PORTED.contains(&kind).then(run)
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

/// Where saved results are read from: the model's cache and the file's blobs.
struct Store<'a> {
    original: Option<zip::ZipArchive<Cursor<&'a [u8]>>>,
    model_blobs: &'a BTreeMap<String, Vec<u8>>,
    decoded: HashMap<String, Shape>,
}

impl Store<'_> {
    fn blob(&mut self, sha: &str) -> Option<Vec<u8>> {
        if let Some(blob) = self.model_blobs.get(sha) {
            return Some(blob.clone());
        }
        let archive = self.original.as_mut()?;
        let mut entry = archive.by_name(&format!("geometry/{sha}.brep")).ok()?;
        let mut bytes = Vec::new();
        entry.read_to_end(&mut bytes).ok()?;
        (format!("{:x}", Sha256::digest(&bytes)) == sha).then_some(bytes)
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
            let sha = output["sha256"].as_str()?;
            let part = match output["kind"].as_str()? {
                "brep" => Part {
                    shape: self.decode(sha)?,
                    sha: Some(sha.to_string()),
                },
                "located" => {
                    let matrix: Vec<f64> = output["location"]
                        .as_array()?
                        .iter()
                        .filter_map(Value::as_f64)
                        .collect();
                    let base = self.decode(sha)?;
                    Part {
                        shape: Rc::new(occt::located(&base, &matrix).ok()?),
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
    new_blobs: BTreeMap<String, Vec<u8>>,
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
                    return Some(part.shape);
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
                return Some(part.shape);
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
        let bodies = self.inputs(&feature)?;
        self.finish(fid, feature, signature, bodies, suppressed)
    }

    /// `_input_context`: each bound name, from the step that wrote it.
    fn inputs(&mut self, feature: &Value) -> Result<HashMap<String, Shape>, StepError> {
        let mut bodies = HashMap::new();
        for (name, reference) in feature["bindings"].as_object().into_iter().flatten() {
            let source_id = reference["feature_id"].as_str().unwrap_or("");
            if !self.features.contains_key(source_id) {
                return Err(lost(format!(
                    "The object '{name}' this feature needs was deleted. Undo the deletion or re-select its input."
                )));
            }
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
                Some(part) => bodies.insert(name.clone(), part.shape),
                None => {
                    return Err(lost(format!(
                        "The output '{name}' this feature needs is unavailable."
                    )));
                }
            };
        }
        Ok(bodies)
    }

    /// The step run on its inputs, and its result stored.
    fn finish(
        &mut self,
        fid: &str,
        feature: Value,
        signature: String,
        bodies: HashMap<String, Shape>,
        suppressed: bool,
    ) -> Result<Outputs, StepError> {
        let names: Vec<String> = feature["outputs"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|n| n.as_str().map(String::from))
            .collect();
        let mut outputs: BTreeMap<String, Shape> = if suppressed {
            self.states
                .insert(fid.to_string(), ("suppressed".into(), String::new()));
            names
                .iter()
                .filter_map(|n| Some((n.clone(), bodies.get(n)?.clone())))
                .collect()
        } else {
            let kind = feature["type"].as_str().unwrap_or("");
            let ctx = Context {
                feature: &feature,
                bodies,
                parameters: &self.parameters,
                message: Default::default(),
            };
            let made = execute(kind, &ctx).ok_or_else(|| {
                failed("This step has to be rebuilt, and this version cannot build it yet. Edit it in SimpleCAD 0.4.")
            })??;
            if made.is_empty() {
                return Err(failed("This feature produced no geometry."));
            }
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
        for (name, shape) in outputs {
            let blob = occt::write_brep(&shape).map_err(kernel)?;
            let sha = format!("{:x}", Sha256::digest(&blob));
            encoded.insert(
                output_ids[&name].as_str().unwrap_or("").to_string(),
                json!({ "kind": "brep", "sha256": sha }),
            );
            self.new_blobs.insert(sha.clone(), blob);
            parts.insert(
                name,
                Part {
                    shape,
                    sha: Some(sha),
                },
            );
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
    blobs: &'a BTreeMap<String, Vec<u8>>,
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
/// description, or the error it gives.
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
        let outcome = rebuilder.inputs(feature).and_then(|bodies| {
            let ctx = Context { feature, bodies, parameters: &rebuilder.parameters, message: Default::default() };
            match kind {
                "align" => crate::align::solve(&ctx).map(|(_, _, p)| {
                    json!({ "transform": occt::trsf_values(&p.transform), "description": p.description })
                }),
                _ => Err(failed("not a checked step type")),
            }
        });
        found.push(match outcome {
            Ok(value) => json!({ "name": name, "result": value }),
            Err(error) => json!({ "name": name, "error": error.message }),
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
                     name: &str| {
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
        if !previous.contains_key(oid)
            && (consumed.contains(oid)
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
                if let Some(part) = outputs.get(name) {
                    last.retain(|(o, _)| o != oid);
                    last.push((
                        oid.clone(),
                        (name.clone(), Some(part.shape.clone()), fid.clone()),
                    ));
                }
            }
            Err(_) => {
                keep_last(&mut rebuilder, &mut last, oid, fid, name);
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
            keep_last(&mut rebuilder, &mut last, &oid, fid, &name);
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
                .find(|p| Rc::ptr_eq(&p.shape, shape))
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
                    model.blobs.insert(sha.clone(), blob);
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

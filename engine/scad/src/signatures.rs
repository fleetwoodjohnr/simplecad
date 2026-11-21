//! Cache signatures (`simplecad/core/result_store.py` `signatures`): a hash of
//! each step's recipe, the parameters it reads (transitively), and the
//! signatures of the steps upstream of it. Equal signatures mean equal results,
//! so a result either app saved can be reused by the other.
//!
//! Works on `model.json` as saved by formats 2–4, whose features are already in
//! the normalised form the Python app hashes (its load and graph preparation
//! leave them unchanged).

use crate::params::dependencies;
use crate::project::CACHE_VERSION;
use crate::pyjson;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

/// `document.OUTPUT_NAME_INPUTS`: inputs that list output names, hashed by output id.
const OUTPUT_NAME_INPUTS: &[&str] = &["names", "connector_names"];

/// `OutputRef`: which step wrote an object, and the object.
#[derive(Clone)]
struct OutputRef {
    feature_id: String,
    object_id: String,
}

impl OutputRef {
    fn from(value: &Value) -> Option<Self> {
        Some(Self {
            feature_id: value
                .get("feature_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            object_id: value.get("object_id")?.as_str()?.to_string(),
        })
    }

    fn to_value(&self) -> Value {
        json!({ "feature_id": self.feature_id, "object_id": self.object_id })
    }
}

fn str_map(value: Option<&Value>) -> BTreeMap<String, String> {
    value
        .and_then(Value::as_object)
        .map(|map| {
            map.iter()
                .filter_map(|(k, v)| Some((k.clone(), v.as_str()?.to_string())))
                .collect()
        })
        .unwrap_or_default()
}

fn bindings(feature: &Value) -> BTreeMap<String, OutputRef> {
    feature
        .get("bindings")
        .and_then(Value::as_object)
        .map(|map| {
            map.iter()
                .filter_map(|(k, v)| Some((k.clone(), OutputRef::from(v)?)))
                .collect()
        })
        .unwrap_or_default()
}

/// The step ids a feature's face/edge references point at (`Feature.shape_refs`).
fn shape_ref_features(value: &Value, out: &mut Vec<String>) {
    match value {
        Value::Object(map) if map.contains_key("__ref__") => {
            if let Some(id) = map["__ref__"].get("feature_id").and_then(Value::as_str) {
                out.push(id.to_string());
            }
        }
        Value::Object(map) if map.contains_key("__body__") => {}
        Value::Object(map) => map.values().for_each(|v| shape_ref_features(v, out)),
        Value::Array(items) => items.iter().for_each(|v| shape_ref_features(v, out)),
        _ => {}
    }
}

/// `DependencyGraph.forward`: the steps a feature reads from.
pub(crate) fn upstream(feature: &Value, id: &str) -> BTreeSet<String> {
    let mut deps: BTreeSet<String> = bindings(feature)
        .into_values()
        .map(|r| r.feature_id)
        .filter(|f| !f.is_empty())
        .collect();
    let mut refs = Vec::new();
    if let Some(inputs) = feature.get("inputs") {
        shape_ref_features(inputs, &mut refs);
    }
    deps.extend(refs.into_iter().filter(|f| !f.is_empty() && f != id));
    deps.remove(id);
    deps
}

/// `Feature.parameter_names`: what the top-level text inputs name. Three types
/// read only their numeric inputs (the only overrides in the Python registry; a
/// test there would catch a new one): text, face_texture, edge_finish.
fn parameter_names(feature: &Value) -> BTreeSet<String> {
    let Some(inputs) = feature.get("inputs").and_then(Value::as_object) else {
        return BTreeSet::new();
    };
    let named = |keys: &[&str]| -> BTreeSet<String> {
        keys.iter()
            .filter_map(|k| inputs.get(*k)?.as_str())
            .flat_map(dependencies)
            .collect()
    };
    match feature.get("type").and_then(Value::as_str) {
        Some("text") => named(&["text_height", "depth", "offset_x", "offset_y", "rotation"]),
        Some("face_texture") => named(&[
            "size",
            "spacing",
            "depth",
            "rotation",
            "offset_u",
            "offset_v",
            "edge_clearance",
            "hole_clearance",
            "text_clearance",
            "other_clearance",
        ]),
        Some("sketch") => inputs["sketch"]["constraints"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|constraint| constraint["driving"].as_bool().unwrap_or(true))
            .filter_map(|constraint| {
                constraint
                    .get("expression")
                    .or_else(|| constraint.get("value"))?
                    .as_str()
            })
            .flat_map(dependencies)
            .collect(),
        Some("edge_finish") => inputs
            .get("edge_specs")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .flat_map(|spec| {
                ["radius", "distance", "distance2", "angle"]
                    .into_iter()
                    .filter_map(|k| spec.get(k)?.as_str())
            })
            .flat_map(dependencies)
            .collect(),
        _ => inputs
            .values()
            .filter_map(Value::as_str)
            .flat_map(dependencies)
            .collect(),
    }
}

/// The inputs as hashed: body references by the object they resolve to.
fn canonical(value: &Value, bindings: &BTreeMap<String, OutputRef>) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.iter().map(|v| canonical(v, bindings)).collect()),
        Value::Object(map) if map.contains_key("__body__") => {
            let name = &map["__body__"];
            let resolved = name
                .as_str()
                .and_then(|n| bindings.get(n))
                .map(OutputRef::to_value);
            json!({ "__body__": resolved.unwrap_or_else(|| name.clone()) })
        }
        Value::Object(map) if map.contains_key("__ref__") => {
            let mut reference = map["__ref__"].as_object().cloned().unwrap_or_default();
            let binding = reference
                .get("body")
                .and_then(Value::as_str)
                .and_then(|b| bindings.get(b));
            let body = match binding {
                Some(r) => Value::String(r.object_id.clone()),
                None => reference
                    .get("body_id")
                    .cloned()
                    .unwrap_or_else(|| Value::String(String::new())),
            };
            reference.insert("body".into(), body);
            reference.remove("body_id");
            json!({ "__ref__": reference })
        }
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, v)| (k.clone(), canonical(v, bindings)))
                .collect(),
        ),
        _ => value.clone(),
    }
}

/// Every feature's signature, by feature id. `None` for a model these rules do
/// not cover (an output without an id); the caller then rebuilds rather than trusts.
pub fn signatures(model: &Value) -> Option<BTreeMap<String, String>> {
    let features: Vec<&Value> = model.get("features")?.as_array()?.iter().collect();
    let by_id: BTreeMap<&str, &Value> = features
        .iter()
        .filter_map(|f| Some((f.get("id")?.as_str()?, *f)))
        .collect();
    let parameters: BTreeMap<String, Value> = model
        .get("parameters")
        .and_then(Value::as_array)
        .map(|list| {
            list.iter()
                .filter_map(|p| Some((p.get("name")?.as_str()?.to_string(), p.clone())))
                .collect()
        })
        .unwrap_or_default();

    let mut result = BTreeMap::new();
    // Upstream first; the Python app walks the graph's topological order, which
    // gives the same values since a signature depends only on upstream ones.
    fn visit(
        id: &str,
        by_id: &BTreeMap<&str, &Value>,
        parameters: &BTreeMap<String, Value>,
        result: &mut BTreeMap<String, String>,
        visiting: &mut BTreeSet<String>,
    ) -> Option<()> {
        if result.contains_key(id) {
            return Some(());
        }
        // A cycle is a broken project; the Python app refuses it too.
        if !visiting.insert(id.to_string()) {
            return None;
        }
        let feature = by_id[id];
        let deps = upstream(feature, id);
        for dep in &deps {
            if by_id.contains_key(dep.as_str()) {
                visit(dep, by_id, parameters, result, visiting)?;
            }
        }
        let refs = bindings(feature);
        let output_ids = str_map(feature.get("output_ids"));

        let mut names = parameter_names(feature);
        let mut todo: Vec<String> = names.iter().cloned().collect();
        while let Some(name) = todo.pop() {
            if let Some(expression) = parameters
                .get(&name)
                .and_then(|p| p.get("expression"))
                .and_then(Value::as_str)
            {
                for found in dependencies(expression) {
                    if names.insert(found.clone()) {
                        todo.push(found);
                    }
                }
            }
        }

        let mut inputs = canonical(feature.get("inputs").unwrap_or(&json!({})), &refs);
        if let Some(map) = inputs.as_object_mut() {
            for key in OUTPUT_NAME_INPUTS {
                if let Some(Value::Array(list)) = map.get(*key) {
                    let ids = list
                        .iter()
                        .map(|n| match n.as_str().and_then(|s| output_ids.get(s)) {
                            Some(id) => Value::String(id.clone()),
                            None => n.clone(),
                        })
                        .collect();
                    map.insert((*key).into(), Value::Array(ids));
                }
            }
        }
        let names_of = |key: &str| -> Vec<String> {
            feature
                .get(key)
                .and_then(Value::as_array)
                .map(|l| {
                    l.iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default()
        };
        let outputs = names_of("outputs")
            .iter()
            .map(|n| output_ids.get(n).cloned())
            .collect::<Option<Vec<_>>>()?;
        let dropped: Vec<String> = names_of("dropped")
            .iter()
            .map(|n| output_ids.get(n).cloned().unwrap_or_else(|| n.clone()))
            .collect();
        let mut sorted_ids: Vec<&String> = output_ids.values().collect();
        sorted_ids.sort();
        let mut bound: Vec<(String, String)> = refs
            .values()
            .map(|r| (r.feature_id.clone(), r.object_id.clone()))
            .collect();
        bound.sort();

        let mut state = Map::new();
        state.insert("id".into(), json!(id));
        state.insert(
            "type".into(),
            feature.get("type").cloned().unwrap_or(Value::Null),
        );
        state.insert("inputs".into(), inputs);
        state.insert("outputs".into(), json!(outputs));
        state.insert("output_ids".into(), json!(sorted_ids));
        state.insert(
            "bindings".into(),
            json!(bound.iter().map(|(f, o)| [f, o]).collect::<Vec<_>>()),
        );
        state.insert(
            "suppressed".into(),
            feature.get("suppressed").cloned().unwrap_or(json!(false)),
        );
        state.insert("dropped".into(), json!(dropped));
        let parameters_used: Map<String, Value> = names
            .iter()
            .map(|n| (n.clone(), parameters.get(n).cloned().unwrap_or(Value::Null)))
            .collect();
        let upstream_signatures: Map<String, Value> = deps
            .iter()
            .map(|d| {
                (
                    d.clone(),
                    Value::String(result.get(d).cloned().unwrap_or_else(|| "missing".into())),
                )
            })
            .collect();
        let payload = json!({
            "version": CACHE_VERSION,
            "feature": state,
            "parameters": parameters_used,
            "upstream": upstream_signatures,
        });
        result.insert(
            id.to_string(),
            format!("{:x}", Sha256::digest(pyjson::dumps(&payload).as_bytes())),
        );
        visiting.remove(id);
        Some(())
    }
    let mut visiting = BTreeSet::new();
    for id in by_id.keys() {
        visit(id, &by_id, &parameters, &mut result, &mut visiting)?;
    }
    Some(result)
}

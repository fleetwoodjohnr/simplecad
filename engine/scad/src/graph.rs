//! A project's document as the Python app holds it once loaded:
//! `Document.from_dict(model).to_dict()` (`simplecad/core/document.py`), whose
//! dependency graph (`core/graph.py` `DependencyGraph`) gives every step its
//! bindings and output ids, and every body its id and producer. Format 1 files
//! were written before those existed; this is their migration, and for newer
//! files it changes nothing but defaults.

use serde_json::{Map, Value, json};
use std::collections::{BTreeSet, HashMap};

/// `graph.output_id`: the id a step's output gets when first allocated.
pub fn output_id(feature_id: &str, name: &str) -> String {
    uuid::Uuid::new_v5(
        &uuid::Uuid::NAMESPACE_URL,
        format!("simplecad:output:{feature_id}:{name}").as_bytes(),
    )
    .simple()
    .to_string()
}

fn text(value: Option<&Value>, default: &str) -> Value {
    value.cloned().unwrap_or_else(|| json!(default))
}

/// A body alias as Python's `str()` of a decoded input gives it.
fn alias(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Object(map) if map.contains_key("__body__") => alias(&map["__body__"]),
        other => other.to_string(),
    }
}

/// `Fingerprint.from_dict(...).to_dict()`.
fn fingerprint(data: &Value) -> Value {
    let direction = data
        .get("direction")
        .filter(|d| !d.is_null() && d.as_array().is_none_or(|a| !a.is_empty()));
    json!({
        "kind": data.get("kind").cloned().unwrap_or(Value::Null),
        "geometry": text(data.get("geometry"), "unknown"),
        "measure": data.get("measure").and_then(Value::as_f64).unwrap_or(0.0),
        "center": data.get("center").cloned().unwrap_or(json!([0.0, 0.0, 0.0])),
        "direction": direction.cloned().unwrap_or(Value::Null),
        "radius": data.get("radius").cloned().unwrap_or(Value::Null),
        "neighbours": data.get("neighbours").cloned().unwrap_or(json!([])),
    })
}

/// `_encode_inputs(_decode_inputs(inputs))`: face references and body
/// references in their canonical form, everything else as it was.
fn inputs(value: &Value) -> Value {
    match value {
        Value::Object(map) if map.contains_key("__ref__") => {
            let r = &map["__ref__"];
            let hint = r.get("index_hint").and_then(Value::as_f64).unwrap_or(-1.0) as i64;
            json!({ "__ref__": {
                "feature_id": r.get("feature_id").cloned().unwrap_or(Value::Null),
                "kind": r.get("kind").cloned().unwrap_or(Value::Null),
                "fingerprint": fingerprint(r.get("fingerprint").unwrap_or(&Value::Null)),
                "index_hint": hint,
                "role": text(r.get("role"), ""),
                "body": text(r.get("body"), ""),
                "body_id": text(r.get("body_id"), ""),
            }})
        }
        Value::Object(map) if map.contains_key("__body__") => {
            let mut out = Map::new();
            out.insert("__body__".into(), json!(alias(&map["__body__"])));
            let object = map.get("object_id").and_then(Value::as_str).unwrap_or("");
            if !object.is_empty() {
                out.insert("object_id".into(), json!(object));
                out.insert("feature_id".into(), text(map.get("feature_id"), ""));
            }
            Value::Object(out)
        }
        Value::Object(map) => {
            Value::Object(map.iter().map(|(k, v)| (k.clone(), inputs(v))).collect())
        }
        Value::Array(items) => Value::Array(items.iter().map(inputs).collect()),
        other => other.clone(),
    }
}

/// Body aliases (`__body__`) in input order, with their saved object id if any.
fn body_refs<'a>(value: &'a Value, out: &mut Vec<(String, &'a Value)>) {
    match value {
        Value::Object(map) if map.contains_key("__ref__") => {}
        Value::Object(map) if map.contains_key("__body__") => out.push((alias(value), value)),
        Value::Object(map) => map.values().for_each(|v| body_refs(v, out)),
        Value::Array(items) => items.iter().for_each(|v| body_refs(v, out)),
        _ => {}
    }
}

/// The bodies named by face/edge references.
fn ref_bodies(value: &Value, out: &mut Vec<String>) {
    match value {
        Value::Object(map) if map.contains_key("__ref__") => {
            if let Some(body) = map["__ref__"].get("body").and_then(Value::as_str)
                && !body.is_empty()
            {
                out.push(body.to_string());
            }
        }
        Value::Object(map) if map.contains_key("__body__") => {}
        Value::Object(map) => map.values().for_each(|v| ref_bodies(v, out)),
        Value::Array(items) => items.iter().for_each(|v| ref_bodies(v, out)),
        _ => {}
    }
}

/// `graph.input_names`: every name a step reads, in order, once each.
pub(crate) fn input_names(kind: &str, inputs: &Value) -> Vec<String> {
    let mut refs = Vec::new();
    body_refs(inputs, &mut refs);
    let mut names: Vec<String> = refs.into_iter().map(|(name, _)| name).collect();
    ref_bodies(inputs, &mut names);
    let keys: &[&str] = match kind {
        "extrude" | "revolve" => &["sketch"],
        "sweep" => &["profile", "path"],
        "loft" => &["sketches"],
        "path_pattern" => &["path"],
        _ => &[],
    };
    for key in keys {
        let values = match inputs.get(*key) {
            Some(Value::Array(list)) => list.clone(),
            Some(v) => vec![v.clone()],
            None => vec![],
        };
        for value in values {
            let truthy = match &value {
                Value::Null | Value::Bool(false) => false,
                Value::String(s) => !s.is_empty(),
                Value::Array(a) => !a.is_empty(),
                Value::Object(o) => !o.is_empty(),
                Value::Number(n) => n.as_f64() != Some(0.0),
                Value::Bool(true) => true,
            };
            if truthy {
                names.push(format!("__sketch__{}", alias(&value)));
            }
        }
    }
    let mut seen = BTreeSet::new();
    names.retain(|n| seen.insert(n.clone()));
    names
}

/// `graph.declared_outputs`, for a step saved without its outputs.
fn declared_outputs(kind: &str, name: &str, inputs: &Value) -> Vec<String> {
    let get = |k: &str| inputs.get(k);
    let list = |k: &str| -> Vec<String> {
        get(k)
            .and_then(Value::as_array)
            .map(|l| l.iter().map(alias).collect())
            .unwrap_or_default()
    };
    let named = || {
        if name.is_empty() {
            vec![]
        } else {
            vec![name.to_string()]
        }
    };
    match kind {
        "sketch" => vec![format!("__sketch__{name}")],
        "offset_plane"
        | "midplane"
        | "plane_at_angle"
        | "plane_through_points"
        | "tangent_plane"
        | "construction_axis"
        | "construction_point" => vec![format!("__construction__{name}")],
        "split" => {
            let names = list("names");
            if names.is_empty() {
                let source = get("body").map(alias).unwrap_or_default();
                vec![format!("{source} A"), format!("{source} B")]
            } else {
                names
            }
        }
        "hinge" if get("type").map(alias).as_deref() == Some("living") => {
            vec![get("body_a").map(alias).unwrap_or_default()]
        }
        "hinge" | "threaded_connection" | "clip_joint" => {
            let extra = if kind == "hinge" {
                "names"
            } else {
                "connector_names"
            };
            ["body_a", "body_b"]
                .iter()
                .filter_map(|k| get(k).map(alias))
                .chain(list(extra))
                .collect()
        }
        "arrange" | "move_many" => list("bodies"),
        "section_replace" => vec![get("target").map(alias).unwrap_or_default()],
        "matching_bolt" | "matching_nut" => named(),
        _ => match get("body") {
            Some(body) => vec![alias(body)],
            None => named(),
        },
    }
}

/// `{"feature_id", "object_id"}`, as `OutputRef.to_dict` writes it.
fn output_ref(feature_id: &str, object_id: &str) -> Value {
    json!({ "feature_id": feature_id, "object_id": object_id })
}

/// Rewrites `model`'s document parts (`DOCUMENT_KEYS`) as the Python app saves
/// them after loading; the rest (format, history, cache, geometry) is untouched.
pub fn normalise(model: &mut Value) {
    let Some(payload) = model.as_object_mut() else {
        return;
    };
    // Lists the Python app could not load are left for the reader to refuse.
    if ["features", "bodies", "groups", "parameters"]
        .iter()
        .any(|k| payload.get(*k).is_some_and(|v| !v.is_array()))
    {
        return;
    }
    let list = |payload: &Map<String, Value>, key: &str| -> Vec<Value> {
        payload
            .get(key)
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    };

    // Features, then the graph over them, in document order (`DependencyGraph.__init__`).
    let mut latest: HashMap<String, (String, String)> = HashMap::new();
    let mut features = Vec::new();
    for raw in list(payload, "features") {
        let id = raw
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let kind = raw
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let name = raw
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let inputs = inputs(raw.get("inputs").unwrap_or(&json!({})));
        let mut outputs: Vec<String> = raw
            .get("outputs")
            .and_then(Value::as_array)
            .map(|l| l.iter().map(alias).collect())
            .unwrap_or_default();
        if outputs.is_empty() {
            outputs = declared_outputs(&kind, &name, &inputs);
        }
        let needed = input_names(&kind, &inputs);
        let mut bindings: Map<String, Value> = raw
            .get("bindings")
            .and_then(Value::as_object)
            .map(|m| {
                m.iter()
                    .filter(|(n, _)| needed.contains(n))
                    .map(|(n, r)| {
                        let field = |k| r.get(k).and_then(Value::as_str).unwrap_or("");
                        (
                            n.clone(),
                            output_ref(field("feature_id"), field("object_id")),
                        )
                    })
                    .collect()
            })
            .unwrap_or_default();
        let mut refs = Vec::new();
        body_refs(&inputs, &mut refs);
        let mut explicit: HashMap<String, (String, String)> = HashMap::new();
        for (alias, value) in refs {
            let object = value.get("object_id").and_then(Value::as_str).unwrap_or("");
            if !object.is_empty() {
                let feature = value
                    .get("feature_id")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                explicit
                    .entry(alias)
                    .or_insert((feature.into(), object.into()));
            }
        }
        for name in &needed {
            if let Some((feature, object)) = explicit.get(name)
                && bindings
                    .get(name)
                    .and_then(|r| r.get("object_id")?.as_str())
                    != Some(object)
            {
                bindings.insert(name.clone(), output_ref(feature, object));
            }
            if !bindings.contains_key(name) {
                let (feature, object) = latest
                    .get(name)
                    .cloned()
                    .unwrap_or_else(|| (String::new(), output_id(&id, &format!("missing:{name}"))));
                bindings.insert(name.clone(), output_ref(&feature, &object));
            }
        }
        let mut output_ids: Map<String, Value> = raw
            .get("output_ids")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        for name in &outputs {
            if !output_ids.contains_key(name) {
                let object = match bindings
                    .get(name)
                    .and_then(|r| r.get("object_id")?.as_str())
                {
                    Some(source) => source.to_string(),
                    None => output_id(&id, name),
                };
                output_ids.insert(name.clone(), json!(object));
            }
            let object = output_ids[name].as_str().unwrap_or("").to_string();
            latest.insert(name.clone(), (id.clone(), object));
        }
        features.push(json!({
            "id": id, "type": kind, "name": name, "inputs": inputs, "outputs": outputs,
            "output_ids": output_ids, "bindings": bindings,
            "suppressed": raw.get("suppressed").cloned().unwrap_or(json!(false)),
            "dropped": raw.get("dropped").cloned().unwrap_or(json!([])),
        }));
    }

    // Bodies by name, the last of a repeated name winning as in Python's dict.
    let mut bodies: Vec<Value> = Vec::new();
    let mut ids_before: HashMap<String, String> = HashMap::new();
    for raw in list(payload, "bodies") {
        let Some(name) = raw.get("name").and_then(Value::as_str).map(String::from) else {
            continue;
        };
        let producer = raw.get("producer").and_then(Value::as_str);
        let mut id = raw
            .get("id")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(String::from)
            .unwrap_or_else(|| output_id(producer.unwrap_or("legacy"), &name));
        ids_before.insert(id.clone(), name.clone());
        let mut producer = producer.unwrap_or("").to_string();
        if let Some((feature, object)) = latest.get(&name) {
            id = object.clone();
            producer = feature.clone();
        }
        let body = json!({
            "id": id, "name": name,
            "visible": raw.get("visible").cloned().unwrap_or(json!(true)),
            "color": raw.get("color").cloned().unwrap_or(Value::Null),
            "producer": producer,
            "metadata": raw.get("metadata").cloned().unwrap_or(json!({})),
        });
        match bodies.iter_mut().find(|b| b["name"] == body["name"]) {
            Some(slot) => *slot = body,
            None => bodies.push(body),
        }
    }

    let raw_groups = list(payload, "groups");
    let group_id = |g: &Value| -> String {
        g.get("id")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(String::from)
            .unwrap_or_else(|| output_id("group", &alias(&g["name"])))
    };
    for group in &raw_groups {
        ids_before.insert(group_id(group), alias(&group["name"]));
    }
    // A member names a group in preference to a body.
    let id_of: HashMap<String, String> = bodies
        .iter()
        .map(|b| (alias(&b["name"]), alias(&b["id"])))
        .chain(raw_groups.iter().map(|g| (alias(&g["name"]), group_id(g))))
        .collect();
    let groups: Vec<Value> = raw_groups
        .iter()
        .filter(|g| g.get("name").is_some())
        .map(|g| {
            let members: Vec<String> = match g.get("member_ids").and_then(Value::as_array) {
                Some(ids) => ids
                    .iter()
                    .filter_map(|i| ids_before.get(&alias(i)).cloned())
                    .collect(),
                None => g
                    .get("members")
                    .and_then(Value::as_array)
                    .map(|l| l.iter().map(alias).collect())
                    .unwrap_or_default(),
            };
            let member_ids: Vec<&String> = members.iter().filter_map(|m| id_of.get(m)).collect();
            json!({
                "name": alias(&g["name"]), "id": group_id(g), "members": members,
                "visible": g.get("visible").cloned().unwrap_or(json!(true)),
                "expanded": g.get("expanded").cloned().unwrap_or(json!(true)),
                "member_ids": member_ids,
            })
        })
        .collect();

    let mut retired: Vec<String> = list(payload, "retired_ids").iter().map(alias).collect();
    retired.sort();
    retired.dedup();
    let parameters: Vec<Value> = list(payload, "parameters")
        .iter()
        .map(|p| {
            json!({
                "name": p.get("name").cloned().unwrap_or(Value::Null),
                "expression": p.get("expression").cloned().unwrap_or(Value::Null),
                "dimension": text(p.get("dimension"), "length"),
                "comment": text(p.get("comment"), ""),
            })
        })
        .collect();
    let assets: BTreeSet<String> = features
        .iter()
        .filter(|f| f["type"] == "embedded_import")
        .filter_map(|f| f["inputs"].get("asset"))
        .filter(|a| !matches!(a, Value::Null | Value::Bool(false)) && a.as_str() != Some(""))
        .map(alias)
        .collect();

    payload.insert("title".into(), text(payload.get("title"), "Untitled"));
    let counters = payload.get("counters").cloned().unwrap_or(json!({}));
    payload.insert("counters".into(), counters);
    payload.insert("retired_ids".into(), json!(retired));
    payload.insert("parameters".into(), json!(parameters));
    payload.insert("features".into(), json!(features));
    payload.insert("bodies".into(), json!(bodies));
    payload.insert("groups".into(), json!(groups));
    let metadata = payload.get("metadata").cloned().unwrap_or(json!({}));
    payload.insert("metadata".into(), metadata);
    payload.insert("assets".into(), json!(assets));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn output_ids_are_the_python_apps() {
        // uuid.uuid5(uuid.NAMESPACE_URL, "simplecad:output:f1:Box").hex
        assert_eq!(output_id("f1", "Box"), "f60ac9e7a61f55bc827c61760bdaa772");
    }
}

//! Editing and saving a project as the Python app would, on its `model.json`
//! (`core/document.py`, `core/graph.py`, `core/changes.py`, `core/history.py`,
//! `core/project.py`). A step added here is a real feature of that model: the
//! Python app reopens the file, finds its cached result current, rebuilds it if
//! asked, and can undo it.

use crate::project::CACHE_VERSION;
use crate::signatures::signatures;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::io::{Cursor, Read, Write};

/// The parts of `model.json` that are the document (`Document.to_dict`); the rest
/// (format, history, cache, geometry, diagnostics) is what saving adds.
const DOCUMENT_KEYS: &[&str] = &[
    "title",
    "counters",
    "retired_ids",
    "parameters",
    "features",
    "bodies",
    "groups",
    "metadata",
    "assets",
];
/// `changes.COLLECTIONS`: lists patched entity by entity, keyed by this field.
const COLLECTIONS: &[(&str, &str)] = &[
    ("features", "id"),
    ("bodies", "id"),
    ("groups", "id"),
    ("parameters", "name"),
];

/// A saved project being edited: its `model.json` and the geometry blobs it gained.
#[derive(Clone)]
pub struct Model {
    pub payload: Value,
    /// New `geometry/<sha>.brep` entries, by sha.
    pub blobs: BTreeMap<String, Vec<u8>>,
}

impl Model {
    pub fn new(payload: Value) -> Self {
        Self {
            payload,
            blobs: BTreeMap::new(),
        }
    }

    /// A project opened for editing. A format 1 file's bodies are its steps'
    /// results, adopted as the Python app's `Rebuilder.adopt_baseline` does when
    /// every body was read, so that saving gives each its cached geometry.
    pub fn open(project: &crate::project::Project) -> Self {
        let mut model = Self::new(project.model.clone());
        if project
            .model
            .get("format")
            .and_then(Value::as_i64)
            .unwrap_or(1)
            >= 2
        {
            return model;
        }
        let latest = model.latest();
        let adopted = project
            .bodies
            .iter()
            .all(|b| b.brep.is_some() && latest.contains_key(&b.name));
        let signatures = if adopted {
            signatures(&model.payload)
        } else {
            None
        }
        .unwrap_or_default();
        let mut outputs: BTreeMap<String, Map<String, Value>> = BTreeMap::new();
        let payload = model.payload.as_object_mut().unwrap();
        let mut geometry = Map::new();
        for body in &project.bodies {
            let (Some(brep), Some((feature, object))) = (&body.brep, latest.get(&body.name)) else {
                continue;
            };
            let sha = format!("{:x}", Sha256::digest(brep));
            let signature = signatures.get(feature);
            geometry.insert(
                object.clone(),
                json!({ "sha256": sha, "feature_id": feature,
                "signature": signature, "current": signature.is_some() }),
            );
            outputs
                .entry(feature.clone())
                .or_default()
                .insert(object.clone(), json!({ "kind": "brep", "sha256": sha }));
            model.blobs.insert(sha, brep.clone());
        }
        let mut entries = Map::new();
        for feature in payload
            .get("features")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let id = feature["id"].as_str().unwrap_or("");
            let (Some(signature), Some(found)) = (signatures.get(id), outputs.get(id)) else {
                continue;
            };
            // A partial multi-output result must not pass for the whole step.
            let dropped = &feature["dropped"];
            let wanted: Vec<&str> = feature["outputs"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|n| !dropped.as_array().is_some_and(|d| d.contains(n)))
                .filter_map(|n| feature["output_ids"].get(n.as_str()?)?.as_str())
                .collect();
            if wanted.len() == found.len() && wanted.iter().all(|o| found.contains_key(*o)) {
                entries.insert(
                    signature.clone(),
                    json!({ "feature_id": id, "outputs": found,
                    "message": "", "version": CACHE_VERSION }),
                );
            }
        }
        let diagnostics: Map<String, Value> = payload
            .get("features")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|f| {
                Some((
                    f["id"].as_str()?.to_string(),
                    json!({ "state": "ok", "message": "" }),
                ))
            })
            .collect();
        payload.insert(
            "cache".into(),
            json!({ "version": CACHE_VERSION, "entries": entries }),
        );
        payload.insert("geometry".into(), Value::Object(geometry));
        payload.insert("diagnostics".into(), Value::Object(diagnostics));
        model
    }

    /// `Document.to_dict()`.
    pub fn document(&self) -> Value {
        let map: Map<String, Value> = DOCUMENT_KEYS
            .iter()
            .filter_map(|k| Some((k.to_string(), self.payload.get(*k)?.clone())))
            .collect();
        Value::Object(map)
    }

    fn list(&self, key: &str) -> Vec<Value> {
        self.payload
            .get(key)
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    /// `Document.unique_name`: a display name that never recycles a retired suffix.
    fn unique_name(&mut self, base: &str) -> String {
        let named = |key: &str| -> Vec<String> {
            self.list(key)
                .iter()
                .filter_map(|v| v.get("name")?.as_str().map(String::from))
                .collect()
        };
        let taken: HashSet<String> = named("features")
            .into_iter()
            .chain(named("bodies"))
            .chain(named("groups"))
            .collect();
        let counters = self
            .payload
            .as_object_mut()
            .unwrap()
            .entry("counters")
            .or_insert_with(|| json!({}));
        let mut suffix = counters.get(base).and_then(Value::as_i64).unwrap_or(1);
        loop {
            let candidate = if suffix == 1 {
                base.to_string()
            } else {
                format!("{base}{suffix}")
            };
            suffix += 1;
            if !taken.contains(&candidate) {
                counters[base] = json!(suffix);
                return candidate;
            }
        }
    }

    /// `DependencyGraph.latest`: the step and object that last wrote each body name.
    fn latest(&self) -> BTreeMap<String, (String, String)> {
        let mut latest = BTreeMap::new();
        for feature in self.list("features") {
            let id = feature
                .get("id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            for name in feature
                .get("outputs")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
            {
                if let Some(object) = feature
                    .get("output_ids")
                    .and_then(|o| o.get(name))
                    .and_then(Value::as_str)
                {
                    latest.insert(name.to_string(), (id.clone(), object.to_string()));
                }
            }
        }
        latest
    }

    /// A face reference as `naming.make_ref` writes it, anchored to the body's producing step.
    pub fn face_ref(&self, body: &str, index: u32, fingerprint: Value) -> Value {
        let producer = self
            .list("bodies")
            .iter()
            .find(|b| b.get("name").and_then(Value::as_str) == Some(body))
            .and_then(|b| b.get("producer")?.as_str().map(String::from))
            .unwrap_or_default();
        json!({
            "feature_id": producer, "kind": "face", "fingerprint": fingerprint,
            "index_hint": index, "role": "", "body": body, "body_id": "",
        })
    }

    /// The Pull tool's step on `body`, its result `brep`: the feature the Python
    /// app's `add_feature` and dependency graph would write, its cached result and
    /// current geometry, and its undo step. Returns the new feature's id.
    pub fn push_pull(
        &mut self,
        body: &str,
        face: Value,
        distance: &str,
        brep: Vec<u8>,
    ) -> Result<String, String> {
        let id = new_feature_id();
        self.push_pull_as(&id, body, face, distance, brep)?;
        Ok(id)
    }

    fn push_pull_as(
        &mut self,
        id: &str,
        body: &str,
        face: Value,
        distance: &str,
        brep: Vec<u8>,
    ) -> Result<(), String> {
        let before = self.document();
        let (producer, object) = self
            .latest()
            .remove(body)
            .ok_or_else(|| format!("No step writes the body \"{body}\"."))?;
        let name = self.unique_name("Pull");
        let feature = json!({
            "id": id, "type": "push_pull", "name": name,
            "inputs": { "body": { "__body__": body }, "face": { "__ref__": face }, "distance": distance },
            "outputs": [body],
            "output_ids": { body: object },
            "bindings": { body: { "feature_id": producer, "object_id": object } },
            "suppressed": false,
            "dropped": [],
        });
        let payload = self.payload.as_object_mut().unwrap();
        payload
            .entry("features")
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .unwrap()
            .push(feature);
        for entry in payload
            .entry("bodies")
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .unwrap()
        {
            if entry.get("name").and_then(Value::as_str) == Some(body) {
                entry["producer"] = json!(id);
            }
        }

        self.record(id, "Pull", &object, brep, before)
    }

    /// `History.undo` on the saved history: the newest step taken back, and its
    /// redo step written as the Python app writes it. Each body shows its new
    /// producer's cached result, as after the Python app's rebuild.
    pub fn undo(&mut self) -> Result<(), String> {
        let unreadable = || "This project's history could not be read.".to_string();
        let action = self.payload["history"]["undo"]
            .as_array_mut()
            .and_then(Vec::pop)
            .ok_or("There is nothing to undo.")?;
        let patch = &action["patch"];
        let current = signatures(&self.payload).ok_or_else(unreadable)?;
        let redo = self.inverse_patch(patch);
        let old = self.payload.clone();
        let producers = self.latest();
        let stale = self.apply_patch(patch);
        let mut stale_all = dependents(&old, &stale);
        stale_all.extend(dependents(&self.payload, &stale));
        let mut cache_keys: Vec<&String> =
            stale_all.iter().filter_map(|f| current.get(f)).collect();
        cache_keys.sort();
        let entry = json!({ "label": action["label"], "patch": redo, "cache_keys": cache_keys });
        self.payload["history"]
            .as_object_mut()
            .ok_or_else(unreadable)?
            .entry("redo")
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .ok_or_else(unreadable)?
            .push(entry);
        self.show_cached_results(&producers)
    }

    /// `History._inverse_patch`: what puts back the entities `patch` names, as they are now.
    fn inverse_patch(&self, patch: &Value) -> Value {
        let document = self.document();
        let mut inverse = json!({ "values": {}, "collections": {} });
        for key in patch["values"]
            .as_object()
            .into_iter()
            .flatten()
            .map(|(k, _)| k)
        {
            let value = match key.as_str() {
                "title" | "counters" | "retired_ids" | "metadata" | "assets" => {
                    document[key].clone()
                }
                _ => Value::Null,
            };
            inverse["values"][key] = value;
        }
        for (key, change) in patch["sets"].as_object().into_iter().flatten() {
            let present: HashSet<&str> = match key.as_str() {
                "retired_ids" => document["retired_ids"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .collect(),
                _ => HashSet::new(),
            };
            let items = |k: &str| change[k].as_array().cloned().unwrap_or_default();
            inverse["sets"][key] = json!({
                "add": items("remove").into_iter().filter(|i| i.as_str().is_some_and(|i| present.contains(i))).collect::<Vec<_>>(),
                "remove": items("add").into_iter().filter(|i| !i.as_str().is_some_and(|i| present.contains(i))).collect::<Vec<_>>(),
            });
        }
        for (key, change) in patch["keyed"].as_object().into_iter().flatten() {
            let empty = Map::new();
            let mapping = document[key].as_object().unwrap_or(&empty);
            let touched = touched(change);
            inverse["keyed"][key] = json!({
                "set": touched.iter().filter_map(|k| Some((k.clone(), mapping.get(k)?.clone()))).collect::<Map<_, _>>(),
                "remove": touched.iter().filter(|k| !mapping.contains_key(*k)).collect::<Vec<_>>(),
            });
        }
        for (key, change) in patch["collections"].as_object().into_iter().flatten() {
            let identity = COLLECTIONS
                .iter()
                .find(|(c, _)| c == key)
                .map_or("id", |(_, i)| *i);
            let items: Vec<(String, Value)> = document[key]
                .as_array()
                .into_iter()
                .flatten()
                .map(|item| {
                    let mut item = item.clone();
                    // `Group.to_dict`, without the member ids the document adds.
                    if key == "groups" {
                        item.as_object_mut().map(|g| g.remove("member_ids"));
                    }
                    (item[identity].as_str().unwrap_or("").to_string(), item)
                })
                .collect();
            let touched = touched(change);
            inverse["collections"][key] = json!({
                "set": items.iter().filter(|(i, _)| touched.contains(i)).map(|(i, v)| (i.clone(), v.clone())).collect::<Map<_, _>>(),
                "remove": touched.iter().filter(|t| !items.iter().any(|(i, _)| i == *t)).collect::<Vec<_>>(),
                "order": if change["order"].is_null() { Value::Null } else { json!(items.iter().map(|(i, _)| i).collect::<Vec<_>>()) },
            });
        }
        inverse
    }

    /// `History._apply_entity_patch`: the entities `patch` names put in place.
    /// Returns the steps it added, removed or changed.
    ///
    /// ponytail: a parameter change marks nothing stale, so its dependents'
    /// results are not pinned in the redo step; the Rust app does not edit
    /// parameters yet, and the Python app rebuilds what is unpinned.
    fn apply_patch(&mut self, patch: &Value) -> BTreeSet<String> {
        let mut stale = BTreeSet::new();
        let collections = &patch["collections"];
        for (key, identity) in COLLECTIONS {
            let Some(change) = collections.get(*key).filter(|c| c.is_object()) else {
                continue;
            };
            let list = self.list(key);
            let existing: Vec<String> = list
                .iter()
                .map(|v| v[*identity].as_str().unwrap_or("").to_string())
                .collect();
            let mut current: Vec<(String, Value)> = existing.iter().cloned().zip(list).collect();
            for ident in change["remove"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
            {
                if let Some(at) = current.iter().position(|(i, _)| i == ident) {
                    current.remove(at);
                    if *key == "features" {
                        stale.insert(ident.to_string());
                    }
                }
            }
            for (ident, state) in change["set"].as_object().into_iter().flatten() {
                let at = current.iter().position(|(i, _)| i == ident);
                let item = match (*key, at) {
                    ("features" | "parameters", _) => {
                        if *key == "features" && at.is_none_or(|a| &current[a].1 != state) {
                            stale.insert(ident.clone());
                        }
                        state.clone()
                    }
                    // Bodies and groups take the attributes the state names (`Body`, `Group` defaults).
                    (_, found) => {
                        let mut item = match found {
                            Some(a) => current[a].1.clone(),
                            None if *key == "bodies" => {
                                json!({ "id": ident, "name": state["name"], "visible": true,
                                "color": null, "producer": "", "metadata": {} })
                            }
                            None => {
                                json!({ "name": state["name"], "id": ident, "members": [], "visible": true, "expanded": true })
                            }
                        };
                        let attributes: &[&str] = if *key == "bodies" {
                            &["name", "visible", "color", "producer", "metadata"]
                        } else {
                            &["name", "members", "visible", "expanded"]
                        };
                        for a in attributes {
                            if let Some(v) = state.get(*a) {
                                item[*a] = v.clone();
                            }
                        }
                        if *key == "groups" {
                            item.as_object_mut().map(|g| g.remove("member_ids"));
                        }
                        item
                    }
                };
                match at {
                    Some(a) => current[a].1 = item,
                    None => current.push((ident.clone(), item)),
                }
            }
            // An empty order, like none, keeps the order there is.
            let order: Vec<String> = match change["order"].as_array() {
                Some(o) if !o.is_empty() => o
                    .iter()
                    .filter_map(|i| i.as_str().map(String::from))
                    .collect(),
                _ => current.iter().map(|(i, _)| i.clone()).collect(),
            };
            let mut ordered: Vec<Value> = order
                .iter()
                .filter_map(|i| current.iter().find(|(c, _)| c == i).map(|(_, v)| v.clone()))
                .collect();
            ordered.extend(
                current
                    .iter()
                    .filter(|(i, _)| !order.contains(i))
                    .map(|(_, v)| v.clone()),
            );
            self.payload[*key] = Value::Array(ordered);
        }
        let payload = self.payload.as_object_mut().unwrap();
        for (key, value) in patch["values"].as_object().into_iter().flatten() {
            if ["title", "counters", "retired_ids", "metadata"].contains(&key.as_str()) {
                payload.insert(key.clone(), value.clone());
            }
        }
        if let Some(retired) = patch["sets"].get("retired_ids") {
            let strings = |v: &Value| -> BTreeSet<String> {
                v.as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|i| i.as_str().map(String::from))
                    .collect()
            };
            let mut set = strings(&payload["retired_ids"]);
            set.retain(|i| !strings(&retired["remove"]).contains(i));
            set.extend(strings(&retired["add"]));
            payload.insert("retired_ids".into(), json!(set));
        }
        if let Some(counters) = patch["keyed"].get("counters") {
            let map = payload.entry("counters").or_insert_with(|| json!({}));
            if let Some(map) = map.as_object_mut() {
                for name in counters["remove"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                {
                    map.remove(name);
                }
                for (k, v) in counters["set"].as_object().into_iter().flatten() {
                    map.insert(k.clone(), v.clone());
                }
            }
        }
        // `prepare_graph`: body ids and producers, group member ids, as the Python app then holds them.
        crate::graph::normalise(&mut self.payload);
        stale
    }

    /// Each body whose producer is not the one in `before` shown from its new
    /// producer's cached result, and the diagnostics of the steps there are, as
    /// saving after a rebuild writes them. Other bodies keep what they had.
    fn show_cached_results(
        &mut self,
        before: &BTreeMap<String, (String, String)>,
    ) -> Result<(), String> {
        let current =
            signatures(&self.payload).ok_or("This project's steps could not be fingerprinted.")?;
        let mut geometry = Map::new();
        for (name, (producer, object)) in self.latest() {
            if !self.list("bodies").iter().any(|b| b["name"] == json!(name)) {
                continue;
            }
            let signature = current.get(&producer).cloned().unwrap_or_default();
            if before.get(&name) == Some(&(producer.clone(), object.clone())) {
                if let Some(saved) = self.payload["geometry"].get(&object) {
                    geometry.insert(object, saved.clone());
                }
                continue;
            }
            // ponytail: a "located" result (a move) would need its placement
            // applied to a copy; undo reaches only pulls and new boxes so far.
            let output = &self.payload["cache"]["entries"][&signature]["outputs"][&object];
            let Some(sha) = output["sha256"]
                .as_str()
                .filter(|_| output["kind"] == "brep")
            else {
                return Err(format!(
                    "\"{name}\" has to be rebuilt to undo this; undo it in SimpleCAD 0.4."
                ));
            };
            geometry.insert(object, json!({ "sha256": sha, "feature_id": producer, "signature": signature, "current": true }));
        }
        let diagnostics: Map<String, Value> = self
            .list("features")
            .iter()
            .filter_map(|f| {
                let id = f["id"].as_str()?;
                let saved = self.payload["diagnostics"].get(id).cloned();
                Some((
                    id.to_string(),
                    saved.unwrap_or(json!({ "state": "ok", "message": "" })),
                ))
            })
            .collect();
        self.payload["geometry"] = Value::Object(geometry);
        self.payload["diagnostics"] = Value::Object(diagnostics);
        Ok(())
    }

    /// A new project holding one box, as the Shape panel makes it (`ShapePanel.commit`
    /// then `MainWindow.add_feature`); `brep` is the box, 40 × 30 × 20 at the origin.
    pub fn new_box(brep: Vec<u8>) -> Result<Self, String> {
        Self::new_box_as(&new_feature_id(), brep)
    }

    fn new_box_as(id: &str, brep: Vec<u8>) -> Result<Self, String> {
        let mut model = Self::new(json!({
            "title": "Untitled", "counters": {}, "retired_ids": [], "parameters": [], "features": [],
            "bodies": [], "groups": [], "metadata": {}, "assets": [],
        }));
        // Named before the undo snapshot, as the panel does.
        let name = model.unique_name("Box");
        let before = model.document();
        let object = crate::graph::output_id(id, &name);
        let payload = model.payload.as_object_mut().unwrap();
        payload["features"].as_array_mut().unwrap().push(json!({
            "id": id, "type": "box", "name": name,
            "inputs": { "width": "40 mm", "depth": "30 mm", "height": "20 mm" },
            "outputs": [name], "output_ids": { &name: object }, "bindings": {},
            "suppressed": false, "dropped": [],
        }));
        payload["bodies"].as_array_mut().unwrap().push(json!({
            "id": object, "name": name, "visible": true, "color": null, "producer": id, "metadata": {},
        }));
        model.record(id, "Box", &object, brep, before)?;
        Ok(model)
    }

    /// A step just added, `id` writing `object` as `brep`: its cached result,
    /// current geometry and diagnostics, and its undo step back to `before`
    /// (`History.record`, then `_finish_pending` at save).
    fn record(
        &mut self,
        id: &str,
        label: &str,
        object: &str,
        brep: Vec<u8>,
        before: Value,
    ) -> Result<(), String> {
        let signature = signatures(&self.payload)
            .and_then(|s| s.get(id).cloned())
            .ok_or("This project's steps could not be fingerprinted.")?;
        let sha = format!("{:x}", Sha256::digest(&brep));
        let payload = self.payload.as_object_mut().unwrap();
        let cache = payload
            .entry("cache")
            .or_insert_with(|| json!({ "version": CACHE_VERSION, "entries": {} }));
        cache["entries"][&signature] = json!({
            "feature_id": id, "outputs": { object: { "kind": "brep", "sha256": sha } },
            "message": "", "version": CACHE_VERSION,
        });
        payload.entry("geometry").or_insert_with(|| json!({}))[object] =
            json!({ "sha256": sha, "feature_id": id, "signature": signature, "current": true });
        payload.entry("diagnostics").or_insert_with(|| json!({}))[id] =
            json!({ "state": "ok", "message": "" });
        self.blobs.insert(sha, brep);

        let patch = make_patch(&self.document(), &before);
        let history = self
            .payload
            .as_object_mut()
            .unwrap()
            .entry("history")
            .or_insert_with(|| json!({}));
        let history = history
            .as_object_mut()
            .ok_or("This project's history could not be read.")?;
        history.entry("version").or_insert(json!(1));
        history
            .entry("undo")
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .ok_or("This project's history could not be read.")?
            .push(json!({ "label": label, "patch": patch, "cache_keys": [] }));
        history.insert("redo".into(), json!([]));
        Ok(())
    }

    /// The `.scad3` for this model: every entry of `original` kept (geometry,
    /// assets, thumbnail), the new blobs added, `model.json` replaced. An empty
    /// `original` is a project never saved.
    pub fn save(&self, original: &[u8]) -> Result<Vec<u8>, String> {
        let damaged = |e: &dyn std::fmt::Display| format!("The project could not be written: {e}");
        // A project never saved has no file to carry entries over from.
        let empty;
        let original = if original.is_empty() {
            empty = zip::ZipWriter::new(Cursor::new(Vec::new()))
                .finish()
                .map_err(|e| damaged(&e))?
                .into_inner();
            &empty
        } else {
            original
        };
        let mut source = zip::ZipArchive::new(Cursor::new(original)).map_err(|e| damaged(&e))?;
        let mut out = zip::ZipWriter::new(Cursor::new(Vec::new()));
        let options = zip::write::SimpleFileOptions::default();
        let mut payload = self.payload.clone();
        payload["format"] = json!(crate::project::FORMAT_VERSION);
        payload["application"] = json!("SimpleCAD");
        out.start_file("model.json", options)
            .map_err(|e| damaged(&e))?;
        out.write_all(&serde_json::to_vec_pretty(&payload).map_err(|e| damaged(&e))?)
            .map_err(|e| damaged(&e))?;
        for i in 0..source.len() {
            let mut entry = source.by_index(i).map_err(|e| damaged(&e))?;
            let name = entry.name().map_err(|e| damaged(&e))?.to_string();
            // `bodies/` is format 1's geometry, now under `geometry/`.
            if name == "model.json" || name.starts_with("bodies/") || entry.is_dir() {
                continue;
            }
            let mut bytes = Vec::new();
            entry.read_to_end(&mut bytes).map_err(|e| damaged(&e))?;
            out.start_file(name, options).map_err(|e| damaged(&e))?;
            out.write_all(&bytes).map_err(|e| damaged(&e))?;
        }
        for (sha, blob) in &self.blobs {
            let name = format!("geometry/{sha}.brep");
            if source.by_name(&name).is_ok() {
                continue;
            }
            out.start_file(name, options).map_err(|e| damaged(&e))?;
            out.write_all(blob).map_err(|e| damaged(&e))?;
        }
        Ok(out.finish().map_err(|e| damaged(&e))?.into_inner())
    }
}

/// The ids a patch names, set or removed.
fn touched(change: &Value) -> BTreeSet<String> {
    let set = change["set"]
        .as_object()
        .into_iter()
        .flatten()
        .map(|(k, _)| k.clone());
    let removed = change["remove"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|v| v.as_str().map(String::from));
    set.chain(removed).collect()
}

/// `DependencyGraph.dependents`: `ids` and every step downstream of them.
fn dependents(model: &Value, ids: &BTreeSet<String>) -> BTreeSet<String> {
    let mut found = ids.clone();
    let features: Vec<(String, BTreeSet<String>)> = model["features"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|f| {
            let id = f["id"].as_str()?;
            Some((id.to_string(), crate::signatures::upstream(f, id)))
        })
        .collect();
    loop {
        let before = found.len();
        for (id, upstream) in &features {
            if upstream.iter().any(|u| found.contains(u)) {
                found.insert(id.clone());
            }
        }
        if found.len() == before {
            return found;
        }
    }
}

/// `Feature.__init__`'s id for a new step.
fn new_feature_id() -> String {
    format!("f{}", uuid::Uuid::new_v4().simple())
}

/// `changes.make_patch`: what turns `source` into `target`, entity by entity.
pub fn make_patch(source: &Value, target: &Value) -> Value {
    let empty = Map::new();
    let (old_map, new_map) = (
        source.as_object().unwrap_or(&empty),
        target.as_object().unwrap_or(&empty),
    );
    let mut keys: Vec<&String> = old_map.keys().chain(new_map.keys()).collect();
    keys.sort();
    keys.dedup();
    let mut patch = json!({ "values": {}, "collections": {} });
    for key in keys {
        let (old, new) = (old_map.get(key), new_map.get(key));
        if key == "retired_ids"
            && old.is_some_and(Value::is_array)
            && new.is_some_and(Value::is_array)
        {
            let set = |v: Option<&Value>| -> std::collections::BTreeSet<String> {
                v.and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(|x| x.as_str().map(String::from))
                    .collect()
            };
            let (old, new) = (set(old), set(new));
            if old != new {
                patch["sets"][key] = json!({ "add": new.difference(&old).collect::<Vec<_>>(),
                                              "remove": old.difference(&new).collect::<Vec<_>>() });
            }
            continue;
        }
        if key == "counters"
            && old.is_some_and(Value::is_object)
            && new.is_some_and(Value::is_object)
        {
            let (old, new) = (
                old.unwrap().as_object().unwrap(),
                new.unwrap().as_object().unwrap(),
            );
            let changed: Map<String, Value> = new
                .iter()
                .filter(|(k, v)| old.get(*k) != Some(*v))
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
            let removed: Vec<&String> = old.keys().filter(|k| !new.contains_key(*k)).collect();
            if !changed.is_empty() || !removed.is_empty() {
                patch["keyed"][key] = json!({ "set": changed, "remove": removed });
            }
            continue;
        }
        let Some((_, identity)) = COLLECTIONS.iter().find(|(c, _)| c == key) else {
            if old != new {
                patch["values"][key] = new.cloned().unwrap_or(Value::Null);
            }
            continue;
        };
        let keyed = |list: Option<&Value>| -> Vec<(String, Value)> {
            list.and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|v| {
                    let ident = v
                        .get(*identity)
                        .or_else(|| v.get("name"))
                        .and_then(Value::as_str)
                        .unwrap_or("");
                    (ident.to_string(), v.clone())
                })
                .collect()
        };
        let (old, new) = (keyed(old), keyed(new));
        let old_by: BTreeMap<&String, &Value> = old.iter().map(|(k, v)| (k, v)).collect();
        let new_ids: HashSet<&String> = new.iter().map(|(k, _)| k).collect();
        let changed: Map<String, Value> = new
            .iter()
            .filter(|(k, v)| old_by.get(k) != Some(&v))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        let removed: Vec<&String> = old
            .iter()
            .map(|(k, _)| k)
            .filter(|k| !new_ids.contains(k))
            .collect();
        let reordered = old.iter().map(|(k, _)| k).ne(new.iter().map(|(k, _)| k));
        let order = if reordered {
            json!(new.iter().map(|(k, _)| k).collect::<Vec<_>>())
        } else {
            Value::Null
        };
        if !changed.is_empty() || !removed.is_empty() || reordered {
            patch["collections"][key] =
                json!({ "set": changed, "remove": removed, "order": order });
        }
    }
    patch
}

#[cfg(test)]
mod tests {
    use super::*;

    fn saved_model(model: &Model) -> Value {
        let saved = model.save(&[]).unwrap();
        let mut archive = zip::ZipArchive::new(Cursor::new(saved)).unwrap();
        let mut text = String::new();
        archive
            .by_name("model.json")
            .unwrap()
            .read_to_string(&mut text)
            .unwrap();
        serde_json::from_str(&text).unwrap()
    }

    /// Every `sha256` dropped: each app writes its own B-Rep bytes.
    fn without_shas(value: &mut Value) {
        match value {
            Value::Object(map) => {
                map.remove("sha256");
                map.values_mut().for_each(without_shas);
            }
            Value::Array(items) => items.iter_mut().for_each(without_shas),
            _ => {}
        }
    }

    /// `engine_oracles.py`: a new box, its top pulled 5 then -3 mm, the second
    /// pull undone, saved by the Python app. The history (undo and redo steps
    /// and the results they pin), geometry and diagnostics must be the same.
    #[test]
    fn an_undone_pull_saves_with_its_redo_step_as_the_python_app_does() {
        let python: Value =
            serde_json::from_str(include_str!("../tests/data/pulls_undone.json")).unwrap();
        let undo = python["history"]["undo"].as_array().unwrap();
        let box_id = undo[0]["patch"]["collections"]["features"]["remove"][0]
            .as_str()
            .unwrap();
        let first = &python["features"][1];
        let second = python["history"]["redo"][0]["patch"]["collections"]["features"]["set"]
            .as_object()
            .unwrap()
            .values()
            .next()
            .unwrap();
        let mut model = Model::new_box_as(box_id, b"box".to_vec()).unwrap();
        for (pull, brep) in [(first, b"first"), (second, b"other")] {
            model
                .push_pull_as(
                    pull["id"].as_str().unwrap(),
                    "Box",
                    pull["inputs"]["face"]["__ref__"].clone(),
                    pull["inputs"]["distance"].as_str().unwrap(),
                    brep.to_vec(),
                )
                .unwrap();
        }
        model.undo().unwrap();
        let mut rust = saved_model(&model);
        let mut expected = python.clone();
        expected["history"].as_object_mut().unwrap().remove("limit");
        without_shas(&mut rust);
        without_shas(&mut expected);
        assert_eq!(rust["history"], expected["history"]);
        assert_eq!(rust, expected);
        // The body shows the first pull's result again.
        let object = first["output_ids"]["Box"].as_str().unwrap();
        assert_eq!(
            model.payload["geometry"][object]["sha256"],
            json!(format!("{:x}", Sha256::digest(b"first")))
        );
    }

    /// `scripts/engine_oracles.py` `new_box`: what the Python app saves for a
    /// new project with one box. Only the box's B-Rep, written by each app's
    /// own OCCT, may differ.
    #[test]
    fn a_new_box_is_the_python_apps() {
        let python: Value =
            serde_json::from_str(include_str!("../tests/data/new_box.json")).unwrap();
        let id = python["features"][0]["id"].as_str().unwrap();
        let model = Model::new_box_as(id, b"the box".to_vec()).unwrap();
        let mut rust = saved_model(&model);
        let mut expected = python.clone();
        without_shas(&mut rust);
        without_shas(&mut expected);
        // The undo depth is the user's preference in the Python app.
        expected["history"].as_object_mut().unwrap().remove("limit");
        assert_eq!(rust, expected);
    }
}

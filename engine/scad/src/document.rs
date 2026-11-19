//! Editing and saving a project as the Python app would, on its `model.json`
//! (`core/document.py`, `core/graph.py`, `core/changes.py`, `core/history.py`,
//! `core/project.py`). A step added here is a real feature of that model: the
//! Python app reopens the file, finds its cached result current, rebuilds it if
//! asked, and can undo it.

use crate::project::CACHE_VERSION;
use crate::signatures::signatures;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};
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
        let before = self.document();
        let (producer, object) = self
            .latest()
            .remove(body)
            .ok_or_else(|| format!("No step writes the body \"{body}\"."))?;
        let id = format!("f{}", uuid::Uuid::new_v4().simple());
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

        let signature = signatures(&self.payload)
            .and_then(|s| s.get(&id).cloned())
            .ok_or("This project's steps could not be fingerprinted.")?;
        let sha = format!("{:x}", Sha256::digest(&brep));
        let payload = self.payload.as_object_mut().unwrap();
        let cache = payload
            .entry("cache")
            .or_insert_with(|| json!({ "version": CACHE_VERSION, "entries": {} }));
        cache["entries"][&signature] = json!({
            "feature_id": id, "outputs": { &object: { "kind": "brep", "sha256": sha } },
            "message": "", "version": CACHE_VERSION,
        });
        payload.entry("geometry").or_insert_with(|| json!({}))[&object] =
            json!({ "sha256": sha, "feature_id": id, "signature": signature, "current": true });
        payload.entry("diagnostics").or_insert_with(|| json!({}))[&id] =
            json!({ "state": "ok", "message": "" });
        self.blobs.insert(sha, brep);

        // `History.record` then `_finish_pending`: undo is the patch back to before.
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
            .ok_or("bad history")?
            .push(json!({ "label": "Pull", "patch": patch, "cache_keys": [] }));
        history.insert("redo".into(), json!([]));
        Ok(id)
    }

    /// The `.scad3` for this model: every entry of `original` kept (geometry,
    /// assets, thumbnail), the new blobs added, `model.json` replaced.
    pub fn save(&self, original: &[u8]) -> Result<Vec<u8>, String> {
        let damaged = |e: &dyn std::fmt::Display| format!("The project could not be written: {e}");
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

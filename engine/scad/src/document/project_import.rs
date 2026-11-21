//! Bring another project's recipes into the current document with fresh identities.

use super::{Model, new_feature_id};
use crate::params::dependencies;
use crate::project::{CACHE_VERSION, Project};
use crate::rebuild::{Body, Shape};
use crate::signatures::signatures;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::io::{Cursor, Read};
use std::sync::Arc;

const INVALID: &str = "The source project's history could not be read.";

#[derive(Default)]
struct Names {
    taken: HashSet<String>,
    bodies: HashMap<String, String>,
    sketches: HashMap<String, String>,
    groups: HashMap<String, String>,
    parameters: HashMap<String, String>,
    feature_ids: HashMap<String, String>,
    object_ids: HashMap<String, String>,
    private: String,
}

fn array<'a>(data: &'a Value, key: &str) -> &'a [Value] {
    data[key].as_array().map(Vec::as_slice).unwrap_or(&[])
}

fn field<'a>(data: &'a Value, key: &str) -> Result<&'a str, String> {
    data[key]
        .as_str()
        .filter(|text| !text.is_empty())
        .ok_or_else(|| INVALID.into())
}

fn name(mapping: &HashMap<String, String>, source: &str) -> String {
    mapping
        .get(source)
        .cloned()
        .unwrap_or_else(|| source.into())
}

impl Names {
    fn unique(&mut self, base: &str) -> String {
        if self.taken.insert(base.into()) {
            return base.into();
        }
        let stem = base.trim_end_matches(|ch: char| ch.is_ascii_digit());
        for suffix in 2.. {
            let candidate = format!("{stem}{suffix}");
            if self.taken.insert(candidate.clone()) {
                return candidate;
            }
        }
        unreachable!()
    }

    fn collect(destination: &Value, source: &Value) -> Result<Self, String> {
        let mut names = Self {
            private: format!("__copy_{}_", uuid::Uuid::new_v4().simple()),
            ..Self::default()
        };
        for key in ["bodies", "features", "groups"] {
            names.taken.extend(
                array(destination, key)
                    .iter()
                    .filter_map(|entry| entry["name"].as_str().map(String::from)),
            );
        }
        for entry in array(source, "bodies") {
            let original = field(entry, "name")?;
            let cloned = names.unique(original);
            names.bodies.insert(original.into(), cloned);
        }
        for step in array(source, "features")
            .iter()
            .filter(|step| step["type"] == "sketch")
        {
            let original = field(step, "name")?;
            let cloned = names.unique(original);
            names.bodies.insert(
                format!("__sketch__{original}"),
                format!("__sketch__{cloned}"),
            );
            names.sketches.insert(original.into(), cloned);
        }
        for step in array(source, "features") {
            let id = field(step, "id")?;
            if names
                .feature_ids
                .insert(id.into(), new_feature_id())
                .is_some()
            {
                return Err(INVALID.into());
            }
            for output in array(step, "outputs") {
                let alias = output.as_str().ok_or(INVALID)?;
                names.body_alias(alias);
                let id = field(&step["output_ids"], alias)?;
                names.object_ids.entry(id.into()).or_insert_with(random_id);
            }
        }
        for entry in array(source, "groups") {
            let original = field(entry, "name")?;
            let cloned = names.unique(original);
            names.groups.insert(original.into(), cloned);
        }
        let mut used: HashSet<String> = array(destination, "parameters")
            .iter()
            .filter_map(|entry| entry["name"].as_str().map(String::from))
            .collect();
        for parameter in array(source, "parameters") {
            let original = field(parameter, "name")?;
            let mut candidate = original.to_owned();
            let mut suffix = 1;
            while !used.insert(candidate.clone()) {
                candidate = if suffix == 1 {
                    format!("{original}_copy")
                } else {
                    format!("{original}_copy{suffix}")
                };
                suffix += 1;
            }
            names.parameters.insert(original.into(), candidate);
        }
        Ok(names)
    }

    fn body_alias(&mut self, original: &str) -> String {
        if original.is_empty() {
            return String::new();
        }
        self.bodies
            .entry(original.into())
            .or_insert_with(|| format!("{}{original}", self.private))
            .clone()
    }

    fn feature_id(&mut self, original: &str) -> String {
        if original.is_empty() {
            return String::new();
        }
        self.feature_ids
            .entry(original.into())
            .or_insert_with(new_feature_id)
            .clone()
    }

    fn object_id(&mut self, original: &str) -> String {
        if original.is_empty() {
            return String::new();
        }
        self.object_ids
            .entry(original.into())
            .or_insert_with(random_id)
            .clone()
    }

    fn rewrite_refs(&mut self, input: &mut Value) {
        if input.get("__ref__").is_some() {
            let entry = &mut input["__ref__"];
            if let Some(alias) = entry["body"].as_str() {
                entry["body"] = json!(self.body_alias(alias));
            }
            if let Some(id) = entry["feature_id"].as_str() {
                entry["feature_id"] = json!(self.feature_id(id));
            }
            if let Some(id) = entry["body_id"].as_str() {
                entry["body_id"] = json!(self.object_id(id));
            }
        } else if input.get("__body__").is_some() {
            if let Some(alias) = input["__body__"].as_str() {
                input["__body__"] = json!(self.body_alias(alias));
            }
            if let Some(id) = input["feature_id"].as_str() {
                input["feature_id"] = json!(self.feature_id(id));
            }
            if let Some(id) = input["object_id"].as_str() {
                input["object_id"] = json!(self.object_id(id));
            }
        } else if let Some(values) = input.as_array_mut() {
            values.iter_mut().for_each(|value| self.rewrite_refs(value));
        } else if let Some(entries) = input.as_object_mut() {
            entries
                .values_mut()
                .for_each(|value| self.rewrite_refs(value));
        }
    }

    fn expression(&self, text: &str) -> String {
        let mapped: HashMap<_, _> = dependencies(text)
            .into_iter()
            .filter_map(|dependency| {
                let replaced = self.parameters.get(&dependency)?;
                (replaced != &dependency).then_some((dependency, replaced))
            })
            .collect();
        if mapped.is_empty() {
            return text.into();
        }
        // Match whole identifiers, the same rewriting used by the native clipboard.
        let mut result = String::with_capacity(text.len());
        let mut token = String::new();
        let flush = |result: &mut String, token: &mut String| {
            result.push_str(
                mapped
                    .get(token)
                    .map_or(token.as_str(), |name| name.as_str()),
            );
            token.clear();
        };
        for ch in text.chars() {
            if ch.is_alphanumeric() || ch == '_' {
                token.push(ch);
            } else {
                flush(&mut result, &mut token);
                result.push(ch);
            }
        }
        flush(&mut result, &mut token);
        result
    }

    fn feature(&mut self, original: &Value) -> Result<Value, String> {
        let mut feature = original.clone();
        feature["id"] = json!(self.feature_id(field(original, "id")?));
        let old_name = field(original, "name")?;
        feature["name"] = json!(if original["type"] == "sketch" {
            name(&self.sketches, old_name)
        } else if original["output_ids"].get(old_name).is_some() {
            name(&self.bodies, old_name)
        } else {
            self.unique(old_name)
        });
        for key in ["outputs", "dropped"] {
            feature[key] = json!(
                array(original, key)
                    .iter()
                    .filter_map(Value::as_str)
                    .map(|alias| name(&self.bodies, alias))
                    .collect::<Vec<_>>()
            );
        }
        let outputs = original["output_ids"].as_object().ok_or(INVALID)?;
        feature["output_ids"] = Value::Object(
            outputs
                .iter()
                .filter_map(|(alias, id)| {
                    Some((
                        name(&self.bodies, alias),
                        json!(self.object_id(id.as_str()?)),
                    ))
                })
                .collect(),
        );
        if let Some(bindings) = original["bindings"].as_object() {
            feature["bindings"] = Value::Object(
                bindings
                    .iter()
                    .filter_map(|(alias, binding)| {
                        Some((
                            self.body_alias(alias),
                            json!({
                                "feature_id": self.feature_id(binding["feature_id"].as_str()?),
                                "object_id": self.object_id(binding["object_id"].as_str()?),
                            }),
                        ))
                    })
                    .collect(),
            );
        }
        let kind = field(original, "type")?;
        if let Some(inputs) = feature["inputs"].as_object_mut() {
            if kind == "sketch"
                && let Some(sketch) = inputs.get_mut("sketch")
            {
                sketch["name"] = json!(name(&self.sketches, old_name));
                self.sketch_dimensions(sketch);
            }
            let sketch_refs: &[&str] = match kind {
                "extrude" | "revolve" => &["sketch"],
                "sweep" => &["profile", "path"],
                "loft" => &["sketches"],
                "path_pattern" => &["path"],
                _ => &[],
            };
            for (key, value) in inputs {
                self.rewrite_refs(value);
                if sketch_refs.contains(&key.as_str()) {
                    match value {
                        Value::String(alias) => *alias = name(&self.sketches, alias),
                        Value::Array(list) => {
                            for entry in list {
                                if let Some(alias) = entry.as_str() {
                                    *entry = json!(name(&self.sketches, alias));
                                }
                            }
                        }
                        _ => {}
                    }
                } else if matches!(key.as_str(), "names" | "connector_names") {
                    if let Some(parts) = value.as_array_mut() {
                        for part in parts {
                            if let Some(alias) = part.as_str() {
                                *part = json!(name(&self.bodies, alias));
                            }
                        }
                    }
                } else if let Some(text) = value.as_str() {
                    *value = json!(self.expression(text));
                }
            }
        }
        Ok(feature)
    }

    fn sketch_dimensions(&self, data: &mut Value) {
        if let Some(constraints) = data["constraints"].as_array_mut() {
            for constraint in constraints {
                for key in ["value", "expression"] {
                    if let Some(expression) = constraint[key].as_str() {
                        constraint[key] = json!(self.expression(expression));
                    }
                }
            }
        }
    }
}

fn random_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

fn rename(entry: &mut Value, key: &str, names: &HashMap<String, String>) {
    if let Some(original) = entry[key].as_str() {
        entry[key] = json!(name(names, original));
    }
}

struct Assets<'a> {
    archive: zip::ZipArchive<Cursor<&'a [u8]>>,
    blobs: BTreeMap<String, Arc<[u8]>>,
}

impl Assets<'_> {
    fn read(&mut self, folder: &str, sha: &str) -> Result<Arc<[u8]>, String> {
        if sha.len() != 64
            || !sha
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(INVALID.into());
        }
        if let Some(bytes) = self.blobs.get(sha) {
            return Ok(bytes.clone());
        }
        let file = self
            .archive
            .by_name(&format!("{folder}/{sha}.brep"))
            .map_err(|_| "The source project is missing saved geometry.")?;
        let limit = crate::rebuild::MAX_IMPORT_BYTES;
        if file.size() > limit {
            return Err(
                "The source project's saved geometry exceeds the import size limit.".into(),
            );
        }
        let mut bytes = Vec::new();
        file.take(limit + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| "The source project's saved geometry could not be read.")?;
        if bytes.len() as u64 > limit {
            return Err(
                "The source project's saved geometry exceeds the import size limit.".into(),
            );
        }
        if format!("{:x}", Sha256::digest(&bytes)) != sha {
            return Err("The source project's saved geometry is damaged.".into());
        }
        let bytes: Arc<[u8]> = bytes.into();
        self.blobs.insert(sha.into(), bytes.clone());
        Ok(bytes)
    }
}

impl Model {
    /// Import the source's editable history and translated assembly as one undo item.
    pub fn import_project(
        &self,
        source: &Project,
        source_bytes: &[u8],
        parts: Vec<Body>,
        offset: [f64; 3],
    ) -> Result<(Self, Vec<Body>), String> {
        let payload = &source.model;
        let source_keys = signatures(payload).ok_or(INVALID)?;
        let source_cache = &payload["cache"]["entries"];
        let mut candidate = self.clone();
        let before = self.document();
        let undo_len = array(&self.payload["history"], "undo").len();
        let mut names = Names::collect(&self.payload, payload)?;
        let mut assets = Assets {
            archive: zip::ZipArchive::new(Cursor::new(source_bytes)).map_err(|_| INVALID)?,
            blobs: source
                .recovered_blobs
                .iter()
                .map(|(sha, bytes)| (sha.clone(), Arc::from(bytes.as_slice())))
                .collect(),
        };
        let mut saved_steps = Vec::new();
        let mut states = Map::new();
        for step in array(payload, "features") {
            let id = field(step, "id")?;
            let mut clone = names.feature(step)?;
            let mut cached = source_keys
                .get(id)
                .and_then(|key| source_cache.get(key))
                .filter(|entry| entry["version"] == CACHE_VERSION && entry["feature_id"] == id)
                .cloned();
            match field(step, "type")? {
                "embedded_import" => {
                    let sha = field(&step["inputs"], "asset")?;
                    assets.read("assets", sha)?;
                    candidate.add_project_asset(sha)?;
                }
                "import" => {
                    // The legacy recipe may point at a file already moved. Use the exact
                    // result saved at the import step, before any later features ran.
                    let output = cached.as_ref().and_then(|entry| entry["outputs"].as_object())
                        .filter(|outputs| outputs.len() == 1 && array(step, "outputs").len() == 1)
                        .and_then(|outputs| outputs.values().next())
                        .ok_or("An external import has no saved result. Embed its geometry in the source project first.")?;
                    let raw = assets.read("geometry", field(output, "sha256")?)?;
                    let baked = if output["kind"] == "located" {
                        let transform: Vec<f64> = array(output, "location")
                            .iter()
                            .filter_map(Value::as_f64)
                            .collect();
                        let shape = occt::read_brep(&raw)
                            .and_then(|shape| occt::located(&shape, &transform))
                            .and_then(|shape| occt::write_brep(&shape))
                            .map_err(|error| error.what().to_owned())?;
                        Arc::from(shape)
                    } else {
                        raw
                    };
                    let sha = format!("{:x}", Sha256::digest(&baked));
                    assets.blobs.insert(sha.clone(), baked);
                    candidate.add_project_asset(&sha)?;
                    clone["type"] = json!("embedded_import");
                    clone["inputs"] = json!({ "asset": sha });
                    cached = None;
                }
                _ => {}
            }
            let clone_id = field(&clone, "id")?.to_owned();
            if let Some(mut entry) = cached
                && let Some(outputs) = entry["outputs"].as_object_mut()
            {
                let entries = std::mem::take(outputs);
                for (object, mut value) in entries {
                    if let Some(sha) = value["sha256"].as_str() {
                        assets.read("geometry", sha)?;
                    }
                    if value["kind"] == "sketch" && value["data"].is_object() {
                        value["data"]["name"] = clone["name"].clone();
                        names.sketch_dimensions(&mut value["data"]);
                    }
                    outputs.insert(names.object_id(&object), value);
                }
                entry["feature_id"] = json!(clone_id);
                saved_steps.push((clone_id.clone(), entry));
            }
            if let Some(state) = payload["diagnostics"].get(id) {
                states.insert(clone_id, state.clone());
            }
            candidate.push_import_entry("features", clone)?;
        }
        for parameter in array(payload, "parameters") {
            let mut clone = parameter.clone();
            rename(&mut clone, "name", &names.parameters);
            clone["expression"] = json!(names.expression(field(parameter, "expression")?));
            candidate.push_import_entry("parameters", clone)?;
        }
        for group in array(payload, "groups") {
            let mut clone = group.clone();
            clone["id"] = json!(random_id());
            rename(&mut clone, "name", &names.groups);
            if let Some(members) = clone["members"].as_array_mut() {
                for member in members {
                    if let Some(alias) = member.as_str() {
                        *member = json!(
                            names
                                .groups
                                .get(alias)
                                .cloned()
                                .unwrap_or_else(|| name(&names.bodies, alias))
                        );
                    }
                }
            }
            candidate.push_import_entry("groups", clone)?;
        }
        for body in array(payload, "bodies") {
            let mut clone = body.clone();
            clone["id"] = json!(names.object_id(field(body, "id")?));
            clone["producer"] = json!(names.feature_id(field(body, "producer")?));
            rename(&mut clone, "name", &names.bodies);
            candidate.push_import_entry("bodies", clone)?;
        }
        for id in array(payload, "retired_ids")
            .iter()
            .filter_map(Value::as_str)
        {
            candidate.push_import_entry("retired_ids", json!(names.feature_id(id)))?;
        }

        candidate.blobs.extend(assets.blobs);
        let now = signatures(&candidate.payload).ok_or(INVALID)?;
        for (id, entry) in saved_steps {
            candidate.payload["cache"]["version"] = json!(CACHE_VERSION);
            candidate.payload["cache"]["entries"][now.get(&id).ok_or(INVALID)?] = entry;
        }
        for (id, state) in states {
            candidate.payload["diagnostics"][id] = state;
        }

        let mut result = Vec::with_capacity(parts.len());
        for body in parts {
            let id = names.object_id(&body.id);
            let name = name(&names.bodies, &body.name);
            let shape = body
                .shape
                .as_ref()
                .ok_or("A body has no saved geometry. Repair the source project first.")?;
            let source_info = array(payload, "bodies")
                .iter()
                .find(|b| b["id"] == body.id)
                .ok_or(INVALID)?;
            let producer = names.feature_id(field(source_info, "producer")?);
            let sig = now.get(&producer).ok_or(INVALID)?;
            let brep = occt::write_brep(shape).map_err(|e| e.what().to_owned())?;
            let sha = format!("{:x}", Sha256::digest(&brep));
            candidate.blobs.insert(sha.clone(), brep.into());
            let current = !matches!(
                candidate.payload["diagnostics"][&producer]["state"].as_str(),
                Some("failed" | "needs_attention")
            );
            candidate.payload["geometry"][&id] = json!({
                "sha256": sha, "feature_id": producer, "signature": sig, "current": current,
            });
            let shape: Shape = if offset == [0.0; 3] {
                shape.clone()
            } else {
                let [dx, dy, dz] = offset;
                let moved = crate::rebuild::create_transform(
                    &mut candidate,
                    &name,
                    shape,
                    "Move",
                    json!({
                        "dx": dx, "dy": dy, "dz": dz,
                    }),
                )?;
                std::rc::Rc::new(moved)
            };
            result.push(Body {
                id,
                name,
                shape: Some(shape),
            });
        }
        if let Some(undo) = candidate.payload["history"]["undo"].as_array_mut() {
            undo.truncate(undo_len);
        }
        candidate.push_undo("Import", before)?;
        Ok((candidate, result))
    }

    fn push_import_entry(&mut self, key: &str, entry: Value) -> Result<(), String> {
        self.payload
            .as_object_mut()
            .ok_or("Invalid destination project.")?
            .entry(key)
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .ok_or("Invalid destination project.")?
            .push(entry);
        Ok(())
    }

    fn add_project_asset(&mut self, key: &str) -> Result<(), String> {
        if !array(&self.payload, "assets")
            .iter()
            .any(|other| other == key)
        {
            self.push_import_entry("assets", json!(key))?;
        }
        self.new_assets.insert(key.into());
        Ok(())
    }
}

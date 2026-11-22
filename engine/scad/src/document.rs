//! Editing and saving a project as the Python app would, on its `model.json`
//! (`core/document.py`, `core/graph.py`, `core/changes.py`, `core/history.py`,
//! `core/project.py`). A step added here is a real feature of that model: the
//! Python app reopens the file, finds its cached result current, rebuilds it if
//! asked, and can undo it.

mod project_import;
mod sketch_edit;

use crate::project::CACHE_VERSION;
use crate::signatures::signatures;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::io::{Cursor, Read, Write};
use std::sync::Arc;

pub struct ImportedPart {
    pub name: String,
    pub asset: Vec<u8>,
    pub placed: Vec<u8>,
    pub note: String,
}

pub struct StepSummary {
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

pub struct MissingInput {
    pub name: String,
    pub object_id: String,
    pub face: bool,
}

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
    pub blobs: BTreeMap<String, Arc<[u8]>>,
    new_assets: BTreeSet<String>,
}

impl Model {
    pub fn new(payload: Value) -> Self {
        Self {
            payload,
            blobs: BTreeMap::new(),
            new_assets: BTreeSet::new(),
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
            model.blobs.extend(
                project
                    .recovered_blobs
                    .iter()
                    .map(|(sha, bytes)| (sha.clone(), Arc::from(bytes.as_slice()))),
            );
            return model;
        }
        let latest = model.latest();
        let signatures = model.adopt_baseline(&project.bodies).unwrap_or_default();
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
            model.blobs.insert(sha, Arc::from(brep.as_slice()));
        }
        let payload = model.payload.as_object_mut().unwrap();
        // Saved results only where the bodies were adopted, as the Python app's load.
        payload
            .entry("cache")
            .or_insert_with(|| json!({ "version": CACHE_VERSION, "entries": {} }));
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

    /// The step that last wrote `body`, and its object ID, when the body can be changed. A body whose
    /// step failed, or waits on a repair upstream, keeps its last valid model whenever it is rebuilt
    /// (as in SimpleCAD 0.4), so a change made on top of it would show now and be lost on Redo after
    /// reopening.
    pub fn editable(&self, body: &str) -> Result<(String, String), String> {
        let (producer, object) = self
            .latest()
            .remove(body)
            .ok_or_else(|| format!("No step writes the body \"{body}\"."))?;
        if self.blocked_steps().contains(&producer) {
            return Err(format!(
                "Repair the step marked in History that made \"{body}\" before changing it."
            ));
        }
        Ok((producer, object))
    }

    /// The steps that cannot rebuild as saved, and every step downstream of one: those marked failed
    /// or waiting on a repair, and those bound to no step or to one that no longer exists (a deleted
    /// input, as Spondge Holder's Combine5 and its Cylinder2). A file opened from its saved results shows them
    /// as they were, but the first rebuild keeps their last valid model.
    fn blocked_steps(&self) -> HashSet<String> {
        let order = self.list("features");
        let ids: HashSet<&str> = order.iter().filter_map(|f| f["id"].as_str()).collect();
        let lost: HashSet<String> = order
            .iter()
            .filter(|step| {
                let id = step["id"].as_str().unwrap_or_default();
                matches!(
                    self.payload["diagnostics"][id]["state"].as_str(),
                    Some("failed" | "needs_attention")
                ) || lost_binding(step, &ids)
            })
            .filter_map(|step| step["id"].as_str().map(String::from))
            .collect();
        crate::rebuild::dependents(&order, &lost)
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

    /// `Rebuilder.adopt_baseline`: when every body was read and has a producer,
    /// each step whose outputs are all bodies gets them as its saved result
    /// (one already saved is kept). Returns the signatures, or `None` when not adopted.
    pub fn adopt_baseline(
        &mut self,
        bodies: &[crate::project::SavedBody],
    ) -> Option<BTreeMap<String, String>> {
        let latest = self.latest();
        if bodies.is_empty()
            || !bodies
                .iter()
                .all(|b| b.brep.is_some() && latest.contains_key(&b.name))
        {
            return None;
        }
        let signatures = signatures(&self.payload)?;
        let mut outputs: BTreeMap<String, Map<String, Value>> = BTreeMap::new();
        for body in bodies {
            let (Some(brep), Some((feature, object))) = (&body.brep, latest.get(&body.name)) else {
                continue;
            };
            if matches!(
                self.payload["diagnostics"][feature]["state"].as_str(),
                Some("failed" | "needs_attention")
            ) {
                continue;
            }
            let sha = format!("{:x}", Sha256::digest(brep));
            let output = match &body.location {
                Some(matrix) => {
                    json!({ "kind": "located", "sha256": sha, "location": matrix.to_vec() })
                }
                None => json!({ "kind": "brep", "sha256": sha }),
            };
            outputs
                .entry(feature.clone())
                .or_default()
                .insert(object.clone(), output);
            self.blobs.insert(sha, Arc::from(brep.as_slice()));
        }
        let features = self.list("features");
        let payload = self.payload.as_object_mut().unwrap();
        let cache = payload
            .entry("cache")
            .or_insert_with(|| json!({ "version": CACHE_VERSION, "entries": {} }));
        if !cache["entries"].is_object() {
            cache["entries"] = json!({});
        }
        let entries = cache["entries"].as_object_mut().unwrap();
        for feature in &features {
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
            if wanted.len() == found.len()
                && wanted.iter().all(|o| found.contains_key(*o))
                && !entries.contains_key(signature)
            {
                entries.insert(signature.clone(), json!({ "feature_id": id, "outputs": found, "message": "", "version": CACHE_VERSION }));
            }
        }
        Some(signatures)
    }

    fn matching_thread(feature: &Value) -> Option<HashMap<String, String>> {
        let inputs = &feature["inputs"];
        if inputs["thread_modelled"] != true
            || !matches!(
                feature["type"].as_str(),
                Some("hole" | "thread" | "matching_bolt" | "matching_nut" | "matching_thread")
            )
        {
            return None;
        }
        let designation = inputs["designation"]
            .as_str()
            .filter(|value| !value.is_empty())?;
        let internal = inputs["thread_internal"].as_bool()?;
        let form = inputs["form"].as_str().unwrap_or("printed");
        let left_hand = inputs["left_hand"].as_bool().unwrap_or(false);
        let clearance = inputs["clearance"]
            .as_str()
            .map(String::from)
            .or_else(|| inputs["clearance"].as_f64().map(|value| value.to_string()))
            .unwrap_or_else(|| "normal".into());
        Some(HashMap::from([
            ("designation".into(), designation.into()),
            ("internal".into(), internal.to_string()),
            ("form".into(), form.into()),
            ("left_hand".into(), left_hand.to_string()),
            ("clearance".into(), clearance),
        ]))
    }

    /// Every step and its most recent diagnostic, in recipe order.
    pub fn steps(&self) -> Vec<StepSummary> {
        let features = self.list("features");
        let ids: HashSet<_> = features.iter().filter_map(|f| f["id"].as_str()).collect();
        let current = std::cell::OnceCell::new();
        features
            .iter()
            .map(|f| {
                let text = |v: &Value| v.as_str().unwrap_or("").to_string();
                let id = text(&f["id"]);
                let diagnostic = &self.payload["diagnostics"][&id];
                // Saved as built but bound to an input that is gone: its first rebuild needs a repair.
                let lost = diagnostic["state"].as_str().is_none_or(|state| state == "ok")
                    && f["suppressed"] != true
                    && lost_binding(f, &ids);
                let state = if lost {
                    "needs_attention"
                } else {
                    diagnostic["state"].as_str().unwrap_or("ok")
                };
                let construction_plane = (state == "ok")
                    .then(|| {
                        let kind = crate::construction::Kind::from_feature(f["type"].as_str()?)?;
                        if kind.cache_kind() != "ConstructionPlane" || f["suppressed"] == true {
                            return None;
                        }
                        let signatures =
                            current.get_or_init(|| signatures(&self.payload).unwrap_or_default());
                        let key = signatures.get(&id)?;
                        self.cached_construction_plane(f, key)
                    })
                    .flatten();
                StepSummary {
                    id,
                    name: text(&f["name"]),
                    kind: text(&f["type"]),
                    state: state.into(),
                    message: if lost {
                        Some("An input this step was built from has been deleted. Choose a replacement with Repair input, or undo the deletion.".into())
                    } else {
                        diagnostic["message"]
                            .as_str()
                            .filter(|message| !message.is_empty())
                            .map(String::from)
                    },
                    missing_input: (state == "needs_attention")
                        .then(|| missing_reference(f, &ids))
                        .flatten(),
                    face_settings: edit_values(
                        f,
                        face_edit_fields(&f["type"]).or_else(|| body_edit_fields(&f["type"])),
                    ),
                    shape_settings: edit_values(f, shape_edit_fields(&f["type"])),
                    matching_thread: (state == "ok").then(|| Self::matching_thread(f)).flatten(),
                    construction_plane,
                }
            })
            .collect()
    }

    fn cached_construction_plane(
        &self,
        feature: &Value,
        key: &str,
    ) -> Option<HashMap<String, Vec<f64>>> {
        let name = format!("__construction__{}", feature["name"].as_str()?);
        let id = feature["output_ids"][name].as_str()?;
        let entry = &self.payload["cache"]["entries"][key];
        if entry["version"] != CACHE_VERSION || entry["feature_id"] != feature["id"] {
            return None;
        }
        let output = &entry["outputs"][id];
        if output["kind"] != "ConstructionPlane" {
            return None;
        }
        ["origin", "normal", "x_axis"]
            .into_iter()
            .map(|key| {
                let [x, y, z] = output["data"][key].as_array()?.as_slice() else {
                    return None;
                };
                Some((key.into(), vec![x.as_f64()?, y.as_f64()?, z.as_f64()?]))
            })
            .collect()
    }

    /// Rebuild after changing a step's editable expressions. The source ID,
    /// bindings and every input the user left alone are kept.
    pub fn edit_step_settings(
        &mut self,
        step_id: &str,
        changes: HashMap<String, String>,
        original: &[u8],
        shown: &HashMap<String, crate::rebuild::Shape>,
    ) -> Result<crate::rebuild::Report, String> {
        let (position, mut updated) = self.recipe(step_id)?;
        let allowed = face_edit_fields(&updated["type"])
            .or_else(|| body_edit_fields(&updated["type"]))
            .or_else(|| shape_edit_fields(&updated["type"]))
            .ok_or("This history step has no editable settings.")?;
        if changes.is_empty() || changes.keys().any(|key| !allowed.contains(&key.as_str())) {
            return Err("Choose a setting to edit on this history step.".into());
        }
        let inputs = updated["inputs"]
            .as_object_mut()
            .ok_or("The original step settings are missing.")?;
        for (key, value) in changes {
            if value.is_empty()
                && matches!(
                    key.as_str(),
                    "counterbore_diameter" | "counterbore_depth" | "countersink_diameter"
                )
            {
                inputs.remove(&key);
                continue;
            }
            let value = match key.as_str() {
                "left_hand" | "resize" | "keep" | "keep_tool" => json!(value == "true"),
                _ => Value::String(value),
            };
            inputs.insert(key, value);
        }
        self.rebuild_recipe(position, updated, "Edit step", original, shown)
    }

    /// Rebind a deleted Combine tool or Align target to a selected object.
    /// The original feature ID, unrelated inputs and its undo branch survive.
    pub fn repair_reference(
        &mut self,
        step_id: &str,
        missing_id: &str,
        body_id: &str,
        face: Option<u32>,
        original: &[u8],
        shown: &HashMap<String, crate::rebuild::Shape>,
    ) -> Result<crate::rebuild::Report, String> {
        let (position, mut updated) = self.recipe(step_id)?;
        let ids: HashSet<_> = self.payload["features"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|f| f["id"].as_str())
            .collect();
        let lost = missing_reference(&updated, &ids)
            .filter(|reference| reference.object_id == missing_id)
            .ok_or("This step's missing input has changed.")?;
        let body = self.payload["bodies"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|body| body["id"] == body_id)
            .ok_or("Select an existing body.")?;
        let name = body["name"].as_str().ok_or("This body has no name.")?;
        let producer = body["producer"]
            .as_str()
            .filter(|producer| ids.contains(producer))
            .ok_or("This body has no available modeling step.")?;
        let already_bound = updated["bindings"].as_object().is_some_and(|bindings| {
            bindings.iter().any(|(alias, binding)| {
                alias != &lost.name && (alias == name || binding["object_id"] == body_id)
            })
        });
        if producer == step_id || already_bound {
            return Err("Select a body that is not already an input to this step.".into());
        }
        if lost.face {
            let index = face.ok_or("Select a replacement target face.")?;
            let shape = shown
                .get(body_id)
                .ok_or("This body has no visible geometry.")?;
            let mark =
                occt::face_fingerprint(shape, index).map_err(|error| error.what().to_string())?;
            let old_role = updated["inputs"]["target_face"]["__ref__"]["role"].clone();
            updated["inputs"]["target_face"] = json!({ "__ref__": {
                "feature_id": producer, "body": name, "body_id": body_id, "index_hint": index, "kind": "face", "role": old_role,
                "fingerprint": {"kind": "face", "geometry": mark.geometry, "measure": mark.measure, "center": mark.center,
                    "direction": mark.has_direction.then_some(mark.direction), "radius": mark.has_radius.then_some(mark.radius), "neighbours": []}
            }});
            crate::align::reframe_target(&mut updated["inputs"], shape, index)?;
        } else {
            let key = tools_key(&updated);
            let value = &mut updated["inputs"][key];
            let tools = match value {
                Value::Array(tools) => tools.as_mut_slice(),
                value => std::slice::from_mut(value),
            };
            let tool = tools
                .iter_mut()
                .find(|tool| tool_name(tool) == Some(lost.name.as_str()))
                .ok_or("The old Combine tool could not be read.")?;
            *tool = json!({"__body__": name, "feature_id": producer, "object_id": body_id});
            for key in ["outputs", "dropped"] {
                if let Some(names) = updated[key].as_array_mut() {
                    names.retain(|name| name != &lost.name);
                }
            }
            let keep_tool = updated["inputs"]["keep_tool"].as_bool() == Some(true);
            if let Some(ids) = updated["output_ids"].as_object_mut() {
                ids.shift_remove(&lost.name);
                if keep_tool {
                    ids.insert(name.into(), json!(body_id));
                }
            }
        }
        let bindings = updated["bindings"]
            .as_object_mut()
            .ok_or("The old input was not bound.")?;
        bindings.shift_remove(&lost.name);
        bindings.insert(
            name.into(),
            json!({"feature_id": producer, "object_id": body_id}),
        );

        self.rebuild_recipe(position, updated, "Repair input", original, shown)
    }

    fn recipe(&self, step_id: &str) -> Result<(usize, Value), String> {
        self.payload["features"]
            .as_array()
            .into_iter()
            .flatten()
            .enumerate()
            .find(|(_, feature)| feature["id"] == step_id)
            .map(|(position, feature)| (position, feature.clone()))
            .ok_or_else(|| "This history step is no longer available.".into())
    }

    fn rebuild_recipe(
        &mut self,
        position: usize,
        updated: Value,
        label: &str,
        original: &[u8],
        shown: &HashMap<String, crate::rebuild::Shape>,
    ) -> Result<crate::rebuild::Report, String> {
        let step_id = updated["id"]
            .as_str()
            .ok_or("This step has no identity.")?
            .to_owned();
        let before = self.document();
        let mut candidate = self.clone();
        candidate.payload["features"][position] = updated;
        if signatures(&candidate.payload).is_none() {
            return Err(
                "That input depends on the step you are repairing. Choose a different body.".into(),
            );
        }
        let stale = dependents(&candidate.payload, &BTreeSet::from([step_id.clone()]));
        let report = crate::rebuild::rebuild(&mut candidate, original, shown, &stale)?;
        if let Some(error) = report.failures.get(&step_id) {
            return Err(error.clone());
        }
        candidate.push_undo(label, before)?;
        *self = candidate;
        Ok(report)
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

    /// The groups, as their names and members (body or group names), as SimpleCAD 0.4 saves them.
    pub fn groups(&self) -> Vec<(String, Vec<String>)> {
        self.list("groups")
            .iter()
            .filter_map(|group| {
                let members = group["members"]
                    .as_array()?
                    .iter()
                    .filter_map(Value::as_str);
                Some((
                    group["name"].as_str()?.to_owned(),
                    members.map(String::from).collect(),
                ))
            })
            .collect()
    }

    /// `Document.add_group`: organise bodies or groups under one new group, taking each out of the group
    /// it was in. A group is not geometry, so nothing is rebuilt; it is one undoable step.
    pub fn group(&mut self, members: &[String]) -> Result<String, String> {
        let known: HashSet<String> = ["bodies", "groups"]
            .iter()
            .flat_map(|key| self.list(key))
            .filter_map(|entry| entry["name"].as_str().map(String::from))
            .collect();
        let mut chosen: Vec<String> = Vec::new();
        for member in members {
            if known.contains(member) && !chosen.contains(member) {
                chosen.push(member.clone());
            }
        }
        if chosen.len() < 2 {
            return Err("Select two or more bodies to group.".into());
        }
        let before = self.document();
        let name = self.unique_name("Group");
        let groups = self.groups_mut();
        for group in groups.iter_mut() {
            if let Some(list) = group["members"].as_array_mut() {
                list.retain(|member| !chosen.iter().any(|name| member == name));
            }
        }
        // `Document._prune_groups`: a group left with nothing in it goes.
        groups.retain(|group| {
            group["members"]
                .as_array()
                .is_some_and(|list| !list.is_empty())
        });
        groups.push(json!({
            "name": name, "id": new_feature_id(), "members": chosen, "visible": true, "expanded": true,
        }));
        self.push_undo("Group", before)?;
        Ok(name)
    }

    /// `Document.ungroup`: dissolve a group. Its members return to where it sat, so a nested group's
    /// members are handed up to the parent rather than stranded at the top level.
    pub fn ungroup(&mut self, name: &str) -> Result<(), String> {
        let before = self.document();
        let groups = self.groups_mut();
        let index = groups
            .iter()
            .position(|group| group["name"] == name)
            .ok_or("There is no such group.")?;
        let members = groups.remove(index)["members"].clone();
        for group in groups.iter_mut() {
            if let Some(list) = group["members"].as_array_mut()
                && let Some(at) = list.iter().position(|member| member == name)
            {
                list.splice(at..=at, members.as_array().cloned().unwrap_or_default());
            }
        }
        self.push_undo("Ungroup", before)
    }

    fn groups_mut(&mut self) -> &mut Vec<Value> {
        if !self.payload["groups"].is_array() {
            self.payload["groups"] = json!([]);
        }
        self.payload["groups"].as_array_mut().unwrap()
    }

    /// Round (fillet) or bevel (chamfer) edges of one body, as SimpleCAD 0.4's edge tools record them:
    /// each edge by its curve and its faces, so it is found again after the body changes upstream.
    pub fn add_edge_finish(
        &mut self,
        kind: &str,
        body: &str,
        edges: &[u32],
        value: &str,
        original: &[u8],
        shown: &HashMap<String, crate::rebuild::Shape>,
    ) -> Result<crate::rebuild::Report, String> {
        let (label, key) = match kind {
            "fillet" => ("Fillet", "radius"),
            "chamfer" => ("Chamfer", "distance"),
            _ => return Err("Choose Fillet or Chamfer.".into()),
        };
        if edges.is_empty() {
            return Err("Select the edges to round or bevel.".into());
        }
        let (producer, object) = self.editable(body)?;
        let shape = shown
            .get(&object)
            .ok_or("Select edges of a body with geometry.")?;
        let mut refs = Vec::with_capacity(edges.len());
        for &index in edges {
            refs.push(json!({ "__ref__": self.edge_ref(body, &object, shape, index)? }));
        }
        let mut candidate = self.clone();
        let name = candidate.unique_name(label);
        let feature = json!({
            "id": new_feature_id(), "type": kind, "name": name, "outputs": [body],
            "output_ids": { body: object },
            "inputs": {
                "body": { "__body__": body, "feature_id": producer, "object_id": object },
                "edges": refs, key: value,
            },
            "bindings": { body: { "feature_id": producer, "object_id": object } },
            "dropped": [], "suppressed": false,
        });
        self.add_rebuilt_recipe(candidate, feature, label, original, shown)
    }

    /// Line bodies up in a row along a world axis, with equal gaps between them or a fixed gap, as
    /// SimpleCAD 0.4's Arrange writes it. Every body is an input and an output, bound by object ID.
    /// ponytail: world rows only; a row seated on a target face, and the face patterns, are not made here yet.
    pub fn add_arrangement(
        &mut self,
        bodies: &[String],
        mut inputs: Value,
        original: &[u8],
        shown: &HashMap<String, crate::rebuild::Shape>,
    ) -> Result<crate::rebuild::Report, String> {
        if !inputs.is_object() {
            return Err("The arrangement settings could not be read.".into());
        }
        let mut refs = Vec::new();
        let mut bindings = Map::new();
        let mut output_ids = Map::new();
        for body in bodies {
            let (producer, object) = self.editable(body)?;
            if bindings.contains_key(body) {
                return Err("Choose each body once.".into());
            }
            refs.push(json!({ "__body__": body, "feature_id": producer, "object_id": object }));
            bindings.insert(
                body.clone(),
                json!({ "feature_id": producer, "object_id": object }),
            );
            output_ids.insert(body.clone(), json!(object));
        }
        if bodies.len() < 2 {
            return Err("Choose two or more bodies to arrange.".into());
        }
        inputs["bodies"] = Value::Array(refs);
        inputs["orient"] = json!(false);
        if inputs.get("normal_offset").is_none() {
            inputs["normal_offset"] = json!("0");
        }
        let mut candidate = self.clone();
        let name = candidate.unique_name("Arrange");
        let feature = json!({
            "id": new_feature_id(), "type": "arrange", "name": name, "outputs": bodies,
            "output_ids": output_ids, "inputs": inputs, "bindings": bindings,
            "dropped": [], "suppressed": false,
        });
        self.add_rebuilt_recipe(candidate, feature, "Arrange", original, shown)
    }

    /// Mirror, pattern, scale or combine one body in place, as SimpleCAD 0.4 records those steps.
    /// A Combine's `tools` arrive as body names and are bound, like the body, by object ID.
    pub fn add_body_operation(
        &mut self,
        kind: &str,
        body: &str,
        mut inputs: Value,
        original: &[u8],
        shown: &HashMap<String, crate::rebuild::Shape>,
    ) -> Result<crate::rebuild::Report, String> {
        let label = match kind {
            "mirror" => "Mirror",
            "rectangular_pattern" => "Rectangular Pattern",
            "circular_pattern" => "Circular Pattern",
            "scale" => "Scale",
            "boolean" => "Combine",
            "split" => "Split",
            _ => return Err("Choose an operation.".into()),
        };
        if !inputs.is_object() {
            return Err("The operation settings could not be read.".into());
        }
        let (producer, object) = self.editable(body)?;
        inputs["body"] = json!({"__body__": body, "feature_id": producer, "object_id": object});
        let mut bindings = Map::from_iter([(
            body.to_owned(),
            json!({ "feature_id": producer, "object_id": object }),
        )]);
        if kind == "boolean" {
            let mut tools = Vec::new();
            for tool in inputs["tools"]
                .as_array()
                .ok_or("Choose the bodies to combine with.")?
            {
                let tool = tool
                    .as_str()
                    .filter(|tool| *tool != body)
                    .ok_or("Choose a different body to combine with.")?;
                let (producer, object) = &self.editable(tool)?;
                let bound = json!({ "feature_id": producer, "object_id": object });
                if bindings.insert(tool.to_owned(), bound).is_some() {
                    return Err("Choose each tool body once.".into());
                }
                tools.push(json!({"__body__": tool, "feature_id": producer, "object_id": object}));
            }
            if tools.is_empty() {
                return Err("Choose the bodies to combine with.".into());
            }
            inputs["tools"] = Value::Array(tools);
        }
        let mut candidate = self.clone();
        let name = candidate.unique_name(label);
        let id = new_feature_id();
        // A split consumes the body and makes two new ones, named as SimpleCAD 0.4's split tool names them.
        let (outputs, output_ids) = if kind == "split" {
            let names = [
                candidate.unique_name(&format!("{body} A")),
                candidate.unique_name(&format!("{body} B")),
            ];
            inputs["names"] = json!(names);
            let ids: Map<String, Value> = names
                .iter()
                .map(|name| (name.clone(), json!(crate::graph::output_id(&id, name))))
                .collect();
            (json!(names), Value::Object(ids))
        } else {
            (json!([body]), json!({ body: object }))
        };
        let feature = json!({
            "id": id, "type": kind, "name": name, "outputs": outputs,
            "output_ids": output_ids, "inputs": inputs, "bindings": bindings,
            "dropped": [], "suppressed": false,
        });
        self.add_rebuilt_recipe(candidate, feature, label, original, shown)
    }

    /// An edge reference as SimpleCAD 0.4's edge tools write it: the curve, and its faces' summaries.
    fn edge_ref(
        &self,
        body: &str,
        object: &str,
        shape: &occt::Shape,
        index: u32,
    ) -> Result<Value, String> {
        let prints = occt::edge_fingerprints(shape).map_err(|error| error.what().to_owned())?;
        let print = prints
            .get(index as usize)
            .ok_or("Select edges of one body.")?;
        let curve = &print.curve;
        let neighbours: Vec<String> = print
            .neighbours
            .iter()
            .map(crate::rebuild::neighbour_summary)
            .collect();
        let mut reference = self.face_ref(
            body,
            index,
            json!({
                "kind": "edge", "geometry": curve.geometry, "measure": curve.measure,
                "center": curve.center, "direction": curve.has_direction.then_some(curve.direction),
                "radius": curve.has_radius.then_some(curve.radius), "neighbours": neighbours,
            }),
        );
        reference["kind"] = json!("edge");
        reference["body_id"] = json!(object);
        Ok(reference)
    }

    /// A face reference, anchored to its body by object ID, as Offset Plane writes it.
    fn anchored_face_ref(
        &self,
        body: &str,
        object: &str,
        shape: &occt::Shape,
        index: u32,
    ) -> Result<Value, String> {
        let mark = occt::face_fingerprint(shape, index).map_err(|error| error.what().to_owned())?;
        let mut reference = self.face_ref(
            body,
            index,
            json!({
                "kind": "face", "geometry": mark.geometry, "measure": mark.measure,
                "center": mark.center, "direction": mark.has_direction.then_some(mark.direction),
                "radius": mark.has_radius.then_some(mark.radius), "neighbours": [],
            }),
        );
        reference["body_id"] = json!(object);
        Ok(reference)
    }

    /// Midplane, Plane at Angle, Plane through Points, Tangent Plane, Axis or Point, written as SimpleCAD 0.4
    /// writes them: the picked faces and edges by reference, each body bound by object ID, and the
    /// settings in `inputs` (expressions, or a Plane through Points' three `points`).
    pub fn add_construction(
        &mut self,
        kind: &str,
        faces: &[(String, u32)],
        edges: &[(String, u32)],
        mut inputs: Value,
        original: &[u8],
        shown: &HashMap<String, crate::rebuild::Shape>,
    ) -> Result<crate::rebuild::Report, String> {
        let (label, wanted): (&str, &[(usize, usize)]) = match kind {
            "midplane" => ("Midplane", &[(2, 0)]),
            "plane_at_angle" => ("Plane at Angle", &[(1, 1)]),
            "plane_through_points" => ("Plane through Points", &[(0, 0)]),
            "tangent_plane" => ("Tangent Plane", &[(1, 0)]),
            "construction_axis" => ("Axis", &[(1, 0), (0, 1)]),
            "construction_point" => ("Point", &[(1, 0), (0, 0)]),
            _ => return Err("Choose a construction tool.".into()),
        };
        if !wanted.contains(&(faces.len(), edges.len())) {
            return Err(match kind {
                "midplane" => "Pick the two faces the midplane sits between.",
                "plane_at_angle" => "Pick a flat face and the edge to turn it about.",
                "tangent_plane" => "Pick the round face the plane touches.",
                "construction_axis" => "Pick an edge or a round face.",
                _ => "Pick one face, or none.",
            }
            .into());
        }
        if !inputs.is_object() {
            return Err("The construction settings could not be read.".into());
        }
        let latest = self.latest();
        let mut bindings = Map::new();
        let mut bind = |body: &str| -> Result<(String, &crate::rebuild::Shape), String> {
            let (producer, object) = latest.get(body).ok_or("Select a body with geometry.")?;
            bindings.insert(
                body.to_owned(),
                json!({ "feature_id": producer, "object_id": object }),
            );
            Ok((
                object.clone(),
                shown.get(object).ok_or("Select a body with geometry.")?,
            ))
        };
        let mut face_refs = Vec::new();
        for (body, index) in faces {
            let (object, shape) = bind(body)?;
            face_refs
                .push(json!({ "__ref__": self.anchored_face_ref(body, &object, shape, *index)? }));
        }
        let mut edge_refs = Vec::new();
        for (body, index) in edges {
            let (object, shape) = bind(body)?;
            edge_refs.push(json!({ "__ref__": self.edge_ref(body, &object, shape, *index)? }));
        }
        if kind == "midplane" {
            inputs["faces"] = Value::Array(face_refs);
        } else {
            if let Some(face) = face_refs.pop() {
                inputs["face"] = face;
            }
            if let Some(edge) = edge_refs.pop() {
                inputs["edge"] = edge;
            }
        }
        let mut candidate = self.clone();
        let name = candidate.unique_name(label);
        let id = new_feature_id();
        let output = format!("__construction__{name}");
        let feature = json!({
            "id": id, "type": kind, "name": name, "outputs": [output],
            "output_ids": { &output: crate::graph::output_id(&id, &output) },
            "inputs": inputs, "bindings": bindings, "dropped": [], "suppressed": false,
        });
        self.add_rebuilt_recipe(candidate, feature, label, original, shown)
    }

    pub fn add_offset_plane(
        &mut self,
        body: &str,
        index: u32,
        distance: &str,
        original: &[u8],
        shown: &HashMap<String, crate::rebuild::Shape>,
    ) -> Result<crate::rebuild::Report, String> {
        let (producer, object) = self.latest().remove(body).ok_or("Select a planar face.")?;
        let shape = shown.get(&object).ok_or("Select a body with geometry.")?;
        let mark = occt::face_fingerprint(shape, index).map_err(|error| error.what().to_owned())?;
        if mark.geometry != "plane" {
            return Err("An offset plane needs a flat face to offset from.".into());
        }
        let mut reference = self.face_ref(
            body,
            index,
            json!({
                "kind": "face", "geometry": mark.geometry, "measure": mark.measure,
                "center": mark.center, "direction": mark.has_direction.then_some(mark.direction),
                "radius": mark.has_radius.then_some(mark.radius), "neighbours": [],
            }),
        );
        reference["body_id"] = json!(object);

        let label = "Offset Plane";
        let mut candidate = self.clone();
        let name = candidate.unique_name(label);
        let id = new_feature_id();
        let output = format!("__construction__{name}");
        let feature = json!({
            "id": id, "type": "offset_plane", "name": name, "outputs": [output],
            "output_ids": { &output: crate::graph::output_id(&id, &output) },
            "inputs": { "face": { "__ref__": reference }, "distance": distance },
            "bindings": { body: { "feature_id": producer, "object_id": object } },
            "dropped": [], "suppressed": false,
        });
        self.add_rebuilt_recipe(candidate, feature, label, original, shown)
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
        let (producer, object) = self.editable(body)?;
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
        self.append_body_step(body, id, feature);
        self.record(id, "Pull", &object, brep, before, "")
    }

    pub(crate) fn record_align(
        &mut self,
        moving: &str,
        target: &str,
        inputs: Value,
        brep: Vec<u8>,
        message: &str,
    ) -> Result<(), String> {
        let (moving_producer, object) = &self.editable(moving)?;
        let (target_producer, target_object) = &self.editable(target)?;
        let before = self.document();
        let id = new_feature_id();
        let name = self.unique_name("Align");
        let feature = json!({
            "id": id, "type": "align", "name": name, "inputs": inputs,
            "outputs": [moving], "output_ids": { moving: object },
            "bindings": {
                moving: { "feature_id": moving_producer, "object_id": object },
                target: { "feature_id": target_producer, "object_id": target_object },
            },
            "suppressed": false, "dropped": [],
        });
        self.append_body_step(moving, &id, feature);
        self.record(&id, "Align", object, brep, before, message)
    }

    pub(crate) fn record_body_feature(
        &mut self,
        body: &str,
        mut feature: Value,
        label: &str,
        brep: Vec<u8>,
        message: &str,
    ) -> Result<(), String> {
        let before = self.document();
        let latest = self.latest();
        let (producer, object) = &self.editable(body)?;
        let id = new_feature_id();
        feature["id"] = json!(id);
        feature["name"] = json!(self.unique_name(label));
        feature["outputs"] = json!([body]);
        feature["output_ids"] = json!({ body: object });
        let mut bindings = json!({ body: { "feature_id": producer, "object_id": object } });
        for input in crate::graph::input_names(
            feature["type"].as_str().unwrap_or_default(),
            &feature["inputs"],
        ) {
            if let Some((producer, object)) = latest.get(&input) {
                bindings[&input] = json!({ "feature_id": producer, "object_id": object });
            }
        }
        feature["bindings"] = bindings;
        feature["suppressed"] = json!(false);
        feature["dropped"] = json!([]);
        self.append_body_step(body, &id, feature);
        self.record(&id, label, object, brep, before, message)
    }

    fn append_body_step(&mut self, body: &str, id: &str, feature: Value) {
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
    }

    pub fn can_undo(&self) -> bool {
        self.has_history("undo")
    }

    pub fn can_redo(&self) -> bool {
        self.has_history("redo")
    }

    fn has_history(&self, stack: &str) -> bool {
        self.payload["history"][stack]
            .as_array()
            .is_some_and(|entries| !entries.is_empty())
    }

    pub fn undo(&mut self) -> Result<(), String> {
        self.move_cached_history(false)
    }

    pub fn redo(&mut self) -> Result<(), String> {
        self.move_cached_history(true)
    }

    fn move_cached_history(&mut self, redo: bool) -> Result<(), String> {
        let previous = self.payload.clone();
        let producers = self.latest();
        let before =
            signatures(&self.payload).ok_or("This project's steps could not be fingerprinted.")?;
        let result = self
            .move_history(redo)
            .and_then(|_| self.show_cached_results(&producers, &before));
        if result.is_err() {
            self.payload = previous;
        }
        result
    }

    /// Move a saved patch between the history stacks. The caller rebuilds at
    /// the desired history point, reusing cached outputs for its signatures.
    pub fn move_history(&mut self, redo: bool) -> Result<BTreeSet<String>, String> {
        let unreadable = || "This project's history could not be read.".to_string();
        let (source, destination) = if redo {
            ("redo", "undo")
        } else {
            ("undo", "redo")
        };
        let history = &self.payload["history"];
        let action = history[source]
            .as_array()
            .and_then(|stack| stack.last())
            .cloned()
            .ok_or_else(|| format!("There is nothing to {source}."))?;
        if !action["patch"].is_object()
            || !action["label"].is_string()
            || !(history[destination].is_array() || history.get(destination).is_none())
        {
            return Err(unreadable());
        }
        let current = signatures(&self.payload).ok_or_else(unreadable)?;
        let patch = &action["patch"];
        let inverse = self.inverse_patch(patch);
        let old = self.payload.clone();
        let stale = self.apply_patch(patch);
        let mut stale_all = dependents(&old, &stale);
        stale_all.extend(dependents(&self.payload, &stale));
        let Some(next) = signatures(&self.payload) else {
            self.payload = old;
            return Err(unreadable());
        };
        stale_all.extend(
            current
                .keys()
                .filter(|id| current.get(*id) != next.get(*id))
                .cloned(),
        );
        let mut cache_keys: Vec<&String> =
            stale_all.iter().filter_map(|f| current.get(f)).collect();
        cache_keys.sort();
        cache_keys.dedup();
        let entry = json!({ "label": action["label"], "patch": inverse, "cache_keys": cache_keys });
        self.payload["history"][source]
            .as_array_mut()
            .unwrap()
            .pop();
        self.payload["history"]
            .as_object_mut()
            .unwrap()
            .entry(destination)
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .unwrap()
            .push(entry);
        Ok(stale_all)
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
            if ["title", "counters", "retired_ids", "metadata", "assets"].contains(&key.as_str()) {
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
        previous_signatures: &BTreeMap<String, String>,
    ) -> Result<(), String> {
        let current =
            signatures(&self.payload).ok_or("This project's steps could not be fingerprinted.")?;
        let mut geometry = Map::new();
        for (name, (producer, object)) in self.latest() {
            if !self.list("bodies").iter().any(|b| b["name"] == json!(name)) {
                continue;
            }
            let signature = current.get(&producer).cloned().unwrap_or_default();
            if before.get(&name) == Some(&(producer.clone(), object.clone()))
                && previous_signatures.get(&producer) == current.get(&producer)
            {
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
                    "\"{name}\" must be rebuilt to restore this history step."
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
        model.record_primitive(
            id,
            "box",
            "Box",
            json!({ "width": "40 mm", "depth": "30 mm", "height": "20 mm" }),
            brep,
        )?;
        Ok(model)
    }

    pub(crate) fn add_primitive(
        &mut self,
        kind: &str,
        label: &str,
        inputs: Value,
        brep: Vec<u8>,
    ) -> Result<String, String> {
        self.record_primitive(&new_feature_id(), kind, label, inputs, brep)
    }

    /// Import each solid as its own editable feature, keeping all solids in
    /// one undo action. The asset remains available if that action is redone.
    pub fn add_imports(
        &mut self,
        parts: Vec<ImportedPart>,
        offset: [f64; 3],
    ) -> Result<Vec<String>, String> {
        let before = self.document();
        let undo_len = self.payload["history"]["undo"]
            .as_array()
            .map_or(0, Vec::len);
        let mut names = Vec::with_capacity(parts.len());
        for ImportedPart {
            name: label,
            asset: brep,
            placed,
            note,
        } in parts
        {
            let asset = format!("{:x}", Sha256::digest(&brep));
            let assets = self
                .payload
                .as_object_mut()
                .ok_or("Invalid project.")?
                .entry("assets")
                .or_insert_with(|| json!([]))
                .as_array_mut()
                .ok_or("This project's assets could not be read.")?;
            if !assets.iter().any(|other| other == &asset) {
                assets.push(json!(&asset));
                assets.sort_unstable_by(|a, b| a.as_str().cmp(&b.as_str()));
            }
            let [dx, dy, dz] = offset;
            let mut inputs = json!({ "asset": asset, "dx": dx, "dy": dy, "dz": dz });
            if !note.is_empty() {
                inputs["mesh_note"] = json!(note);
            }
            let name = self.record_primitive(
                &new_feature_id(),
                "embedded_import",
                &label,
                inputs,
                placed,
            )?;
            self.blobs
                .entry(asset.clone())
                .or_insert_with(|| brep.into());
            self.new_assets.insert(asset);
            names.push(name);
        }
        if !names.is_empty() {
            self.payload["history"]["undo"]
                .as_array_mut()
                .unwrap()
                .truncate(undo_len);
            self.push_undo("Import", before)?;
        }
        Ok(names)
    }

    fn record_primitive(
        &mut self,
        id: &str,
        kind: &str,
        label: &str,
        inputs: Value,
        brep: Vec<u8>,
    ) -> Result<String, String> {
        let note = inputs["mesh_note"].as_str().unwrap_or("").to_owned();
        // Named before the undo snapshot, as the panel does.
        let name = self.unique_name(label);
        let before = self.document();
        let object = crate::graph::output_id(id, &name);
        let payload = self.payload.as_object_mut().unwrap();
        payload["features"].as_array_mut().unwrap().push(json!({
            "id": id, "type": kind, "name": name, "inputs": inputs,
            "outputs": [name], "output_ids": { &name: object }, "bindings": {},
            "suppressed": false, "dropped": [],
        }));
        payload["bodies"].as_array_mut().unwrap().push(json!({
            "id": object, "name": name, "visible": true, "color": null, "producer": id, "metadata": {},
        }));
        self.record(id, label, &object, brep, before, &note)?;
        Ok(name)
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
        message: &str,
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
            "message": message, "version": CACHE_VERSION,
        });
        payload.entry("geometry").or_insert_with(|| json!({}))[object] =
            json!({ "sha256": sha, "feature_id": id, "signature": signature, "current": true });
        payload.entry("diagnostics").or_insert_with(|| json!({}))[id] =
            json!({ "state": "ok", "message": message });
        self.blobs.insert(sha, brep.into());

        self.push_undo(label, before)
    }

    fn push_undo(&mut self, label: &str, before: Value) -> Result<(), String> {
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
            let replaced = name
                .strip_prefix("geometry/")
                .and_then(|name| name.strip_suffix(".brep"))
                .is_some_and(|sha| self.blobs.contains_key(sha))
                || name
                    .strip_prefix("assets/")
                    .and_then(|name| name.strip_suffix(".brep"))
                    .is_some_and(|sha| self.new_assets.contains(sha));
            if name == "model.json" || name.starts_with("bodies/") || entry.is_dir() || replaced {
                continue;
            }
            let mut bytes = Vec::new();
            entry.read_to_end(&mut bytes).map_err(|e| damaged(&e))?;
            out.start_file(name, options).map_err(|e| damaged(&e))?;
            out.write_all(&bytes).map_err(|e| damaged(&e))?;
        }
        for (sha, blob) in &self.blobs {
            let name = format!("geometry/{sha}.brep");
            out.start_file(name, options).map_err(|e| damaged(&e))?;
            out.write_all(blob).map_err(|e| damaged(&e))?;
        }
        for sha in &self.new_assets {
            let blob = self
                .blobs
                .get(sha)
                .ok_or("This project's imported geometry is missing.")?;
            out.start_file(format!("assets/{sha}.brep"), options)
                .map_err(|e| damaged(&e))?;
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
fn edit_values(feature: &Value, fields: Option<&[&str]>) -> Option<HashMap<String, String>> {
    Some(
        fields?
            .iter()
            .filter_map(|key| {
                let value = match feature["inputs"].get(*key)? {
                    Value::String(text) => text.clone(),
                    Value::Number(number) => number.to_string(),
                    Value::Bool(state) => state.to_string(),
                    _ => return None,
                };
                Some(((*key).into(), value))
            })
            .collect(),
    )
}

fn shape_edit_fields(kind: &Value) -> Option<&'static [&'static str]> {
    Some(match kind.as_str()? {
        "box" | "oval" | "pill" => &["width", "depth", "height"],
        "cylinder" => &["radius", "height"],
        "sphere" => &["radius"],
        "cone" => &["bottom_radius", "top_radius", "height"],
        "torus" => &["major_radius", "minor_radius"],
        "tube" => &["outer_radius", "inner_radius", "height"],
        "wedge" => &["width", "depth", "height", "top_width"],
        "star" => &["size", "inner_ratio", "height"],
        "heart" | "lightning" => &["size", "height"],
        "cross" => &["size", "arm_width", "height"],
        "crescent" => &["size", "thickness", "height"],
        "polygon_prism" => &["sides", "across_corners", "across_flats", "height"],
        _ => return None,
    })
}

/// The settings of a body operation that History can edit; its directions stay as they were made.
fn body_edit_fields(kind: &Value) -> Option<&'static [&'static str]> {
    Some(match kind.as_str()? {
        "mirror" => &["keep"],
        "rectangular_pattern" => &["count_x", "count_y", "spacing_x", "spacing_y"],
        "circular_pattern" => &["count", "total_angle"],
        "scale" => &["factor"],
        "boolean" => &["operation", "keep_tool"],
        "split" => &["position"],
        "arrange" => &["mode", "axis", "gap"],
        _ => return None,
    })
}

fn face_edit_fields(kind: &Value) -> Option<&'static [&'static str]> {
    match kind.as_str()? {
        "offset_plane" => Some(&["distance"]),
        "plane_at_angle" => Some(&["angle"]),
        "tangent_plane" => Some(&["around"]),
        "construction_point" => Some(&["x", "y", "z"]),
        "shell" => Some(&["thickness"]),
        "fillet" => Some(&["radius"]),
        "chamfer" => Some(&["distance"]),
        "hole" => Some(&[
            "style",
            "depth_mode",
            "diameter",
            "depth",
            "counterbore_diameter",
            "counterbore_depth",
            "countersink_diameter",
            "countersink_angle",
            "designation",
            "clearance",
            "form",
            "thread_length",
            "from_end",
            "left_hand",
        ]),
        "thread" => Some(&[
            "designation",
            "clearance",
            "form",
            "length",
            "from_end",
            "left_hand",
            "resize",
        ]),
        "text" => Some(&[
            "text",
            "font_family",
            "mode",
            "text_height",
            "depth",
            "offset_x",
            "offset_y",
            "rotation",
        ]),
        "face_texture" => Some(&[
            "pattern",
            "mode",
            "size",
            "spacing",
            "depth",
            "offset_u",
            "offset_v",
            "rotation",
            "edge_clearance",
            "hole_clearance",
            "text_clearance",
            "other_clearance",
        ]),
        _ => None,
    }
}

fn tools_key(step: &Value) -> &'static str {
    if step["inputs"]["tools"]
        .as_array()
        .is_some_and(|tools| !tools.is_empty())
    {
        "tools"
    } else {
        "tool"
    }
}

fn tool_name(value: &Value) -> Option<&str> {
    value["__body__"].as_str().or_else(|| value.as_str())
}

/// A step bound to no step, or to one that no longer exists: it cannot rebuild as saved.
fn lost_binding(step: &Value, ids: &HashSet<&str>) -> bool {
    step["bindings"].as_object().is_some_and(|bindings| {
        bindings.values().any(|binding| {
            binding["feature_id"]
                .as_str()
                .is_some_and(|producer| producer.is_empty() || !ids.contains(producer))
        })
    })
}

fn missing_reference(step: &Value, available: &HashSet<&str>) -> Option<MissingInput> {
    let face = step["type"] == "align";
    let sources = match step["type"].as_str()? {
        "align" => vec![step["inputs"]["target_face"]["__ref__"]["body"].as_str()?],
        "boolean" => {
            let tools = &step["inputs"][tools_key(step)];
            tools
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or_else(|| std::slice::from_ref(tools))
                .iter()
                .filter_map(tool_name)
                .collect()
        }
        _ => return None,
    };
    let bindings = step["bindings"].as_object()?;
    // This repair changes a single input and must leave a buildable step.
    let mut lost = bindings.iter().filter(|(_, binding)| {
        !binding["feature_id"]
            .as_str()
            .is_some_and(|producer| available.contains(producer))
    });
    let (name, binding) = lost.next()?;
    if lost.next().is_some() || !sources.contains(&name.as_str()) {
        return None;
    }
    Some(MissingInput {
        name: name.clone(),
        object_id: binding["object_id"].as_str()?.into(),
        face,
    })
}

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

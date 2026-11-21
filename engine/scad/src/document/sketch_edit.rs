use super::{Model, new_feature_id};
use crate::graph::output_id;
use crate::rebuild::{Report, Shape, rebuild};
use crate::sketch::{Solution, solve};
use serde_json::{Value, json};
use std::collections::{BTreeSet, HashMap};

impl Model {
    pub fn sketch_draft(&self, id: &str) -> Result<Value, String> {
        let (_, feature) = self.recipe(id)?;
        if feature["type"] != "sketch" {
            return Err("Select a sketch from History.".into());
        }
        Ok(feature["inputs"]["sketch"].clone())
    }

    pub fn solve_sketch_draft(&self, recipe: &Value) -> Result<Solution, String> {
        let (parameters, _) = crate::evaluate::resolve(
            self.payload["parameters"]
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or(&[]),
        );
        solve(recipe, &parameters).map_err(|error| error.message)
    }

    pub fn apply_sketch_draft(
        &mut self,
        id: &str,
        creating: bool,
        mut draft: Value,
        original: &[u8],
        shown: &HashMap<String, Shape>,
    ) -> Result<Report, String> {
        if !draft.is_object() {
            return Err("The sketch geometry could not be read.".into());
        }
        // Check the plane and the geometry even when nothing uses this sketch yet.
        crate::sketch::modeling::read_profile(&draft).map_err(|error| error.message)?;
        if !creating {
            let (position, mut feature) = self.recipe(id)?;
            if feature["type"] != "sketch" {
                return Err("Select a sketch from History.".into());
            }
            draft["name"] = feature["name"].clone();
            feature["inputs"]["sketch"] = draft;
            return self.rebuild_recipe(position, feature, "Edit Sketch", original, shown);
        }
        if uuid::Uuid::parse_str(id).is_err()
            || self.recipe(id).is_ok()
            || self.list("retired_ids").iter().any(|retired| retired == id)
        {
            return Err("This sketch does not have a unique identity.".into());
        }
        let mut candidate = self.clone();
        let name = candidate.unique_name("Sketch");
        draft["name"] = json!(name);
        let output = format!("__sketch__{name}");
        let feature = json!({
            "id": id, "type": "sketch", "name": name, "outputs": [output],
            "output_ids": { &output: output_id(id, &output) },
            "inputs": { "sketch": draft }, "bindings": {}, "dropped": [], "suppressed": false,
        });
        self.add_rebuilt_recipe(candidate, feature, "Sketch", original, shown)
    }

    pub fn apply_sketch_solid(
        &mut self,
        kind: &str,
        profile_ids: &[String],
        mut inputs: Value,
        original: &[u8],
        shown: &HashMap<String, Shape>,
    ) -> Result<Report, String> {
        let label = match (kind, profile_ids.len()) {
            ("extrude", 1) => "Extrude",
            ("revolve", 1) => "Revolve",
            ("sweep", 2) => "Sweep",
            ("loft", 2..) => "Loft",
            ("path_pattern", 1) => "Pattern Along Path",
            _ => return Err("Select the sketches this operation needs.".into()),
        };
        if !inputs.is_object() {
            return Err("The sketch operation settings could not be read.".into());
        }
        let mut names = Vec::with_capacity(profile_ids.len());
        let mut bindings = serde_json::Map::new();
        for id in profile_ids {
            let (_, sketch) = self.recipe(id)?;
            let name = sketch["name"].as_str().ok_or("This sketch has no name.")?;
            let key = format!("__sketch__{name}");
            let object = sketch["output_ids"][&key]
                .as_str()
                .filter(|_| sketch["type"] == "sketch")
                .ok_or("One of the selected sketches is unavailable.")?;
            if bindings
                .insert(key, json!({"feature_id": id, "object_id": object}))
                .is_some()
            {
                return Err("Select a different sketch for each input.".into());
            }
            names.push(name.to_owned());
        }
        match kind {
            "sweep" => {
                inputs["profile"] = json!(names[0]);
                inputs["path"] = json!(names[1]);
            }
            "loft" => inputs["sketches"] = json!(names),
            "path_pattern" => inputs["path"] = json!(names[0]),
            _ => inputs["sketch"] = json!(names[0]),
        }
        let mut candidate = self.clone();
        let name = candidate.unique_name(label);
        let id = new_feature_id();
        let (output, object) = if kind == "path_pattern" {
            let body = inputs["body"]
                .as_str()
                .filter(|body| {
                    self.payload["bodies"]
                        .as_array()
                        .is_some_and(|bodies| bodies.iter().any(|entry| entry["name"] == *body))
                })
                .ok_or("Select a body to repeat along the path.")?
                .to_owned();
            let (producer, object) = self
                .latest()
                .remove(&body)
                .ok_or("The selected body has no available modeling step.")?;
            inputs["body"] = json!({"__body__": body, "feature_id": producer, "object_id": object});
            bindings.insert(
                body.clone(),
                json!({"feature_id": producer, "object_id": object}),
            );
            (body, object)
        } else {
            (name.clone(), output_id(&id, &name))
        };
        let feature = json!({
            "id": id, "type": kind, "name": name, "outputs": [output],
            "output_ids": { &output: object }, "inputs": inputs,
            "bindings": bindings, "dropped": [], "suppressed": false
        });
        self.add_rebuilt_recipe(candidate, feature, label, original, shown)
    }

    pub(super) fn add_rebuilt_recipe(
        &mut self,
        mut candidate: Model,
        feature: Value,
        label: &str,
        original: &[u8],
        shown: &HashMap<String, Shape>,
    ) -> Result<Report, String> {
        let id = feature["id"]
            .as_str()
            .ok_or("This step has no identity.")?
            .to_owned();
        candidate.payload["features"]
            .as_array_mut()
            .ok_or("This project has no feature list.")?
            .push(feature);
        let report = rebuild(
            &mut candidate,
            original,
            shown,
            &BTreeSet::from([id.clone()]),
        )?;
        if let Some(error) = report.failures.get(&id) {
            return Err(error.clone());
        }
        candidate.push_undo(label, self.document())?;
        *self = candidate;
        Ok(report)
    }
}

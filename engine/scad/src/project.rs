//! Reading `.scad3` projects, the format of the Python app (`simplecad/core/project.py`):
//! a zip holding `model.json` and each body's B-Rep. Format 1 stores a body at
//! `bodies/<name>.brep`; formats 2–4 at `geometry/<sha256>.brep`, named in `model.json`.

use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::{Cursor, Read};
use std::rc::Rc;

/// The newest format this build reads; the Python app refuses newer files the same way.
pub const FORMAT_VERSION: i64 = 4;
/// `CACHE_VERSION` in `simplecad/core/result_store.py`; a test keeps them equal.
pub const CACHE_VERSION: &str = "simplecad-feature-cache-2-occt-7.9";

pub struct SavedBody {
    pub id: String,
    pub name: String,
    pub visible: bool,
    /// The saved B-Rep; `None` when the file has none for this body or it is damaged.
    pub brep: Option<Vec<u8>>,
    /// Its sha256, when the file names it: rebuilds read the same blob by it, and share the shape read.
    pub sha: Option<String>,
    /// Where the B-Rep is placed: the 3×4 matrix of a cached "located" output.
    pub location: Option<[f64; 12]>,
}

pub struct Project {
    pub model: Value,
    pub bodies: Vec<SavedBody>,
    /// What could not be read, in words.
    pub notes: Vec<String>,
    pub(crate) recovered_blobs: BTreeMap<String, Vec<u8>>,
}

const DAMAGED: &str = "This project could not be read. The file may be damaged.";

/// The zip entry of a body in a format 1 file, as the Python app's `_safe_entry` names it.
///
/// ponytail: `char::is_alphanumeric` stands in for Python's `str.isalnum`; they
/// differ only on rare combining marks, in names of format 1 files.
pub fn legacy_entry(name: &str) -> String {
    let keep: String = name
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || "-_.".contains(c) {
                c
            } else {
                '_'
            }
        })
        .collect();
    format!(
        "bodies/{}.brep",
        if keep.is_empty() { "body" } else { &keep }
    )
}

/// Every body of the project, shown or hidden, in the file's order. A missing or
/// damaged body is listed without geometry and noted, as the Python app skips it.
pub fn read(data: &[u8]) -> Result<Project, String> {
    read_bodies(data, true)
}

/// Inspect the saved body blobs before recovering failed steps for display.
pub fn read_saved(data: &[u8]) -> Result<Project, String> {
    read_bodies(data, false)
}

fn read_bodies(data: &[u8], recover: bool) -> Result<Project, String> {
    let mut archive = zip::ZipArchive::new(Cursor::new(data))
        .map_err(|_| "This is not a SimpleCAD project, or the file is damaged.".to_string())?;
    let mut entry = |name: &str| -> Option<Vec<u8>> {
        let mut file = archive.by_name(name).ok()?;
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).ok()?;
        Some(bytes)
    };
    let raw = entry("model.json")
        .ok_or("This file has no SimpleCAD model in it. The file may be damaged.")?;
    let mut model: Value = serde_json::from_slice(&raw).map_err(|_| DAMAGED.to_string())?;
    if !model.is_object() {
        return Err(DAMAGED.into());
    }
    let format = match model.get("format") {
        None => 1,
        Some(v) => v.as_i64().filter(|f| *f >= 1).ok_or(DAMAGED)?,
    };
    if format > FORMAT_VERSION {
        return Err(
            "This project was saved by a newer SimpleCAD. Update SimpleCAD to open it.".into(),
        );
    }
    // As the Python app holds it: format 1 gains its ids here, which the geometry is found by.
    crate::graph::normalise(&mut model);
    let listed: Vec<&Value> = match model.get("bodies") {
        Some(Value::Array(list)) => list.iter().collect(),
        Some(Value::Object(map)) => map.values().collect(),
        _ => Vec::new(),
    };
    let named: Vec<(&str, &Value)> = listed
        .into_iter()
        .filter_map(|b| Some((b.get("name")?.as_str()?, b)))
        .collect();

    let mut notes = Vec::new();
    let mut bodies = Vec::new();
    for &(name, body) in &named {
        let id = body["id"].as_str().unwrap_or_default().to_owned();
        let visible = body.get("visible") != Some(&Value::Bool(false));
        if format >= 2
            && let Some((blob, sha, location)) = cached_output(&model, body, &mut entry)
        {
            bodies.push(SavedBody {
                id,
                name: name.to_string(),
                visible,
                brep: Some(blob),
                sha: Some(sha),
                location,
            });
            continue;
        }
        let mut known = None;
        let brep = if format >= 2 {
            let sha = body
                .get("id")
                .and_then(Value::as_str)
                .and_then(|id| model.get("geometry")?.get(id)?.get("sha256")?.as_str())
                .filter(|s| {
                    s.len() == 64
                        && s.bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                });
            known = sha.map(String::from);
            sha.and_then(|sha| {
                entry(&format!("geometry/{sha}.brep"))
                    .filter(|blob| format!("{:x}", Sha256::digest(blob)) == sha)
            })
        } else {
            // Two bodies whose names map to one entry can't be told apart, so neither is read.
            let path = legacy_entry(name);
            if named
                .iter()
                .filter(|(other, _)| legacy_entry(other) == path)
                .count()
                > 1
            {
                notes.push(format!(
                    "\"{name}\" shares its saved name with another body; open it in SimpleCAD 0.4 to rebuild it."
                ));
                None
            } else {
                entry(&path).or_else(|| {
                    notes.push(format!("\"{name}\" could not be read from this file."));
                    None
                })
            }
        };
        bodies.push(SavedBody {
            id,
            name: name.to_string(),
            visible,
            sha: known.filter(|_| brep.is_some()),
            brep,
            location: None,
        });
    }
    let mut recovered_blobs = BTreeMap::new();
    if format >= 2 && recover {
        let missing: Vec<_> = bodies
            .iter()
            .zip(&named)
            .filter(|(body, _)| body.brep.is_none())
            .filter_map(|(_, (_, saved))| {
                let oid = saved["id"].as_str()?;
                let producer = saved["producer"].as_str()?;
                matches!(
                    model["diagnostics"][producer]["state"].as_str(),
                    Some("needs_attention" | "failed")
                )
                .then_some((oid, producer))
            })
            .collect();
        let mut recovered = if missing.is_empty() {
            Default::default()
        } else {
            crate::rebuild::recover_saved(&model, data, &missing)
        };
        let mut geometry = Vec::new();
        for (body, (_, saved)) in bodies.iter_mut().zip(&named) {
            if body.brep.is_some() {
                continue;
            }
            let oid = saved["id"].as_str().unwrap_or_default();
            if let Some(blob) = recovered.remove(oid) {
                let sha = format!("{:x}", Sha256::digest(&blob));
                geometry.push((oid.to_string(), json!({
                    "sha256": sha, "feature_id": saved["producer"], "signature": null, "current": false,
                })));
                recovered_blobs.insert(sha.clone(), blob.clone());
                body.brep = Some(blob);
                body.sha = Some(sha);
            } else if model["geometry"][oid]["sha256"].is_string() {
                notes.push(format!(
                    "\"{}\" could not be read from this file.",
                    body.name
                ));
            } else {
                notes.push(format!(
                    "\"{}\" has no saved geometry; open it in SimpleCAD 0.4 to rebuild it.",
                    body.name
                ));
            }
        }
        if !geometry.is_empty() {
            let mut manifest = model["geometry"].as_object().cloned().unwrap_or_default();
            manifest.extend(geometry);
            model["geometry"] = Value::Object(manifest);
        }
    }
    let mut project = Project {
        model,
        bodies,
        notes,
        recovered_blobs,
    };
    if recover
        && format == 1
        && project.model["features"]
            .as_array()
            .is_some_and(|steps| !steps.is_empty())
        && project.bodies.iter().any(|body| body.brep.is_none())
    {
        rebuild_legacy_display(&mut project, data)?;
    }
    if recover && !project.bodies.is_empty() && project.bodies.iter().all(|b| b.brep.is_none()) {
        return Err(
            "This project has no saved bodies this build can show. Open it in SimpleCAD 0.4."
                .into(),
        );
    }
    Ok(project)
}

/// A legacy archive used entry names derived from the body name. If it lacks
/// some geometry, show the recipe's results, using any readable prior solids
/// as fallbacks when a feature needs attention.
fn rebuild_legacy_display(project: &mut Project, original: &[u8]) -> Result<(), String> {
    let shown = project
        .bodies
        .iter()
        .filter_map(|body| {
            let shape = occt::read_brep(body.brep.as_deref()?).ok()?;
            Some((body.id.clone(), Rc::new(shape)))
        })
        .collect();
    let mut model = crate::document::Model::open(project);
    crate::rebuild::rebuild(&mut model, original, &shown, &Default::default())?;
    let mut notes = Vec::new();
    project.bodies = model.payload["bodies"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|body| {
            let id = body["id"].as_str()?;
            let name = body["name"].as_str()?;
            let made = &model.payload["geometry"][id];
            let sha = made["sha256"].as_str().map(String::from);
            let brep = sha
                .as_deref()
                .and_then(|sha| model.blobs.get(sha))
                .map(|blob| blob.to_vec());
            // A moved body is its base's blob and where it is placed.
            let location =
                crate::rebuild::location(made).and_then(|m| <[f64; 12]>::try_from(m).ok());
            if brep.is_none() {
                notes.push(format!(
                    "\"{name}\" has no readable saved or rebuilt geometry."
                ));
            }
            Some(SavedBody {
                id: id.into(),
                name: name.into(),
                visible: body["visible"] != false,
                sha: sha.filter(|_| brep.is_some()),
                brep,
                location,
            })
        })
        .collect();
    project.notes = notes;
    // Open and Save As should keep the feature cache and the bodies this
    // rebuild found, even though the original archive used format 1 paths.
    model.payload["format"] = json!(FORMAT_VERSION);
    project.model = model.payload;
    project.recovered_blobs = model
        .blobs
        .into_iter()
        .map(|(sha, blob)| (sha, blob.to_vec()))
        .collect();
    Ok(())
}

/// The cached result of the step that made `body`, which the Python app shows in
/// preference to the body's own blob (`project.py` `_cached_output`): it shares
/// geometry with the rest of the cache, and its tolerances can differ from the blob's.
///
/// ponytail: trusts the `current` flag saved with the body. The Python app also
/// recomputes the step's signature; that comes with the core port (phase 3).
fn cached_output(
    model: &Value,
    body: &Value,
    entry: &mut impl FnMut(&str) -> Option<Vec<u8>>,
) -> Option<(Vec<u8>, String, Option<[f64; 12]>)> {
    let id = body.get("id")?.as_str()?;
    let saved = model.get("geometry")?.get(id)?;
    if saved.get("current") != Some(&Value::Bool(true)) {
        return None;
    }
    let feature_id = saved.get("feature_id")?.as_str()?;
    let cached = model
        .get("cache")?
        .get("entries")?
        .get(saved.get("signature")?.as_str()?)?;
    if cached.get("version")?.as_str()? != CACHE_VERSION
        || cached.get("feature_id")?.as_str()? != feature_id
    {
        return None;
    }
    let feature = model
        .get("features")?
        .as_array()?
        .iter()
        .find(|f| f.get("id").and_then(Value::as_str) == Some(feature_id))?;
    let owned: Vec<&str> = feature
        .get("output_ids")?
        .as_object()?
        .values()
        .filter_map(Value::as_str)
        .collect();
    let outputs = cached.get("outputs")?.as_object()?;
    // As the Python store: every output must decode and belong to the step, or none is used.
    let mut found = None;
    for (oid, output) in outputs {
        if !owned.contains(&oid.as_str()) {
            return None;
        }
        match output.get("kind")?.as_str()? {
            kind @ ("brep" | "located") => {
                let sha = output.get("sha256")?.as_str()?;
                let blob = entry(&format!("geometry/{sha}.brep"))
                    .filter(|b| format!("{:x}", Sha256::digest(b)) == sha)?;
                let location = if kind == "located" {
                    let values: Vec<f64> = output
                        .get("location")?
                        .as_array()?
                        .iter()
                        .filter_map(Value::as_f64)
                        .collect();
                    Some(<[f64; 12]>::try_from(values).ok()?)
                } else {
                    None
                };
                if oid == id {
                    found = Some((blob, sha.to_string(), location));
                }
            }
            "sketch" | "ConstructionPlane" | "ConstructionAxis" | "ConstructionPoint" => {}
            _ => return None,
        }
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    const EMBEDDED_IMPORTS: &[u8] =
        include_bytes!("../../../app/tests/e2e/fixtures/features.scad3");

    fn archive(model: &str, entries: &[(&str, &[u8])]) -> Vec<u8> {
        let mut out = zip::ZipWriter::new(Cursor::new(Vec::new()));
        let options = zip::write::SimpleFileOptions::default();
        out.start_file("model.json", options).unwrap();
        out.write_all(model.as_bytes()).unwrap();
        for (name, bytes) in entries {
            out.start_file(*name, options).unwrap();
            out.write_all(bytes).unwrap();
        }
        out.finish().unwrap().into_inner()
    }

    #[test]
    fn reads_every_body_by_sha_and_notes_what_it_cannot() {
        let blob = b"a brep";
        let sha = format!("{:x}", Sha256::digest(blob));
        let wrong = "b".repeat(64);
        let model = format!(
            r#"{{"format": 4, "bodies": [{{"id": "1", "name": "Box", "visible": true}},
                {{"id": "2", "name": "Hexagon", "visible": false}}, {{"id": "3", "name": "Lid"}}],
                "geometry": {{"1": {{"sha256": "{sha}"}}, "2": {{"sha256": "{sha}"}}, "3": {{"sha256": "{wrong}"}}}}}}"#
        );
        let project = read(&archive(&model, &[(&format!("geometry/{sha}.brep"), blob)])).unwrap();
        let shown: Vec<_> = project
            .bodies
            .iter()
            .map(|b| (b.name.as_str(), b.visible, b.brep.is_some()))
            .collect();
        assert_eq!(
            shown,
            [
                ("Box", true, true),
                ("Hexagon", false, true),
                ("Lid", true, false)
            ]
        );
        assert_eq!(project.notes, ["\"Lid\" could not be read from this file."]);
    }

    #[test]
    fn a_format_1_file_is_read_by_body_name() {
        assert_eq!(legacy_entry("Lid / top"), "bodies/Lid___top.brep");
        assert_eq!(legacy_entry(""), "bodies/body.brep");
        let model = r#"{"bodies": [{"name": "a b"}, {"name": "a_b"}, {"name": "c"}]}"#;
        let project = read(&archive(
            model,
            &[("bodies/c.brep", b"c"), ("bodies/a_b.brep", b"x")],
        ))
        .unwrap();
        let read: Vec<_> = project.bodies.iter().map(|b| b.brep.is_some()).collect();
        assert_eq!(read, [false, false, true]);
        assert_eq!(project.notes.len(), 2);
    }

    #[test]
    fn opening_a_partial_legacy_archive_rebuilds_missing_bodies_and_saves_them() {
        let model = json!({
            "format": 1,
            "features": [
                {"id":"box", "type":"box", "name":"Box", "inputs":{"width":"10 mm", "depth":"10 mm", "height":"10 mm"}},
                {"id":"lid", "type":"box", "name":"Lid", "inputs":{"width":"5 mm", "depth":"5 mm", "height":"4 mm"}},
            ],
            "bodies": [{"name":"Box"}, {"name":"Lid", "visible":false}]
        });
        let box_shape = occt::make_box(10.0, 10.0, 10.0).unwrap();
        let brep = occt::write_brep(&box_shape).unwrap();
        let original = archive(&model.to_string(), &[("bodies/Box.brep", &brep)]);
        let raw = read_saved(&original).unwrap();
        assert!(raw.bodies[1].brep.is_none());
        let opened = read(&original).unwrap();
        assert_eq!(opened.bodies[1].name, "Lid");
        assert_eq!(opened.bodies[1].id, raw.bodies[1].id);
        assert!(!opened.bodies[1].visible);
        assert!(opened.notes.is_empty());
        let saved = crate::document::Model::open(&opened)
            .save(&original)
            .unwrap();
        let reopened = read_saved(&saved).unwrap();
        for project in [&opened, &reopened] {
            let lid = occt::read_brep(project.bodies[1].brep.as_deref().unwrap()).unwrap();
            assert!((occt::volume(&lid) - 100.0).abs() < 1.0e-6);
        }
    }

    #[test]
    fn reopens_an_empty_project_with_redo_history() {
        let shape = occt::make_box(40.0, 30.0, 20.0).unwrap();
        let mut model = crate::document::Model::new_box(occt::write_brep(&shape).unwrap()).unwrap();
        model.undo().unwrap();
        let project = read(&model.save(&[]).unwrap()).unwrap();
        assert!(project.bodies.is_empty());
        assert!(crate::document::Model::open(&project).can_redo());
        assert_eq!(read(&archive("null", &[])).err().as_deref(), Some(DAMAGED));
    }

    #[test]
    fn embedded_import_rebuilds_its_archive_asset_at_the_edited_offset() {
        let opened = read(EMBEDDED_IMPORTS).unwrap();
        let original = opened
            .bodies
            .iter()
            .find(|body| body.name == "Plate")
            .unwrap();
        let shape = occt::read_brep(original.brep.as_ref().unwrap()).unwrap();
        let bounds = occt::bounds(&shape).unwrap();
        let mut model = crate::document::Model::open(&opened);
        model.payload["features"][0]["inputs"]["dx"] = json!("2 cm");
        let rerun = model.payload["features"]
            .as_array()
            .unwrap()
            .iter()
            .map(|feature| feature["id"].as_str().unwrap().to_string())
            .collect();

        let report =
            crate::rebuild::rebuild(&mut model, EMBEDDED_IMPORTS, &Default::default(), &rerun)
                .unwrap();

        assert!(report.failures.is_empty(), "{:?}", report.failures);
        assert_eq!(report.rebuilt.len(), 2);
        let saved = model.save(EMBEDDED_IMPORTS).unwrap();
        let reopened = read(&saved).unwrap();
        let plate = reopened
            .bodies
            .iter()
            .find(|body| body.name == "Plate")
            .unwrap();
        let placed = occt::read_brep(plate.brep.as_ref().unwrap()).unwrap();
        let moved = occt::bounds(&placed).unwrap();
        assert!((occt::volume(&placed) - occt::volume(&shape)).abs() < 1e-6);
        for (axis, (old, new)) in bounds.into_iter().zip(moved).enumerate() {
            let offset = if axis % 3 == 0 { 20.0 } else { 0.0 };
            assert!((new - old - offset).abs() < 1e-6);
        }
    }

    #[test]
    fn a_missing_embedded_asset_reports_failure_and_keeps_the_cached_body() {
        let opened = read(EMBEDDED_IMPORTS).unwrap();
        let mut model = crate::document::Model::open(&opened);
        let fid = model.payload["features"][0]["id"]
            .as_str()
            .unwrap()
            .to_string();
        model.payload["features"][0]["inputs"]["asset"] = json!("0".repeat(64));
        let report = crate::rebuild::rebuild(
            &mut model,
            EMBEDDED_IMPORTS,
            &Default::default(),
            &[fid.clone()].into(),
        )
        .unwrap();

        assert!(
            report.failures[&fid]
                .contains("The embedded geometry for 'Imported solid' is missing.")
        );
        let plate = report
            .bodies
            .iter()
            .find(|body| body.name == "Plate")
            .unwrap();
        let shape = plate.shape.as_ref().unwrap();
        assert!((occt::volume(shape) - 71016.32).abs() < 0.01);
    }

    #[test]
    fn the_cache_version_is_the_python_apps() {
        let python = include_str!("../../../simplecad/core/result_store.py");
        assert!(python.contains(&format!("CACHE_VERSION = \"{CACHE_VERSION}\"")));
    }

    #[test]
    fn a_current_body_comes_from_its_steps_cached_output() {
        let (own, cached) = (b"own blob".as_slice(), b"cached".as_slice());
        let (own_sha, cached_sha) = (
            format!("{:x}", Sha256::digest(own)),
            format!("{:x}", Sha256::digest(cached)),
        );
        let model = format!(
            r#"{{"format": 4, "bodies": [{{"id": "b1", "name": "Box"}}],
                "features": [{{"id": "f1", "output_ids": {{"Box": "b1"}}}}],
                "geometry": {{"b1": {{"sha256": "{own_sha}", "feature_id": "f1", "signature": "s1", "current": true}}}},
                "cache": {{"entries": {{"s1": {{"version": "{CACHE_VERSION}", "feature_id": "f1",
                    "outputs": {{"b1": {{"kind": "located", "sha256": "{cached_sha}",
                        "location": [1, 0, 0, 5, 0, 1, 0, 0, 0, 0, 1, 0]}}}}}}}}}}}}"#
        );
        let files = [
            (format!("geometry/{own_sha}.brep"), own),
            (format!("geometry/{cached_sha}.brep"), cached),
        ];
        let entries: Vec<(&str, &[u8])> = files.iter().map(|(n, b)| (n.as_str(), *b)).collect();
        let body = &read(&archive(&model, &entries)).unwrap().bodies[0];
        assert_eq!(body.brep.as_deref(), Some(cached));
        assert_eq!(body.location.unwrap()[3], 5.0);
        // Not current: the body's own blob.
        let stale = model.replace(r#""current": true"#, r#""current": false"#);
        let body = &read(&archive(&stale, &entries)).unwrap().bodies[0];
        assert_eq!(body.brep.as_deref(), Some(own));
    }

    #[test]
    fn a_newer_or_damaged_file_is_refused_in_words() {
        let newer = read(&archive(r#"{"format": 5, "bodies": []}"#, &[]))
            .err()
            .unwrap();
        assert!(newer.contains("newer SimpleCAD"));
        assert!(
            read(b"not a zip")
                .err()
                .unwrap()
                .contains("not a SimpleCAD project")
        );
        assert!(read(&archive("{", &[])).err().unwrap().contains("damaged"));
    }
}

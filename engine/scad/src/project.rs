//! Reading `.scad3` projects, the format of the Python app (`simplecad/core/project.py`):
//! a zip holding `model.json` and each body's B-Rep. Format 1 stores a body at
//! `bodies/<name>.brep`; formats 2–4 at `geometry/<sha256>.brep`, named in `model.json`.

use serde_json::Value;
use sha2::{Digest, Sha256};
use std::io::{Cursor, Read};

/// The newest format this build reads; the Python app refuses newer files the same way.
pub const FORMAT_VERSION: i64 = 4;
/// `CACHE_VERSION` in `simplecad/core/result_store.py`; a test keeps them equal.
pub const CACHE_VERSION: &str = "simplecad-feature-cache-2-occt-7.9";

pub struct SavedBody {
    pub name: String,
    pub visible: bool,
    /// The saved B-Rep; `None` when the file has none for this body or it is damaged.
    pub brep: Option<Vec<u8>>,
    /// Where the B-Rep is placed: the 3×4 matrix of a cached "located" output.
    pub location: Option<[f64; 12]>,
}

pub struct Project {
    pub model: Value,
    pub bodies: Vec<SavedBody>,
    /// What could not be read, in words.
    pub notes: Vec<String>,
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
    let model: Value = serde_json::from_slice(&raw).map_err(|_| DAMAGED.to_string())?;
    let format = match model.get("format") {
        None => 1,
        Some(v) => v.as_i64().filter(|f| *f >= 1).ok_or(DAMAGED)?,
    };
    if format > FORMAT_VERSION {
        return Err(
            "This project was saved by a newer SimpleCAD. Update SimpleCAD to open it.".into(),
        );
    }
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
        let visible = body.get("visible") != Some(&Value::Bool(false));
        if format >= 2
            && let Some((blob, location)) = cached_output(&model, body, &mut entry)
        {
            bodies.push(SavedBody {
                name: name.to_string(),
                visible,
                brep: Some(blob),
                location,
            });
            continue;
        }
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
            match sha {
                None => {
                    notes.push(format!(
                        "\"{name}\" has no saved geometry; open it in SimpleCAD 0.4 to rebuild it."
                    ));
                    None
                }
                Some(sha) => entry(&format!("geometry/{sha}.brep"))
                    .filter(|blob| format!("{:x}", Sha256::digest(blob)) == sha)
                    .or_else(|| {
                        notes.push(format!("\"{name}\" could not be read from this file."));
                        None
                    }),
            }
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
            name: name.to_string(),
            visible,
            brep,
            location: None,
        });
    }
    if bodies.iter().all(|b| b.brep.is_none()) {
        return Err(
            "This project has no saved bodies this build can show. Open it in SimpleCAD 0.4."
                .into(),
        );
    }
    Ok(Project {
        model,
        bodies,
        notes,
    })
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
) -> Option<(Vec<u8>, Option<[f64; 12]>)> {
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
                    found = Some((blob, location));
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

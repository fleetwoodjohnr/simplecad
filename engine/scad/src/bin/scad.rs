//! `scad dump <file.scad3>`: each body's volume, bounding box and face count as
//! JSON, for `scripts/compare_projects.py --rust` to hold against the Python app.
//! Raw numbers: the comparison rounds both sides the same way.

use serde_json::{Map, Value, json};

fn signature(brep: &[u8], location: Option<&[f64; 12]>) -> Result<Value, String> {
    let mut shape = occt::read_brep(brep).map_err(|e| e.what().to_string())?;
    if let Some(matrix) = location {
        shape = occt::located(&shape, matrix).map_err(|e| e.what().to_string())?;
    }
    let bounds = occt::bounds(&shape).map_err(|e| e.what().to_string())?;
    Ok(json!({
        "volume": occt::volume(&shape).abs(),
        "box": bounds,
        "faces": occt::face_occurrences(&shape),
    }))
}

/// Every face's fingerprint, as `naming.fingerprint(face, "face").to_dict()` writes it.
fn face_fingerprints(brep: &[u8], location: Option<&[f64; 12]>) -> Result<Value, String> {
    let mut shape = occt::read_brep(brep).map_err(|e| e.what().to_string())?;
    if let Some(matrix) = location {
        shape = occt::located(&shape, matrix).map_err(|e| e.what().to_string())?;
    }
    let count = occt::faces(&shape).map_err(|e| e.what().to_string())?.len() as u32;
    (0..count)
        .map(|i| {
            let f = occt::face_fingerprint(&shape, i).map_err(|e| e.what().to_string())?;
            Ok(json!({
                "kind": "face", "geometry": f.geometry, "measure": f.measure, "center": f.center,
                "direction": if f.has_direction { json!(f.direction) } else { Value::Null },
                "radius": if f.has_radius { json!(f.radius) } else { Value::Null },
                "neighbours": [],
            }))
        })
        .collect::<Result<Vec<_>, String>>()
        .map(Value::Array)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let [_, command, path] = args.as_slice() else {
        eprintln!("usage: scad dump <file.scad3>");
        std::process::exit(2);
    };
    if command != "dump" {
        eprintln!("unknown command {command:?}; the only one is dump");
        std::process::exit(2);
    }
    let result = std::fs::read(path)
        .map_err(|e| e.to_string())
        .and_then(|data| scad::project::read(&data))
        .and_then(|project| {
            let mut loaded = Map::new();
            for body in &project.bodies {
                let value = match &body.brep {
                    Some(brep) => signature(brep, body.location.as_ref())?,
                    None => Value::Null,
                };
                loaded.insert(body.name.clone(), value);
            }
            let mut fingerprints = Map::new();
            for body in &project.bodies {
                if let Some(brep) = &body.brep {
                    fingerprints.insert(
                        body.name.clone(),
                        face_fingerprints(brep, body.location.as_ref())?,
                    );
                }
            }
            let mut stages = json!({ "loaded": loaded, "fingerprints": fingerprints });
            // Format 1 files get their output ids on load; those have no saved signatures to match.
            if project
                .model
                .get("format")
                .and_then(Value::as_i64)
                .unwrap_or(1)
                >= 2
            {
                stages["signatures"] = json!(scad::signatures::signatures(&project.model));
            }
            Ok(stages)
        });
    match result {
        Ok(stages) => println!("{stages}"),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}

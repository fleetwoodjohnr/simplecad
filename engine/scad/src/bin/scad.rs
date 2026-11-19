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
            Ok(json!({ "loaded": loaded }))
        });
    match result {
        Ok(stages) => println!("{stages}"),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}

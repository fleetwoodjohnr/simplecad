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

/// `scad pull <in> <body> <face> <distance> <out>`: one push/pull, saved as the
/// Pull tool would, for checking that the Python app takes it as its own.
fn pull(args: &[String]) -> Result<(), String> {
    let [input, body, face, distance, output] = args else {
        return Err(
            "usage: scad pull <in.scad3> <body> <face index> <distance> <out.scad3>".into(),
        );
    };
    let err = |e: occt::Exception| e.what().to_string();
    let original = std::fs::read(input).map_err(|e| e.to_string())?;
    let project = scad::project::read(&original)?;
    let saved = project
        .bodies
        .iter()
        .find(|b| &b.name == body)
        .ok_or("no such body")?;
    let mut shape =
        occt::read_brep(saved.brep.as_ref().ok_or("the body has no geometry")?).map_err(err)?;
    if let Some(m) = &saved.location {
        shape = occt::located(&shape, m).map_err(err)?;
    }
    let face: u32 = face.parse().map_err(|_| "the face is a number")?;
    let amount: f64 = distance.parse().map_err(|_| "the distance is a number")?;
    let f = occt::face_fingerprint(&shape, face).map_err(err)?;
    let fingerprint = json!({
        "kind": "face", "geometry": f.geometry, "measure": f.measure, "center": f.center,
        "direction": if f.has_direction { json!(f.direction) } else { Value::Null },
        "radius": if f.has_radius { json!(f.radius) } else { Value::Null },
        "neighbours": [],
    });
    let pulled = occt::push_pull(&shape, face, amount).map_err(err)?;
    let mut model = scad::document::Model::open(&project);
    let reference = model.face_ref(body, face, fingerprint);
    model.push_pull(
        body,
        reference,
        distance,
        occt::write_brep(&pulled).map_err(err)?,
    )?;
    std::fs::write(output, model.save(&original)?).map_err(|e| e.to_string())?;
    println!("{}", json!({ "volume": occt::volume(&pulled) }));
    Ok(())
}

/// `scad save <in> <out>`: open and save, no edit, as Save As would.
fn save(args: &[String]) -> Result<(), String> {
    let [input, output] = args else {
        return Err("usage: scad save <in.scad3> <out.scad3>".into());
    };
    let original = std::fs::read(input).map_err(|e| e.to_string())?;
    let model = scad::document::Model::open(&scad::project::read(&original)?);
    std::fs::write(output, model.save(&original)?).map_err(|e| e.to_string())
}

type Command = fn(&[String]) -> Result<(), String>;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let command: Option<Command> = match args.get(1).map(String::as_str) {
        Some("pull") => Some(pull),
        Some("save") => Some(save),
        _ => None,
    };
    if let Some(command) = command {
        if let Err(error) = command(&args[2..]) {
            eprintln!("{error}");
            std::process::exit(1);
        }
        return;
    }
    let [_, command, path] = args.as_slice() else {
        eprintln!(
            "usage: scad dump <file.scad3> | scad pull <in.scad3> <body> <face> <distance> <out.scad3> | scad save <in.scad3> <out.scad3>"
        );
        std::process::exit(2);
    };
    if command != "dump" {
        eprintln!("unknown command {command:?}; use dump, pull or save");
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
            Ok(json!({
                "loaded": loaded,
                "fingerprints": fingerprints,
                "signatures": scad::signatures::signatures(&project.model),
                "document": scad::document::Model::new(project.model).document(),
            }))
        });
    match result {
        Ok(stages) => println!("{stages}"),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}

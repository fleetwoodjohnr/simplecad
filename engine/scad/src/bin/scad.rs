//! `scad dump <file.scad3>`: each body's volume, bounding box and face count as
//! JSON, for `scripts/compare_projects.py --rust` to hold against the Python app.
//! Raw numbers: the comparison rounds both sides the same way.

use scad::document::Model;
use scad::project::Project;
use scad::rebuild::{Report, Shape};
use serde_json::{Map, Value, json};
use std::collections::{BTreeSet, HashMap};
use std::rc::Rc;

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

fn saved_signatures(project: &Project) -> Result<Map<String, Value>, String> {
    project
        .bodies
        .iter()
        .map(|body| {
            let geometry = match &body.brep {
                Some(brep) => signature(brep, body.location.as_ref())?,
                None => Value::Null,
            };
            Ok((body.name.clone(), geometry))
        })
        .collect()
}

/// `scad pull <in> <body> <face> <distance>[,<distance>...] <out> [undone]`:
/// push/pull face `face` of `body` by each distance in turn, saved as the Pull
/// tool would with the newest `undone` pulls taken back (kept to redo), for
/// checking that the Python app takes them as its own.
fn pull(args: &[String]) -> Result<(), String> {
    let (input, body, face, distances, output, undone) = match args {
        [i, b, f, d, o] => (i, b, f, d, o, "0"),
        [i, b, f, d, o, u] => (i, b, f, d, o, u.as_str()),
        _ => {
            return Err(
                "usage: scad pull <in.scad3> <body> <face index> <distance>[,<distance>...] <out.scad3> [undone]"
                    .into(),
            );
        }
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
    let mut face: u32 = face.parse().map_err(|_| "the face is a number")?;
    let undone: usize = undone.parse().map_err(|_| "undone is a number")?;
    let mut model = scad::document::Model::open(&project);
    let mut volumes = Vec::new();
    for distance in distances.split(',') {
        let amount: f64 = distance.parse().map_err(|_| "the distance is a number")?;
        let f = occt::face_fingerprint(&shape, face).map_err(err)?;
        let fingerprint = json!({
            "kind": "face", "geometry": f.geometry, "measure": f.measure, "center": f.center,
            "direction": if f.has_direction { json!(f.direction) } else { Value::Null },
            "radius": if f.has_radius { json!(f.radius) } else { Value::Null },
            "neighbours": [],
        });
        shape = occt::push_pull(&shape, face, amount).map_err(err)?;
        let reference = model.face_ref(body, face, fingerprint);
        model.push_pull(
            body,
            reference,
            distance,
            occt::write_brep(&shape).map_err(err)?,
        )?;
        volumes.push(occt::volume(&shape));
        // The pulled face, moved along its normal, in the new shape's numbering.
        let moved: Vec<f64> = (0..3)
            .map(|k| f.center[k] + f.direction[k] * amount)
            .collect();
        let count = occt::faces(&shape).map_err(err)?.len() as u32;
        face = (0..count)
            .filter_map(|i| Some((i, occt::face_fingerprint(&shape, i).ok()?)))
            .filter(|(_, g)| {
                g.has_direction && (0..3).all(|k| (g.direction[k] - f.direction[k]).abs() < 1e-9)
            })
            .min_by(|(_, a), (_, b)| {
                let d = |g: &occt::FaceFingerprint| {
                    (0..3)
                        .map(|k| (g.center[k] - moved[k]).powi(2))
                        .sum::<f64>()
                };
                d(a).total_cmp(&d(b))
            })
            .map_or(face, |(i, _)| i);
    }
    for _ in 0..undone {
        model.undo()?;
    }
    std::fs::write(output, model.save(&original)?).map_err(|e| e.to_string())?;
    println!("{}", json!({ "volumes": volumes }));
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

/// `scad rebuild <file> [--from-recipe]`: the bodies after the Python app's rebuild
/// stages: `cached_rebuild` (its results adopted, saved ones used) or, from the
/// recipe, `recipe_rebuild` (every step run). With the step types that had to run
/// and are not ported yet, whose bodies then keep what they had.
fn rebuild(args: &[String]) -> Result<(), String> {
    let (path, recipe) = match args {
        [path] => (path, false),
        [path, flag] if flag == "--from-recipe" => (path, true),
        _ => return Err("usage: scad rebuild <file.scad3> [--from-recipe]".into()),
    };
    let original = std::fs::read(path).map_err(|e| e.to_string())?;
    let project = scad::project::read(&original)?;
    let mut model = scad::document::Model::open(&project);
    let (shown, stale) = if recipe {
        let stale = model.payload["features"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|f| f["id"].as_str().map(String::from))
            .collect();
        (HashMap::new(), stale)
    } else {
        model.adopt_baseline(&project.bodies);
        (saved_shapes(&project)?, BTreeSet::new())
    };
    let report = scad::rebuild::rebuild(&mut model, &original, &shown, &stale)?;
    let bodies = report_signatures(&report)?;
    let unported = unported_steps(&model, &report);
    let stage = if recipe { "recipe" } else { "cached" };
    println!(
        "{}",
        json!({
            format!("{stage}_rebuild"): bodies,
            format!("{stage}_failed"): report.failures.len(),
            format!("{stage}_diagnostics"): failure_states(&model, &report),
            "unported": unported,
        })
    );
    Ok(())
}

fn unported_steps<'a>(model: &'a Model, report: &Report) -> BTreeSet<&'a str> {
    report
        .failures
        .iter()
        .filter(|(_, message)| message.contains("cannot build it yet"))
        .filter_map(|(fid, _)| {
            model.payload["features"]
                .as_array()?
                .iter()
                .find(|f| f["id"] == json!(fid))?["type"]
                .as_str()
        })
        .collect()
}

fn failure_states(model: &Model, report: &Report) -> Map<String, Value> {
    report
        .failures
        .keys()
        .map(|id| {
            (
                id.clone(),
                model.payload["diagnostics"][id]["state"].clone(),
            )
        })
        .collect()
}

fn saved_shapes(project: &Project) -> Result<HashMap<String, Shape>, String> {
    let ids: HashMap<&str, &str> = project.model["bodies"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|body| Some((body["name"].as_str()?, body["id"].as_str()?)))
        .collect();
    let mut shown = HashMap::new();
    for body in &project.bodies {
        let (Some(brep), Some(id)) = (&body.brep, ids.get(body.name.as_str())) else {
            continue;
        };
        let mut shape = occt::read_brep(brep).map_err(|e| e.what().to_string())?;
        if let Some(matrix) = &body.location {
            shape = occt::located(&shape, matrix).map_err(|e| e.what().to_string())?;
        }
        shown.insert((*id).into(), Rc::new(shape));
    }
    Ok(shown)
}

fn report_signatures(report: &Report) -> Result<Map<String, Value>, String> {
    report
        .bodies
        .iter()
        .map(|body| {
            let value = match &body.shape {
                Some(shape) => json!({
                    "volume": occt::volume(shape).abs(),
                    "box": occt::bounds(shape).map_err(|e| e.what().to_string())?,
                    "faces": occt::face_occurrences(shape),
                }),
                None => Value::Null,
            };
            Ok((body.name.clone(), value))
        })
        .collect()
}

/// Compare saved history's fully undone and redone states with Python's.
fn history(args: &[String]) -> Result<(), String> {
    let [path] = args else {
        return Err("usage: scad history <file.scad3>".into());
    };
    let original = std::fs::read(path).map_err(|e| e.to_string())?;
    let project = scad::project::read(&original)?;
    let mut model = Model::open(&project);
    let mut stale = BTreeSet::new();
    let shown = saved_shapes(&project)?;
    let initial = scad::rebuild::rebuild(&mut model, &original, &shown, &stale)?;
    let shapes = |report: &Report| -> HashMap<_, _> {
        report
            .bodies
            .iter()
            .filter_map(|body| Some((body.id.clone(), body.shape.clone()?)))
            .collect()
    };
    let mut undo_steps = 0;
    while model.can_undo() {
        stale.extend(model.move_history(false)?);
        undo_steps += 1;
    }
    let undone = scad::rebuild::rebuild(&mut model, &original, &shapes(&initial), &stale)?;
    let undo_diagnostics = failure_states(&model, &undone);
    let undo_unported: BTreeSet<_> = unported_steps(&model, &undone)
        .into_iter()
        .map(String::from)
        .collect();
    let mut redo_steps = 0;
    stale.clear();
    while model.can_redo() {
        stale.extend(model.move_history(true)?);
        redo_steps += 1;
    }
    let redone = scad::rebuild::rebuild(&mut model, &original, &shapes(&undone), &stale)?;
    let unported: BTreeSet<_> = undo_unported
        .into_iter()
        .chain(
            unported_steps(&model, &redone)
                .into_iter()
                .map(String::from),
        )
        .collect();
    println!(
        "{}",
        json!({
            "undo_all": report_signatures(&undone)?,
            "redo_all": report_signatures(&redone)?,
            "undo_steps": undo_steps,
            "redo_steps": redo_steps,
        "undo_diagnostics": undo_diagnostics,
        "redo_diagnostics": failure_states(&model, &redone),
            "unported": unported,
        })
    );
    Ok(())
}

/// `scad steps <file> <type>`: each step of that type solved again on its saved
/// inputs (see `rebuild::check_steps`), for `compare_projects.py --steps`.
fn steps(args: &[String]) -> Result<(), String> {
    let [path, kind] = args else {
        return Err("usage: scad steps <file.scad3> <step type>".into());
    };
    let original = std::fs::read(path).map_err(|e| e.to_string())?;
    let project = scad::project::read(&original)?;
    let mut model = scad::document::Model::open(&project);
    model.adopt_baseline(&project.bodies);
    let found = scad::rebuild::check_steps(&model, &original, kind)?;
    println!("{}", json!(found));
    Ok(())
}

type Command = fn(&[String]) -> Result<(), String>;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let command: Option<Command> = match args.get(1).map(String::as_str) {
        Some("pull") => Some(pull),
        Some("save") => Some(save),
        Some("rebuild") => Some(rebuild),
        Some("history") => Some(history),
        Some("steps") => Some(steps),
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
            "usage: scad dump <file.scad3> | scad pull <in.scad3> <body> <face> <distance> <out.scad3> | scad save <in.scad3> <out.scad3> | scad rebuild <file.scad3> [--from-recipe]"
        );
        std::process::exit(2);
    };
    if command != "dump" {
        eprintln!("unknown command {command:?}; use dump, pull or save");
        std::process::exit(2);
    }
    let result = std::fs::read(path)
        .map_err(|e| e.to_string())
        .and_then(|data| {
            let project = scad::project::read_saved(&data)?;
            let displayed = scad::project::read(&data)?;
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
                "loaded": saved_signatures(&project)?,
                "displayed": saved_signatures(&displayed)?,
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

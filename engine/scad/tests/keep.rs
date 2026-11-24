//! Keep a step that lost its input as it last built, on a project SimpleCAD 0.4.4 made and saved: A stacked on B's
//! top, turned 30°, then B deleted; C cut by D, then D deleted (`tests/data/keep`, `make_keep_fixtures.py`).

use scad::document::Model;
use scad::rebuild::Shape;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::rc::Rc;

const FIXTURE: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/keep/broken");

fn last_good() -> Value {
    serde_json::from_slice::<Value>(&std::fs::read(format!("{FIXTURE}.json")).unwrap()).unwrap()["last_good"].clone()
}

/// The project as the window opens it: its model, and each body as the file saved it, by id.
fn open(data: &[u8]) -> (Model, HashMap<String, Shape>) {
    let project = scad::project::read(data).unwrap();
    let shown = project
        .bodies
        .iter()
        .filter_map(|body| {
            let shape = occt::read_brep(body.brep.as_ref()?).ok()?;
            let shape = match &body.location {
                Some(matrix) => occt::located(&shape, matrix).ok()?,
                None => shape,
            };
            Some((body.id.clone(), Rc::new(shape)))
        })
        .collect();
    (Model::open(&project), shown)
}

fn id(model: &Model, name: &str) -> String {
    model.payload["bodies"]
        .as_array()
        .unwrap()
        .iter()
        .find(|b| b["name"] == name)
        .unwrap()["id"]
        .as_str()
        .unwrap()
        .into()
}

fn step(model: &Model, name: &str) -> Value {
    model.payload["features"]
        .as_array()
        .unwrap()
        .iter()
        .find(|f| f["name"] == name)
        .unwrap()
        .clone()
}

fn bounds(shape: &occt::Shape) -> Vec<f64> {
    occt::bounds(shape).unwrap()
}

fn shapes(report: &scad::rebuild::Report) -> HashMap<String, Shape> {
    report
        .bodies
        .iter()
        .filter_map(|body| Some((body.id.clone(), body.shape.clone()?)))
        .collect()
}

#[test]
fn a_blocked_part_names_the_step_to_keep_or_repair() {
    let original = std::fs::read(format!("{FIXTURE}.scad3")).unwrap();
    let (model, _) = open(&original);
    let steps: HashMap<String, (String, Option<String>)> = model
        .body_steps()
        .into_iter()
        .map(|(name, producer, blocked)| (name, (producer, blocked)))
        .collect();
    let align = step(&model, "Align")["id"].as_str().unwrap().to_string();
    let combine = step(&model, "Combine")["id"].as_str().unwrap().to_string();
    assert_eq!(steps["A"], (align.clone(), Some(align)));
    assert_eq!(steps["C"], (combine.clone(), Some(combine)));
    assert_eq!(
        model.editable("A").unwrap_err(),
        "\"A\" can't change until Align, marked in History, is kept as it is or repaired."
    );
}

#[test]
fn a_kept_align_is_a_move_to_where_it_was_and_the_part_moves_on_from_there() {
    let original = std::fs::read(format!("{FIXTURE}.scad3")).unwrap();
    let (mut model, shown) = open(&original);
    let a = id(&model, "A");
    let align = step(&model, "Align");
    let report = model
        .keep_last_good(align["id"].as_str().unwrap(), &original, &shown)
        .unwrap();
    assert!(
        !report.failures.contains_key(align["id"].as_str().unwrap()),
        "{:?}",
        report.failures
    );
    let kept = step(&model, "Align (kept)");
    assert_eq!(
        (kept["id"].clone(), kept["type"].clone()),
        (align["id"].clone(), json!("move"))
    );
    // A turned 30° about a vertical axis, so the Move turns it too.
    assert!(
        kept["inputs"]["axis"][2].as_f64().unwrap().abs() > 0.999,
        "{}",
        kept["inputs"]
    );
    assert!(
        (kept["inputs"]["angle"].as_f64().unwrap().abs() - 30.0).abs() < 1e-6,
        "{}",
        kept["inputs"]
    );
    let after = shapes(&report);
    let expected: Vec<f64> = serde_json::from_value(last_good()["A_bounds"].clone()).unwrap();
    for (got, want) in bounds(&after[&a]).iter().zip(&expected) {
        assert!(
            (got - want).abs() < 1e-6,
            "{:?} against {expected:?}",
            bounds(&after[&a])
        );
    }
    assert!(model.editable("A").is_ok());
    assert_eq!(
        model
            .body_steps()
            .into_iter()
            .find(|(name, ..)| name == "A")
            .unwrap()
            .2,
        None
    );

    // The part moves on from there, and the save rebuilds to the same in a fresh open.
    let moved = scad::rebuild::create_transform(
        &mut model,
        "A",
        &after[&a],
        "Move",
        json!({ "dx": 5.0, "dy": 0.0, "dz": 0.0 }),
    )
    .unwrap();
    assert!((bounds(&moved)[0] - expected[0] - 5.0).abs() < 1e-6);
    let saved = model.save(&original).unwrap();
    let (mut reopened, _) = open(&saved);
    let stale = all(&reopened);
    let rebuilt = scad::rebuild::rebuild(&mut reopened, &saved, &HashMap::new(), &stale).unwrap();
    assert!(
        !rebuilt.failures.contains_key(align["id"].as_str().unwrap()),
        "{:?}",
        rebuilt.failures
    );
    let moved_again = shapes(&rebuilt)[&a].clone();
    for (got, want) in bounds(&moved_again).iter().zip(bounds(&moved)) {
        assert!((got - want).abs() < 1e-6);
    }
}

#[test]
fn a_kept_combine_is_its_last_good_shape_and_undo_brings_the_step_back() {
    let original = std::fs::read(format!("{FIXTURE}.scad3")).unwrap();
    let (mut model, shown) = open(&original);
    let c = id(&model, "C");
    let combine = step(&model, "Combine");
    let report = model
        .keep_last_good(combine["id"].as_str().unwrap(), &original, &shown)
        .unwrap();
    let kept = step(&model, "Combine (kept)");
    assert_eq!(kept["type"], "embedded_import");
    let volume = occt::volume(&shapes(&report)[&c]);
    assert!(
        (volume - last_good()["C_volume"].as_f64().unwrap()).abs() < 1e-6,
        "{volume}"
    );
    assert!(model.editable("C").is_ok());
    assert_eq!(
        model.history()["undo"].as_array().unwrap().last().unwrap()["label"],
        "Keep as it is"
    );

    // Rebuilt from the recipe after a save, the embedded shape is the same part.
    let saved = model.save(&original).unwrap();
    let (mut reopened, _) = open(&saved);
    let stale = all(&reopened);
    let rebuilt = scad::rebuild::rebuild(&mut reopened, &saved, &HashMap::new(), &stale).unwrap();
    assert!((occt::volume(&shapes(&rebuilt)[&c]) - volume).abs() < 1e-6);

    model.move_history(false).unwrap();
    assert_eq!(step(&model, "Combine")["type"], "boolean");
}

#[test]
fn a_step_that_builds_has_nothing_to_keep() {
    let original = std::fs::read(format!("{FIXTURE}.scad3")).unwrap();
    let (mut model, shown) = open(&original);
    let first = model.payload["features"][0]["id"]
        .as_str()
        .unwrap()
        .to_string();
    let error = model
        .keep_last_good(&first, &original, &shown)
        .err()
        .unwrap();
    assert!(error.contains("nothing to keep"), "{error}");
}

fn all(model: &Model) -> std::collections::BTreeSet<String> {
    model.payload["features"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|f| f["id"].as_str().map(String::from))
        .collect()
}

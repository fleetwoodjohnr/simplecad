//! Face patterns built and saved by SimpleCAD 0.4.4 (`build/rust-verification/make_arrange_fixtures.py`), rebuilt
//! from the recipe alone: every body lands where 0.4.4 put it, and the preview says what 0.4.4's said.

use serde_json::Value;
use std::collections::{BTreeSet, HashMap};

/// One case for each pattern, target and boundary 0.4.4's panel offers. Outlines are meshed from the bodies (a
/// cylinder and a sphere among them, in "curved"), and OCCT 8 meshes them as 7.9 did, so every body lands where 0.4.4
/// put it to well under a nanometre.
const CASES: &[&str] = &[
    "grid",
    "grid_fixed",
    "linear",
    "radial",
    "radial_fixed",
    "staggered",
    "packed",
    "custom",
    "rotated",
    "manual",
    "holes",
    "contact",
    "offset",
    "curved",
    "no_fit",
    "suggest",
    "round",
    "round_grid",
    "bore",
    "plate",
];

#[test]
fn face_patterns_rebuild_from_their_recipes_as_simplecad_044_built_them() {
    for kind in CASES {
        check(kind, 1e-9);
    }
}

fn check(kind: &str, tolerance: f64) {
    let folder = format!("{}/tests/data/arrange", env!("CARGO_MANIFEST_DIR"));
    let expected: Value = serde_json::from_str(
        &std::fs::read_to_string(format!("{folder}/annotations.json")).unwrap(),
    )
    .unwrap();
    let expected = &expected[kind];
    let original = std::fs::read(format!("{folder}/{kind}.scad3")).unwrap();
    let project = scad::project::read(&original).unwrap();
    let mut model = scad::document::Model::open(&project);
    let features = model.payload["features"].as_array_mut().unwrap();
    let step = features.last_mut().unwrap();
    step["inputs"]["preview"] = true.into();
    let id = step["id"].as_str().unwrap().to_string();
    let stale: BTreeSet<String> = features
        .iter()
        .map(|feature| feature["id"].as_str().unwrap().to_string())
        .collect();
    let report = scad::rebuild::rebuild(&mut model, &original, &HashMap::new(), &stale).unwrap();
    assert!(report.failures.is_empty(), "{kind}: {:?}", report.failures);

    let saved: HashMap<_, _> = project
        .bodies
        .iter()
        .map(|body| (body.name.clone(), body))
        .collect();
    assert_eq!(report.bodies.len(), saved.len(), "{kind}");
    for body in &report.bodies {
        let built = body.shape.as_ref().unwrap();
        let mut python = occt::read_brep(saved[&body.name].brep.as_ref().unwrap()).unwrap();
        if let Some(matrix) = &saved[&body.name].location {
            python = occt::located(&python, matrix).unwrap();
        }
        let (ours, theirs) = (occt::volume(built), occt::volume(&python));
        assert!(
            (ours - theirs).abs() < 1e-6 * theirs.abs().max(1.0),
            "{kind} {}: {ours} vs {theirs}",
            body.name
        );
        let (a, b) = (occt::bounds(built).unwrap(), occt::bounds(&python).unwrap());
        let worst = a
            .iter()
            .zip(&b)
            .map(|(x, y)| (x - y).abs())
            .fold(0.0, f64::max);
        assert!(
            worst < tolerance,
            "{kind} {}: {worst:e} off; {a:?} vs {b:?}",
            body.name
        );
        assert_eq!(
            occt::face_occurrences(built),
            occt::face_occurrences(&python),
            "{kind} {}",
            body.name
        );
    }

    // What the step says, and what its preview shows beside it.
    let step = model
        .steps()
        .into_iter()
        .find(|step| step.id == id)
        .unwrap();
    let warnings: Vec<_> = expected["warnings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|w| w.as_str().unwrap())
        .collect();
    let mut message = expected["message"].as_str().unwrap().to_string();
    if !warnings.is_empty() {
        message = format!("{message} {}", warnings.join(" "));
    }
    assert_eq!(step.message.as_deref(), Some(message.as_str()), "{kind}");
    let note = &report.notes[&id];
    for key in ["fits", "summary", "order", "rows", "columns"] {
        assert_eq!(note[key], expected[key], "{kind} {key}");
    }
    for key in ["gap", "radius"] {
        let (ours, theirs) = (note[key].as_f64().unwrap(), expected[key].as_f64().unwrap());
        assert!(
            (ours - theirs).abs() < tolerance.max(1e-9 * theirs.abs()),
            "{kind} {key}: {ours} vs {theirs}"
        );
    }
    let problems = |value: &Value| -> BTreeSet<String> {
        value
            .as_array()
            .unwrap()
            .iter()
            .map(|p| format!("{} {}", p["name"], p["reason"]))
            .collect()
    };
    assert_eq!(
        problems(&note["problems"]),
        problems(&expected["problems"]),
        "{kind}"
    );
    let offers = |value: &Value| -> Vec<(String, Vec<(String, String)>)> {
        value
            .as_array()
            .unwrap()
            .iter()
            .map(|offer| {
                let patch = offer["patch"].as_object().unwrap();
                let fields = patch
                    .iter()
                    .map(|(k, v)| {
                        (
                            k.clone(),
                            v.as_f64()
                                .map_or_else(|| v.to_string(), |n| format!("{n:.9}")),
                        )
                    })
                    .collect();
                (offer["label"].as_str().unwrap().to_string(), fields)
            })
            .collect()
    };
    assert_eq!(
        offers(&note["suggestions"]),
        offers(&expected["suggestions"]),
        "{kind}"
    );
}

//! A model rebuilds as the Python app's `Rebuilder` would: saved results are
//! taken while their signatures match, an edited step and everything after it
//! run again, and a face reference finds its face in the new shape.

use scad::document::Model;
use scad::rebuild::{Shape, rebuild};
use serde_json::json;
use std::collections::{BTreeSet, HashMap};
use std::rc::Rc;

fn top(shape: &occt::Shape) -> (u32, serde_json::Value) {
    let count = occt::faces(shape).unwrap().len() as u32;
    let (i, f) = (0..count)
        .map(|i| (i, occt::face_fingerprint(shape, i).unwrap()))
        .max_by(|a, b| a.1.center[2].total_cmp(&b.1.center[2]))
        .unwrap();
    let print = json!({ "kind": "face", "geometry": f.geometry, "measure": f.measure, "center": f.center,
        "direction": f.direction, "radius": null, "neighbours": [] });
    (i, print)
}

#[test]
fn an_edited_pull_rebuilds_with_the_pulls_after_it() {
    let shape = occt::make_box(40.0, 30.0, 20.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&shape).unwrap()).unwrap();
    let mut current = shape;
    for distance in [5.0, 3.0] {
        let (face, print) = top(&current);
        let reference = model.face_ref("Box", face, print);
        current = occt::push_pull(&current, face, distance).unwrap();
        model
            .push_pull(
                "Box",
                reference,
                &distance.to_string(),
                occt::write_brep(&current).unwrap(),
            )
            .unwrap();
    }
    assert_eq!(occt::volume(&current), 40.0 * 30.0 * 28.0);
    let id = |m: &Model, i: usize| m.payload["features"][i]["id"].as_str().unwrap().to_string();

    // Everything saved: nothing runs.
    let shown: HashMap<String, Shape> = HashMap::from([(
        model.payload["bodies"][0]["id"].as_str().unwrap().into(),
        Rc::new(current),
    )]);
    let report = rebuild(&mut model, &[], &shown, &BTreeSet::new()).unwrap();
    assert!(report.rebuilt.is_empty() && report.failures.is_empty());
    assert_eq!(
        occt::volume(report.bodies[0].shape.as_ref().unwrap()),
        40.0 * 30.0 * 28.0
    );

    // From the recipe: every step runs, the pulls finding their faces again.
    let all: BTreeSet<String> = (0..3).map(|i| id(&model, i)).collect();
    let report = rebuild(&mut model, &[], &HashMap::new(), &all).unwrap();
    assert_eq!(report.rebuilt.len(), 3, "{:?}", report.failures);
    assert_eq!(
        occt::volume(report.bodies[0].shape.as_ref().unwrap()),
        40.0 * 30.0 * 28.0
    );

    // The first pull made 8 mm: it and the pull after it run, on the taller box.
    model.payload["features"][1]["inputs"]["distance"] = json!("8 mm");
    let edited = BTreeSet::from([id(&model, 1), id(&model, 2)]);
    let report = rebuild(&mut model, &[], &HashMap::new(), &edited).unwrap();
    assert_eq!(report.rebuilt, [id(&model, 1), id(&model, 2)]);
    assert_eq!(
        occt::volume(report.bodies[0].shape.as_ref().unwrap()),
        40.0 * 30.0 * 31.0
    );
    let geometry = &model.payload["geometry"][model.payload["bodies"][0]["id"].as_str().unwrap()];
    assert_eq!(geometry["current"], json!(true));
    assert_eq!(
        model.payload["diagnostics"][id(&model, 2)]["state"],
        json!("ok")
    );

    // A step that has to run and is not ported yet fails in words, keeping the body.
    model.payload["features"][2]["type"] = json!("fillet");
    let last = BTreeSet::from([id(&model, 2)]);
    let shown: HashMap<String, Shape> = report
        .bodies
        .iter()
        .map(|b| (b.id.clone(), b.shape.clone().unwrap()))
        .collect();
    let report = rebuild(&mut model, &[], &shown, &last).unwrap();
    assert_eq!(
        occt::volume(report.bodies[0].shape.as_ref().unwrap()),
        40.0 * 30.0 * 31.0
    );
    assert!(report.failures[&id(&model, 2)].contains("Edit it in SimpleCAD 0.4"));
    assert_eq!(
        model.payload["diagnostics"][id(&model, 2)]["state"],
        json!("failed")
    );
    assert_eq!(
        model.payload["geometry"][model.payload["bodies"][0]["id"].as_str().unwrap()]["current"],
        json!(false)
    );
}

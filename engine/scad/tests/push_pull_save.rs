//! A pull made here and saved is a real step: the file reopens with its result
//! as the body, and its saved signature is what the model computes.

use scad::document::Model;
use serde_json::{Value, json};

#[test]
fn a_pull_saved_here_reopens_as_the_bodys_newest_step() {
    let path = format!(
        "{}/tests/corpus/3_fillet_move_edit_duplicate_reopen-0.scad3",
        env!("CARGO_MANIFEST_DIR")
    );
    let original = std::fs::read(path).unwrap();
    let project = scad::project::read(&original).unwrap();
    let body = project.bodies.iter().find(|b| b.brep.is_some()).unwrap();
    let mut shape = occt::read_brep(body.brep.as_ref().unwrap()).unwrap();
    if let Some(m) = &body.location {
        shape = occt::located(&shape, m).unwrap();
    }
    let faces = occt::faces(&shape).unwrap();
    let top = faces
        .iter()
        .position(|f| f.planar && f.normal[2] > 0.99)
        .unwrap() as u32;
    let pulled = occt::push_pull(&shape, top, 5.0).unwrap();
    let f = occt::face_fingerprint(&shape, top).unwrap();
    let fingerprint = json!({ "kind": "face", "geometry": f.geometry, "measure": f.measure, "center": f.center,
        "direction": f.direction, "radius": Value::Null, "neighbours": [] });

    let mut model = Model::new(project.model.clone());
    let face = model.face_ref(&body.name, top, fingerprint);
    let id = model
        .push_pull(&body.name, face, "5", occt::write_brep(&pulled).unwrap())
        .unwrap();
    let saved = model.save(&original).unwrap();

    let reopened = scad::project::read(&saved).unwrap();
    let again = reopened
        .bodies
        .iter()
        .find(|b| b.name == body.name)
        .unwrap();
    let shape = occt::read_brep(again.brep.as_ref().unwrap()).unwrap();
    assert!(
        (occt::volume(&shape) - occt::volume(&pulled)).abs() < 1e-9,
        "the reopened body is the pulled one"
    );
    let signatures = scad::signatures::signatures(&reopened.model).unwrap();
    let geometry = reopened.model["geometry"]
        .as_object()
        .unwrap()
        .values()
        .find(|g| g["feature_id"] == id)
        .unwrap();
    assert_eq!(
        geometry["signature"].as_str(),
        signatures.get(&id).map(String::as_str)
    );
    let undo = reopened.model["history"]["undo"]
        .as_array()
        .unwrap()
        .last()
        .unwrap();
    assert_eq!(undo["label"], "Pull");
    assert!(
        undo["patch"]["collections"]["features"]["remove"]
            .as_array()
            .unwrap()
            .contains(&json!(id))
    );
}

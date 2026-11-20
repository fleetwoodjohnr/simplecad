use scad::document::{Model, make_patch};
use scad::project::{CACHE_VERSION, Project, read};
use scad::signatures::signatures;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

fn python_project_with_an_undone_pull() -> Vec<u8> {
    // Preserve the Python-generated recipes, signatures, and history patches.
    // The JSON oracle has no B-Reps, so supply the three boxes those steps made.
    let payload = serde_json::from_str(include_str!("data/pulls_undone.json")).unwrap();
    let mut model = Model::new(payload);
    let body = model.payload["bodies"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let initial = model.payload["features"][0]["id"].clone();
    let first_pull = model.payload["features"][1]["id"].clone();
    let redo_signature = model.payload["history"]["redo"][0]["cache_keys"][0]
        .as_str()
        .unwrap();
    let second_pull = model.payload["cache"]["entries"][redo_signature]["feature_id"].clone();
    for (producer, height) in [(initial, 20.0), (first_pull, 25.0), (second_pull, 22.0)] {
        let shape = occt::make_box(40.0, 30.0, height).unwrap();
        let brep = occt::write_brep(&shape).unwrap();
        let sha = format!("{:x}", Sha256::digest(&brep));
        let entry = model.payload["cache"]["entries"]
            .as_object_mut()
            .unwrap()
            .values_mut()
            .find(|entry| entry["feature_id"] == producer)
            .unwrap();
        entry["outputs"][&body]["sha256"] = json!(sha);
        if model.payload["geometry"][&body]["feature_id"] == producer {
            model.payload["geometry"][&body]["sha256"] = json!(sha);
        }
        model.blobs.insert(sha, brep.into());
    }
    model.save(&[]).unwrap()
}

fn assert_box_height(project: &Project, height: f64) {
    let body = &project.bodies[0];
    assert_eq!(body.name, "Box");
    let shape = occt::read_brep(body.brep.as_ref().expect("the shown box has a B-Rep")).unwrap();
    assert!((occt::volume(&shape) - 40.0 * 30.0 * height).abs() < 1e-6);
}

#[test]
fn a_python_redo_restores_geometry_and_survives_save_and_reopen() {
    let original = python_project_with_an_undone_pull();
    let opened = read(&original).unwrap();
    assert_box_height(&opened, 25.0);
    let redo = &opened.model["history"]["redo"][0];
    let signature = redo["cache_keys"][0].as_str().unwrap();
    let expected_step = &opened.model["cache"]["entries"][signature]["feature_id"];
    let body = opened.model["bodies"][0]["id"].as_str().unwrap();

    let mut model = Model::open(&opened);
    assert!(model.can_undo());
    assert!(model.can_redo());
    model.redo().unwrap();
    assert!(model.can_undo());
    assert!(!model.can_redo());

    let saved = model.save(&original).unwrap();
    let reopened = read(&saved).unwrap();
    assert_box_height(&reopened, 22.0);
    assert_eq!(&reopened.model["bodies"][0]["producer"], expected_step);
    assert_eq!(reopened.model["geometry"][body]["signature"], signature);
    assert_eq!(reopened.model["geometry"][body]["current"], true);
    let undo = reopened.model["history"]["undo"]
        .as_array()
        .unwrap()
        .last()
        .unwrap();
    assert_eq!(undo["label"], "Pull");
    assert_eq!(
        undo["patch"]["collections"]["features"]["remove"],
        json!([expected_step])
    );

    let mut reopened_model = Model::open(&reopened);
    reopened_model.undo().unwrap();
    assert!(reopened_model.can_redo());
    let undone = reopened_model.save(&saved).unwrap();
    assert_box_height(&read(&undone).unwrap(), 25.0);
}

#[test]
fn malformed_redo_is_rejected_without_changing_the_model() {
    let original = python_project_with_an_undone_pull();
    let mut model = Model::open(&read(&original).unwrap());
    model.payload["history"]["undo"] = Value::String("damaged undo stack".into());
    let before = model.payload.clone();

    assert!(model.redo().is_err());
    assert_eq!(model.payload, before);
    assert!(model.can_redo());
}

#[test]
fn redo_updates_geometry_when_an_edited_box_keeps_its_producer() {
    let box_shape = occt::make_box(40.0, 30.0, 20.0).unwrap();
    let mut model = Model::new_box(occt::write_brep(&box_shape).unwrap()).unwrap();
    let body = model.payload["bodies"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let producer = model.payload["features"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();

    let resized = occt::make_box(40.0, 30.0, 35.0).unwrap();
    let brep = occt::write_brep(&resized).unwrap();
    let sha = format!("{:x}", Sha256::digest(&brep));
    let mut edited = model.clone();
    edited.payload["features"][0]["inputs"]["height"] = json!("35 mm");
    let signature = &signatures(&edited.payload).unwrap()[&producer];
    model.payload["cache"]["entries"][signature] = json!({
        "version": CACHE_VERSION, "feature_id": producer, "message": "",
        "outputs": { &body: { "kind": "brep", "sha256": sha } },
    });
    model.blobs.insert(sha, brep.into());
    model.payload["history"]["redo"] = json!([{
        "label": "Edit parameters",
        "patch": make_patch(&model.document(), &edited.document()),
        "cache_keys": [signature],
    }]);
    let original = model.save(&[]).unwrap();
    let opened = read(&original).unwrap();
    assert_box_height(&opened, 20.0);

    let mut reopened = Model::open(&opened);
    reopened.redo().unwrap();

    let saved = reopened.save(&original).unwrap();
    let redone = read(&saved).unwrap();
    assert_box_height(&redone, 35.0);
    assert_eq!(redone.model["bodies"][0]["producer"], producer);
    assert_eq!(redone.model["geometry"][body]["signature"], *signature);
}

use scad::document::Model;
use scad::project::{Project, read, read_saved};
use scad::rebuild::rebuild;
use serde_json::json;
use std::collections::{BTreeSet, HashMap};
use std::io::{Cursor, Read};

const ORIGINAL: &[u8] = include_bytes!("data/align.scad3");

fn project_with_the_cut_tool_deleted() -> (Model, String, String, String) {
    // The synthetic Align fixture includes a plate that was cut with a peg.
    let mut model = Model::new(read(ORIGINAL).unwrap().model);
    let features = model.payload["features"].as_array_mut().unwrap();
    let cut = features
        .iter_mut()
        .find(|feature| feature["name"] == "Combine2")
        .unwrap();
    let cut_id = cut["id"].as_str().unwrap().to_owned();
    let plate_id = cut["output_ids"]["Plate"].as_str().unwrap().to_owned();
    let peg_id = cut["output_ids"]["Peg"].as_str().unwrap().to_owned();
    let predecessor = cut["bindings"]["Plate"]["feature_id"]
        .as_str()
        .unwrap()
        .to_owned();
    cut["dropped"] = json!(["Peg"]);
    features.retain(|feature| feature["name"] != "Align2" && feature["name"] != "Cylinder");
    model.payload["bodies"]
        .as_array_mut()
        .unwrap()
        .retain(|body| body["id"] != peg_id);
    let geometry = model.payload["geometry"].as_object_mut().unwrap();
    geometry.remove(&peg_id);
    geometry.remove(&plate_id);
    model.payload["diagnostics"][&cut_id] = json!({
        "state": "needs_attention",
        "message": "The object 'Peg' this feature needs was deleted. Undo the deletion or re-select its input.",
    });
    (model, cut_id, plate_id, predecessor)
}

fn cached_plate(model: &Model, feature_id: &str, plate_id: &str) -> occt::UniquePtr<occt::Shape> {
    let entry = model.payload["cache"]["entries"]
        .as_object()
        .unwrap()
        .values()
        .find(|entry| entry["feature_id"] == feature_id)
        .unwrap();
    let sha = entry["outputs"][plate_id]["sha256"].as_str().unwrap();
    let mut archive = zip::ZipArchive::new(Cursor::new(ORIGINAL)).unwrap();
    let mut brep = Vec::new();
    archive
        .by_name(&format!("geometry/{sha}.brep"))
        .unwrap()
        .read_to_end(&mut brep)
        .unwrap();
    occt::read_brep(&brep).unwrap()
}

fn assert_plates_match(actual: &occt::Shape, expected: &occt::Shape) {
    assert!((occt::volume(actual) - occt::volume(expected)).abs() < 1e-6);
    for (a, b) in occt::bounds(actual)
        .unwrap()
        .into_iter()
        .zip(occt::bounds(expected).unwrap())
    {
        assert!((a - b).abs() < 1e-6);
    }
}

fn assert_opened_plate(project: &Project, expected: &occt::Shape) {
    let plate = project
        .bodies
        .iter()
        .find(|body| body.name == "Plate")
        .unwrap();
    let brep = plate.brep.as_ref().expect("the plate has visible geometry");
    let mut shape = occt::read_brep(brep).unwrap();
    if let Some(location) = &plate.location {
        shape = occt::located(&shape, location).unwrap();
    }
    assert_plates_match(&shape, expected);
    assert!(!project.bodies.iter().any(|body| body.name == "Peg"));
}

#[test]
fn opening_a_missing_body_recovers_the_cut_and_saving_persists_it() {
    let (model, cut_id, plate_id, _) = project_with_the_cut_tool_deleted();
    let cut_plate = cached_plate(&model, &cut_id, &plate_id);
    let original = model.save(ORIGINAL).unwrap();

    let raw = read_saved(&original).unwrap();
    assert!(
        raw.bodies
            .iter()
            .find(|body| body.name == "Plate")
            .unwrap()
            .brep
            .is_none()
    );
    let opened = read(&original).unwrap();
    assert_opened_plate(&opened, &cut_plate);
    let saved = Model::open(&opened).save(&original).unwrap();

    let reopened = read(&saved).unwrap();
    assert_opened_plate(&reopened, &cut_plate);
    let saved_body = &reopened.model["geometry"][&plate_id];
    assert!(saved_body["sha256"].is_string());
    assert_eq!(saved_body["feature_id"], cut_id);
    assert_eq!(saved_body["current"], false);
    assert_eq!(
        reopened.model["diagnostics"][&cut_id]["state"],
        "needs_attention"
    );
    let step = Model::open(&reopened)
        .steps()
        .into_iter()
        .find(|step| step.id == cut_id)
        .unwrap();
    assert_eq!(step.state, "needs_attention");
    assert!(step.message.unwrap().contains("was deleted"));

    let mut rebuilt = Model::open(&reopened);
    rebuilt.adopt_baseline(&reopened.bodies);
    let report = rebuild(&mut rebuilt, &saved, &HashMap::new(), &BTreeSet::new()).unwrap();
    assert!(report.failures[&cut_id].contains("was deleted"));
    assert_eq!(
        rebuilt.payload["diagnostics"][cut_id]["state"],
        "needs_attention"
    );
}

#[test]
fn a_missing_cut_result_falls_back_to_the_plates_preceding_step() {
    let (mut model, cut_id, plate_id, predecessor) = project_with_the_cut_tool_deleted();
    let cut_plate = cached_plate(&model, &cut_id, &plate_id);
    let uncut_plate = cached_plate(&model, &predecessor, &plate_id);
    assert!(occt::volume(&uncut_plate) > occt::volume(&cut_plate));
    model.payload["cache"]["entries"]
        .as_object_mut()
        .unwrap()
        .retain(|_, entry| entry["feature_id"] != cut_id);
    let original = model.save(ORIGINAL).unwrap();

    let opened = read(&original).unwrap();
    assert_opened_plate(&opened, &uncut_plate);
    assert_eq!(
        opened.model["diagnostics"][&cut_id]["state"],
        "needs_attention"
    );

    let report = rebuild(&mut model, &original, &HashMap::new(), &BTreeSet::new()).unwrap();
    let plate = report
        .bodies
        .iter()
        .find(|body| body.id == plate_id)
        .unwrap();
    let shape = plate
        .shape
        .as_ref()
        .expect("the rebuild preserves the plate");
    assert_plates_match(shape, &uncut_plate);
    assert_eq!(
        model.payload["diagnostics"][cut_id]["state"],
        "needs_attention"
    );
}

#[test]
fn interrupted_consumption_keeps_the_tools_last_good_result() {
    let project = read(ORIGINAL).unwrap();
    let mut model = Model::open(&project);
    let features = model.payload["features"].as_array_mut().unwrap();
    let cut = features
        .iter_mut()
        .find(|f| f["name"] == "Combine2")
        .unwrap();
    let cut_id = cut["id"].as_str().unwrap().to_owned();
    let plate_id = cut["output_ids"]["Plate"].clone();
    let peg_id = cut["output_ids"]["Peg"].as_str().unwrap().to_owned();
    // A formerly successful cut is now blocked. The next cut cannot consume
    // its peg, even though the last saved document had the peg consumed.
    cut["bindings"]["Plate"]["feature_id"] = json!("removed");
    features.push(json!({
        "id": "last-cut", "type": "boolean", "name": "Last cut",
        "inputs": {"body": {"__body__": "Plate"}, "tools": [{"__body__": "Peg"}], "operation": "cut"},
        "outputs": ["Plate"], "output_ids": {"Plate": plate_id},
        "bindings": {
            "Plate": {"feature_id": cut_id, "object_id": plate_id},
            "Peg": {"feature_id": cut_id, "object_id": peg_id},
        },
        "suppressed": false, "dropped": [],
    }));
    model.payload["bodies"]
        .as_array_mut()
        .unwrap()
        .retain(|body| body["id"] != peg_id);
    model.payload["geometry"]
        .as_object_mut()
        .unwrap()
        .remove(&peg_id);
    let expected = cached_plate(&model, &cut_id, &peg_id);
    let report = rebuild(&mut model, ORIGINAL, &HashMap::new(), &BTreeSet::new()).unwrap();
    assert!(report.failures.contains_key("last-cut"));
    let peg = report.bodies.iter().find(|body| body.id == peg_id).unwrap();
    assert_plates_match(peg.shape.as_ref().unwrap(), &expected);
    assert!(!report.bodies.iter().any(|body| body.name == "Block"));
}

#[test]
fn interrupted_consumption_cannot_revive_a_tool_with_a_reused_name() {
    let project = read(ORIGINAL).unwrap();
    let mut model = Model::open(&project);
    let features = model.payload["features"].as_array_mut().unwrap();
    let cut = features
        .iter_mut()
        .find(|f| f["name"] == "Combine2")
        .unwrap();
    let cut_id = cut["id"].as_str().unwrap().to_owned();
    let plate_id = cut["output_ids"]["Plate"].clone();
    let old_peg = cut["output_ids"]["Peg"].as_str().unwrap().to_owned();
    cut["bindings"]["Plate"]["feature_id"] = json!("removed");
    features.push(json!({
        "id": "last-cut", "type": "boolean", "name": "Last cut",
        "inputs": {"body": {"__body__": "Plate"}, "tools": [{"__body__": "Peg"}], "operation": "cut"},
        "outputs": ["Plate"], "output_ids": {"Plate": plate_id},
        "bindings": {
            "Plate": {"feature_id": cut_id, "object_id": plate_id},
            "Peg": {"feature_id": cut_id, "object_id": old_peg},
        },
        "suppressed": false, "dropped": [],
    }));
    features.push(json!({
        "id": "replacement", "type": "box", "name": "Replacement",
        "inputs": {"width": 4, "depth": 5, "height": 6},
        "outputs": ["Peg"], "output_ids": {"Peg": "new-peg"},
        "bindings": {}, "suppressed": false, "dropped": [],
    }));
    let bodies = model.payload["bodies"].as_array_mut().unwrap();
    bodies.retain(|body| body["id"] != old_peg);
    bodies
        .push(json!({"id": "new-peg", "name": "Peg", "producer": "replacement", "visible": true}));
    model.payload["geometry"]
        .as_object_mut()
        .unwrap()
        .remove(&old_peg);
    let report = rebuild(&mut model, ORIGINAL, &HashMap::new(), &BTreeSet::new()).unwrap();
    let pegs: Vec<_> = report
        .bodies
        .iter()
        .filter(|body| body.name == "Peg")
        .collect();
    assert_eq!(
        pegs.iter().map(|body| body.id.as_str()).collect::<Vec<_>>(),
        ["new-peg"]
    );
    let peg = pegs[0].shape.as_ref().unwrap();
    assert!((occt::volume(peg) - 120.0).abs() < 1e-6);
    assert!(report.failures.contains_key("last-cut"));
}

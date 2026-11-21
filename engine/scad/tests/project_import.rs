use scad::document::Model;
use scad::project;
use scad::rebuild::{Body, create_primitive, create_transform, rebuild};
use serde_json::json;
use std::collections::{BTreeSet, HashMap};
use std::rc::Rc;

#[test]
fn native_import_remaps_recipes_parameters_and_groups_and_survives_undo() {
    let start = occt::make_box(40.0, 30.0, 20.0).unwrap();
    let mut destination = Model::new_box(occt::write_brep(&start).unwrap()).unwrap();
    destination.payload["parameters"] =
        json!([{ "name": "width", "expression": "4 mm", "dimension": "length" }]);
    let mut source = Model::new(json!({
        "title": "Source", "features": [], "bodies": [], "groups": [], "counters": {},
        "parameters": [
            { "name": "width", "expression": "25 mm", "dimension": "length" },
            { "name": "depth", "expression": "width / 5", "dimension": "length" },
        ],
    }));
    let (name, shape) = create_primitive(
        &mut source,
        "box",
        json!({"width": "width", "depth": "depth", "height": "2 mm"}),
    )
    .unwrap();
    create_transform(&mut source, &name, &shape, "Move", json!({ "dx": "width" })).unwrap();
    source.payload["groups"] =
        json!([{ "id": "source-group-id", "name": "Parts", "members": [&name], "visible": true }]);
    let bytes = source.save(&[]).unwrap();
    let source = project::read(&bytes).unwrap();
    let selected = source
        .bodies
        .iter()
        .map(|body| Body {
            id: body.id.clone(),
            name: body.name.clone(),
            shape: Some(Rc::new(
                occt::read_brep(body.brep.as_deref().unwrap()).unwrap(),
            )),
        })
        .collect();
    let (mut imported, bodies) = destination
        .import_project(&source, &bytes, selected, [50.0, 0.0, 0.0])
        .unwrap();
    assert_eq!(bodies[0].name, "Box2");
    assert_ne!(bodies[0].id, source.bodies[0].id);
    assert_ne!(imported.payload["groups"][0]["id"], "source-group-id");
    assert_eq!(imported.payload["groups"][0]["members"], json!(["Box2"]));
    assert_eq!(imported.payload["parameters"][1]["name"], "width_copy");
    assert_eq!(
        imported.payload["parameters"][2]["expression"],
        "width_copy / 5"
    );
    assert_eq!(
        imported.payload["features"][2]["inputs"]["body"]["__body__"],
        "Box2"
    );
    assert_eq!(
        imported.payload["features"][2]["inputs"]["dx"],
        "width_copy"
    );

    imported.move_history(false).unwrap();
    assert_eq!(imported.payload["bodies"].as_array().unwrap().len(), 1);
    assert!(imported.payload["groups"].as_array().unwrap().is_empty());
    imported.move_history(true).unwrap();
    assert_eq!(imported.payload["bodies"].as_array().unwrap().len(), 2);

    let archive = imported.save(&[]).unwrap();
    let reopened = project::read(&archive).unwrap();
    assert_eq!(reopened.bodies.len(), 2);
    let mut model = Model::open(&reopened);
    let stale: BTreeSet<String> = model.payload["features"]
        .as_array()
        .unwrap()
        .iter()
        .map(|feature| feature["id"].as_str().unwrap().into())
        .collect();
    let report = rebuild(&mut model, &archive, &HashMap::new(), &stale).unwrap();
    assert!(report.failures.is_empty(), "{:?}", report.failures);
    let shape = report
        .bodies
        .iter()
        .find(|body| body.name == "Box2")
        .unwrap()
        .shape
        .as_ref()
        .unwrap();
    let bounds = occt::bounds(shape).unwrap();
    assert!((bounds[0] - 75.0).abs() < 1e-6, "{bounds:?}");
    assert!((bounds[3] - 100.0).abs() < 1e-6, "{bounds:?}");
}

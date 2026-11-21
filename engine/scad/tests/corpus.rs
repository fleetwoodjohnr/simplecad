//! The documents `tests/test_scenarios.py` builds, saved by the Python app
//! (`scripts/engine_oracles.py`): each must give the signatures Python gave it.

use std::collections::BTreeMap;

#[test]
fn every_scenario_document_has_the_python_apps_signatures() {
    let folder = format!("{}/tests/corpus", env!("CARGO_MANIFEST_DIR"));
    let expected: BTreeMap<String, BTreeMap<String, String>> = serde_json::from_str(
        &std::fs::read_to_string(format!("{folder}/signatures.json")).unwrap(),
    )
    .unwrap();
    assert!(expected.len() >= 7);
    for (file, python) in &expected {
        let project =
            scad::project::read(&std::fs::read(format!("{folder}/{file}")).unwrap()).unwrap();
        let rust = scad::signatures::signatures(&project.model).expect("signatures");
        assert_eq!(&rust, python, "{file}");
    }
}

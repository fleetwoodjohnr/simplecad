//! Each corpus document, and a copy cut back to format 1, must load as the
//! Python app's `Document.from_dict(...).to_dict()` does, with its signatures
//! (`scripts/engine_oracles.py` writes `data/normalised.json`).

use serde_json::Value;

#[test]
fn every_document_loads_as_the_python_app_holds_it() {
    let path = format!("{}/tests/data/normalised.json", env!("CARGO_MANIFEST_DIR"));
    let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert!(cases.len() >= 14);
    for case in &cases {
        let name = case["name"].as_str().unwrap();
        let mut model = case["model"].clone();
        scad::graph::normalise(&mut model);
        for (key, expected) in case["document"].as_object().unwrap() {
            assert_eq!(&model[key], expected, "{name}: {key}");
        }
        let signatures = scad::signatures::signatures(&model).expect(name);
        let expected: std::collections::BTreeMap<String, String> =
            serde_json::from_value(case["signatures"].clone()).unwrap();
        assert_eq!(signatures, expected, "{name}: signatures");
    }
}

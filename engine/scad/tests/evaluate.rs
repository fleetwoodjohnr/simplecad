//! Expressions evaluate as the Python app evaluates them (`params.evaluate`):
//! the same value to the bit, or the same error. `scripts/engine_oracles.py`
//! writes `data/evaluate.json`.

use scad::evaluate::{Dimension, EvalError, evaluate};
use serde_json::Value;
use std::collections::HashMap;

#[test]
fn every_expression_has_the_python_apps_value_or_error() {
    let path = format!("{}/tests/data/evaluate.json", env!("CARGO_MANIFEST_DIR"));
    let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert!(cases.len() > 3000);
    let mut wrong = Vec::new();
    for case in &cases {
        let text = case[0].as_str().unwrap();
        let names: HashMap<String, f64> = serde_json::from_value(case[1].clone()).unwrap();
        for (dimension, python) in [Dimension::Length, Dimension::Angle, Dimension::Scalar]
            .iter()
            .zip(case[2].as_array().unwrap())
        {
            let rust = evaluate(text, &names, *dimension);
            let same = match (&rust, python.get("value"), python.get("raised")) {
                (Ok(v), Some(expected), _) => {
                    let expected: f64 = expected.as_str().unwrap().parse().unwrap();
                    v.to_bits() == expected.to_bits() || (v.is_nan() && expected.is_nan())
                }
                (Err(EvalError::Expression(m)), None, None) => {
                    m == python["error"].as_str().unwrap()
                }
                (Err(EvalError::Raised(kind, m)), None, Some(expected)) => {
                    kind == &expected.as_str().unwrap() && m == python["error"].as_str().unwrap()
                }
                _ => false,
            };
            if !same {
                wrong.push(format!(
                    "{text:?} {dimension:?}: rust {rust:?}, python {python}"
                ));
            }
        }
    }
    assert!(
        wrong.is_empty(),
        "{} of {} differ:\n{}",
        wrong.len(),
        cases.len() * 3,
        wrong[..wrong.len().min(40)].join("\n")
    );
}

#[test]
fn parameters_resolve_as_the_python_apps_parameter_sets() {
    let path = format!("{}/tests/data/parameters.json", env!("CARGO_MANIFEST_DIR"));
    let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    for case in &cases {
        let (values, errors) = scad::evaluate::resolve(case[0].as_array().unwrap());
        let expected: HashMap<String, String> = serde_json::from_value(case[1].clone()).unwrap();
        let rust: HashMap<String, String> = values
            .iter()
            .map(|(k, v)| {
                (
                    k.clone(),
                    if v.is_infinite() {
                        if *v > 0.0 {
                            "inf".into()
                        } else {
                            "-inf".into()
                        }
                    } else {
                        scad::pyjson::float_repr(*v)
                    },
                )
            })
            .collect();
        assert_eq!(rust, expected, "{}", case[0]);
        let expected: std::collections::BTreeMap<String, String> =
            serde_json::from_value(case[2].clone()).unwrap();
        assert_eq!(errors, expected, "{}", case[0]);
    }
}

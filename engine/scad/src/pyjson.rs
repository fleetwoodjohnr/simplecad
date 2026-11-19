//! `json.dumps(value, sort_keys=True, separators=(",", ":"))`, byte for byte as
//! Python writes it: the form every cache signature hashes (`result_store.digest`).
//! Python spells floats with `repr` (`1e-05`, `1e+16`, `100.0`) and escapes every
//! non-ASCII character (`é`). `tests/data/pyjson.json` holds Python's output.

use serde_json::Value;
use std::fmt::Write;

pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    write(value, &mut out);
    out
}

fn write(value: &Value, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => match (n.as_i64(), n.as_u64(), n.as_f64()) {
            (Some(i), _, _) => write!(out, "{i}").unwrap(),
            (_, Some(u), _) => write!(out, "{u}").unwrap(),
            (_, _, Some(f)) => out.push_str(&float_repr(f)),
            _ => unreachable!("a JSON number is an integer or a float"),
        },
        Value::String(s) => string(s, out),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write(item, out);
            }
            out.push(']');
        }
        // serde_json's map is ordered by key, which is Python's sort_keys order (code points).
        Value::Object(map) => {
            out.push('{');
            for (i, (key, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                string(key, out);
                out.push(':');
                write(item, out);
            }
            out.push('}');
        }
    }
}

/// Python's `repr(float)`: the shortest digits that read back the same, fixed
/// notation for decimal exponents from -4 to 16, `e±XX` beyond.
pub fn float_repr(f: f64) -> String {
    if f.is_nan() {
        return "NaN".into();
    }
    if f.is_infinite() {
        return if f > 0.0 { "Infinity" } else { "-Infinity" }.into();
    }
    if f == 0.0 {
        return if f.is_sign_negative() { "-0.0" } else { "0.0" }.into();
    }
    // Rust's `{:e}` is also the shortest round-trip form: `-1.2345e3`.
    let sci = format!("{f:e}");
    let (mantissa, exponent) = sci.split_once('e').expect("{:e} has an exponent");
    let (sign, mantissa) = mantissa
        .strip_prefix('-')
        .map_or(("", mantissa), |m| ("-", m));
    let mut digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let point = exponent.parse::<i32>().expect("an integer exponent") + 1;
    if let Some(even) = tie_to_even(f, &digits) {
        digits = even;
    }
    let len = digits.len() as i32;
    if -4 < point && point <= 16 {
        let body = if point <= 0 {
            format!("0.{}{digits}", "0".repeat((-point) as usize))
        } else if point >= len {
            format!("{digits}{}.0", "0".repeat((point - len) as usize))
        } else {
            format!(
                "{}.{}",
                &digits[..point as usize],
                &digits[point as usize..]
            )
        };
        format!("{sign}{body}")
    } else {
        let tail = if len > 1 {
            format!(".{}", &digits[1..])
        } else {
            String::new()
        };
        let e = point - 1;
        format!(
            "{sign}{}{tail}e{}{:02}",
            &digits[..1],
            if e < 0 { '-' } else { '+' },
            e.abs()
        )
    }
}

/// Where two shortest spellings are equally close to `f` (its exact value ends in
/// a 5 just past them), Python's dtoa takes the even last digit and Rust's
/// formatter the one above: `926912065382647.25` is `…647.2` in Python, `…647.3` here.
fn tie_to_even(f: f64, digits: &str) -> Option<String> {
    let k = digits.len();
    let exact = format!("{:.*e}", k + 40, f.abs());
    let exact: String = exact
        .split_once('e')?
        .0
        .chars()
        .filter(|c| *c != '.')
        .collect();
    let (head, rest) = exact.split_at(k);
    if !rest.starts_with('5') || rest[1..].bytes().any(|b| b != b'0') {
        return None;
    }
    let last = head.as_bytes()[k - 1] - b'0';
    if last.is_multiple_of(2) {
        return (head != digits).then(|| head.to_string());
    }
    // The even one is a step up; a carry past the first digit is left to Rust's choice.
    let up = (head.parse::<u128>().ok()? + 1).to_string();
    (up.len() == k && up != digits).then_some(up)
}

/// A JSON string with `ensure_ascii`: `\uXXXX` (lower-case hex, surrogate pairs
/// past the BMP) for every non-ASCII character and control character.
fn string(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if c.is_ascii() && !c.is_ascii_control() => out.push(c),
            c => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    write!(out, "\\u{unit:04x}").unwrap();
                }
            }
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_what_python_writes() {
        let path = format!("{}/tests/data/pyjson.json", env!("CARGO_MANIFEST_DIR"));
        let cases: Vec<(Value, String)> =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        let wrong: Vec<_> = cases
            .iter()
            .filter(|(value, python)| dumps(value) != *python)
            .map(|(value, python)| format!("python {python} rust {}", dumps(value)))
            .collect();
        assert!(
            wrong.is_empty(),
            "{} of {} differ:\n{}",
            wrong.len(),
            cases.len(),
            wrong.join("\n")
        );
    }
}

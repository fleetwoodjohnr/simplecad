//! Metric layouts and solid relief on planar and cylindrical faces.

use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, failed};
use occt::{PlaneFrame, TextureRegion, TextureSpec};
use serde_json::Value;
use std::rc::Rc;

const PATTERNS: &[(&str, &str)] = &[
    ("horizontal_lines", "Horizontal lines"),
    ("vertical_lines", "Vertical lines"),
    ("diagonal_lines", "Diagonal lines"),
    ("crosshatch", "Crosshatch"),
    ("grid", "Grid"),
    ("hexagon", "Hexagon / honeycomb"),
    ("diamond", "Diamond pattern"),
    ("knurling", "Knurling"),
    ("ribbed", "Ribbed texture"),
    ("wave", "Wave pattern"),
    ("dots", "Dots"),
    ("raised_bumps", "Raised bumps"),
    ("recessed_grooves", "Recessed grooves"),
    ("geometric", "Geometric pattern"),
    ("vent", "Vent-style pattern"),
    ("grip", "Grip texture"),
];

type Vector = [f64; 3];

fn vector(value: &Value) -> Option<Vector> {
    let fields = value.as_array()?;
    Some([
        fields.first()?.as_f64()?,
        fields.get(1)?.as_f64()?,
        fields.get(2)?.as_f64()?,
    ])
}

fn cross(a: Vector, b: Vector) -> Vector {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn dot(a: Vector, b: Vector) -> f64 {
    a.into_iter().zip(b).map(|(a, b)| a * b).sum()
}

fn unit(v: Vector) -> Option<Vector> {
    let length = dot(v, v).sqrt();
    (length > 1e-12).then(|| v.map(|x| x / length))
}

fn text_regions(ctx: &Context<'_>, name: &str, plane: &PlaneFrame) -> Vec<TextureRegion> {
    if !plane.planar {
        return Vec::new();
    }
    ctx.ancestors()
        .into_iter()
        .filter_map(|feature| {
            let inputs = &feature["inputs"];
            let body = inputs["body"]["__body__"]
                .as_str()
                .or(inputs["body"].as_str());
            if feature["type"] != "text" || feature["suppressed"] == true || body != Some(name) {
                return None;
            }
            let mark = &inputs["face"]["__ref__"]["fingerprint"];
            let center = vector(&mark["center"])?;
            let normal = unit(vector(&mark["direction"]).unwrap_or(plane.normal))?;
            let x = if normal[2].abs() > 0.9 {
                [1.0, 0.0, 0.0]
            } else {
                unit([-normal[1], normal[0], 0.0])?
            };
            let y = cross(normal, x);
            let read = |key, default, dimension| {
                ctx.number(inputs.get(key), default, dimension)
                    .unwrap_or(default)
            };
            let height = read("text_height", 8.0, Dimension::Length);
            let width = height
                * inputs["text"]
                    .as_str()
                    .map_or(1, |s| s.chars().count().max(1)) as f64
                * 1.2
                / 2.0;
            let offset_x = read("offset_x", 0.0, Dimension::Length);
            let offset_y = read("offset_y", 0.0, Dimension::Length);
            let (sine, cosine) = read("rotation", 0.0, Dimension::Angle)
                .to_radians()
                .sin_cos();
            let outline = [
                [width, -height],
                [width, height],
                [-width, height],
                [-width, -height],
            ]
            .into_iter()
            .flat_map(|[a, b]| {
                let u = a * cosine - b * sine + offset_x;
                let v = a * sine + b * cosine + offset_y;
                let point =
                    std::array::from_fn(|i| center[i] + x[i] * u + y[i] * v - plane.origin[i]);
                [dot(point, plane.x_dir), dot(point, plane.y_dir)]
            })
            .collect();
            Some(TextureRegion { outline })
        })
        .collect()
}

pub(crate) fn build(ctx: &Context<'_>, name: &str) -> Result<Shape, StepError> {
    let string = |key, default| ctx.input(key).and_then(Value::as_str).unwrap_or(default);
    let package = string("package", "builtin.v1");
    let pattern = string("pattern", "horizontal_lines");
    let unavailable = || {
        failed(format!(
            "Texture pattern {package}/{pattern} is unavailable."
        ))
    };
    if package != "builtin.v1" {
        return Err(unavailable());
    }
    let (_, label) = PATTERNS
        .iter()
        .find(|(key, _)| *key == pattern)
        .ok_or_else(unavailable)?;
    let mode = string("mode", "raised");
    if !matches!(mode, "raised" | "recessed") && !(mode == "through" && pattern == "vent") {
        return Err(failed(
            "Choose Raised or Recessed; Through is available for vent patterns.",
        ));
    }
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let settings = TextureSpec {
        size: length("size", 3.0)?,
        spacing: length("spacing", 2.0)?,
        depth: length("depth", 0.6)?,
        rotation: ctx.value("rotation", 0.0, Dimension::Angle)?,
        offset_u: length("offset_u", 0.0)?,
        offset_v: length("offset_v", 0.0)?,
        edge_clearance: length("edge_clearance", 3.0)?,
        hole_clearance: length("hole_clearance", 2.0)?,
        text_clearance: length("text_clearance", 1.5)?,
        other_clearance: length("other_clearance", 2.0)?,
    };
    if settings.size <= 0.0
        || settings.depth <= 0.0
        || [
            settings.spacing,
            settings.edge_clearance,
            settings.hole_clearance,
            settings.text_clearance,
            settings.other_clearance,
        ]
        .into_iter()
        .any(|value| value < 0.0)
    {
        return Err(failed(
            "Texture size and depth must be positive; spacing and clearances cannot be negative.",
        ));
    }
    if settings.size < 0.1 {
        return Err(failed("The texture size is too small to build reliably."));
    }
    let body = ctx.shape("body")?;
    let (owner, index) = ctx.face("face")?;
    let kernel = |error: occt::Exception| failed(error.what());
    let face = occt::sub_face(&owner, index).map_err(kernel)?;
    let regions = text_regions(ctx, name, &occt::plane_frame(&face).map_err(kernel)?);
    let result =
        occt::face_texture(&body, &face, pattern, mode, &settings, &regions).map_err(kernel)?;
    ctx.set_message(format!("{label} on one face"));
    Ok(Rc::new(result))
}

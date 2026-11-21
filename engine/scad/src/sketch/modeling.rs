use super::{Layout, entries, text};
use crate::evaluate::Dimension;
use crate::rebuild::{Context, StepError, failed, kernel};
use occt::{
    Shape, UniquePtr,
    ffi::{SketchAxis, SketchCurve, SketchEdge, SketchPlane},
};
use serde_json::Value;

fn vector(value: &Value, default: [f64; 3]) -> Result<[f64; 3], StepError> {
    if value.is_null() {
        return Ok(default);
    }
    serde_json::from_value(value.clone())
        .map_err(|_| failed("A sketch plane or axis needs three coordinates."))
}

fn read_plane(value: &Value) -> Result<SketchPlane, StepError> {
    let origin = vector(&value["origin"], [0., 0., 0.])?;
    let x_axis = vector(&value["x_axis"], [1., 0., 0.])?;
    let normal = vector(&value["normal"], [0., 0., 1.])?;
    let square_norm = |a: [f64; 3]| a.iter().map(|v| v * v).sum::<f64>();
    let dot = x_axis.iter().zip(normal).map(|(x, n)| x * n).sum::<f64>();
    if (square_norm(x_axis) - 1.).abs() > 1e-6
        || (square_norm(normal) - 1.).abs() > 1e-6
        || dot.abs() > 1e-6
    {
        return Err(failed("A sketch plane needs perpendicular unit axes."));
    }
    Ok(SketchPlane {
        origin,
        x_axis,
        normal,
    })
}

fn read_edges(sketch: &Value) -> Result<Vec<SketchEdge>, StepError> {
    let layout = Layout::read(sketch)?;
    let point = |value| {
        let index = layout.point(text(value)?)?.start;
        Ok::<_, StepError>([layout.values[index], layout.values[index + 1]])
    };
    let size = |entity: &Value, key: &str| {
        entity[key]
            .as_f64()
            .ok_or_else(|| failed(format!("The sketch curve is missing '{key}'.")))
    };
    let mut edges = Vec::new();
    for entity in entries(sketch, "entities")? {
        if entity["construction"].as_bool().unwrap_or(false) {
            continue;
        }
        let centre = || Ok::<_, StepError>(point(&entity["center"])?.to_vec());
        let (curve, points, parameters) = match text(&entity["kind"])? {
            "line" => {
                let mut points = point(&entity["start"])?.to_vec();
                points.extend(point(&entity["end"])?);
                (SketchCurve::Line, points, [0.; 3])
            }
            "circle" => (
                SketchCurve::Circle,
                centre()?,
                [size(entity, "radius")?, 0., 0.],
            ),
            "arc" => (
                SketchCurve::Arc,
                centre()?,
                [
                    size(entity, "radius")?,
                    size(entity, "start_angle")?,
                    size(entity, "end_angle")?,
                ],
            ),
            "ellipse" => (
                SketchCurve::Ellipse,
                centre()?,
                [
                    size(entity, "major")?,
                    size(entity, "minor")?,
                    entity["rotation"].as_f64().unwrap_or(0.),
                ],
            ),
            "spline" => {
                let mut points = Vec::new();
                for reference in entries(entity, "points")? {
                    points.extend(point(reference)?);
                }
                (SketchCurve::Spline, points, [0.; 3])
            }
            _ => unreachable!("Layout::read checked every entity kind"),
        };
        edges.push(SketchEdge {
            curve,
            points,
            parameters,
        });
    }
    Ok(edges)
}

pub(crate) fn build(kind: &str, ctx: &Context<'_>) -> Result<UniquePtr<Shape>, StepError> {
    let sketch = ctx.sketch("sketch")?;
    let plane = read_plane(&sketch["plane"])?;
    let edges = read_edges(sketch)?;
    if kind == "extrude" {
        let distance = ctx.value("distance", 10., Dimension::Length)?;
        if distance.abs() < 1e-9 {
            return Err(failed("An extrude needs a distance greater than zero."));
        }
        let symmetric = ctx
            .input("symmetric")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        occt::extrude_sketch(&plane, &edges, distance, symmetric).map_err(kernel)
    } else {
        let angle = ctx.value("angle", 360., Dimension::Angle)?;
        if angle.abs() < 1e-9 {
            return Err(failed("A revolve needs an angle greater than zero."));
        }
        let axis = SketchAxis {
            origin: vector(
                ctx.input("axis_origin").unwrap_or(&Value::Null),
                plane.origin,
            )?,
            direction: vector(
                ctx.input("axis_direction").unwrap_or(&Value::Null),
                plane.x_axis,
            )?,
        };
        occt::revolve_sketch(&plane, &edges, &axis, angle.to_radians()).map_err(kernel)
    }
}

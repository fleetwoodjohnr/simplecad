//! Body-only draft, mirror and patterns from Python's advanced modeling tools.

use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, failed};
use serde_json::Value;
use std::rc::Rc;

fn vector(ctx: &Context, key: &str, default: [f64; 3]) -> Result<[f64; 3], StepError> {
    let Some(values) = ctx
        .input(key)
        .and_then(Value::as_array)
        .filter(|v| !v.is_empty())
    else {
        return Ok(default);
    };
    let values: Vec<_> = values.iter().filter_map(Value::as_f64).collect();
    values
        .try_into()
        .map_err(|_| failed(format!("The {key} needs three coordinates.")))
}

pub(crate) fn build(kind: &str, ctx: &Context) -> Result<Shape, StepError> {
    let shape = ctx.shape("body")?;
    let kernel = |e: occt::Exception| failed(e.what());
    if kind == "draft" {
        let angle = ctx.value("angle", 3.0, Dimension::Angle)?;
        if angle.abs() < 1e-9 {
            return Err(failed("A draft needs an angle greater than zero."));
        }
        let faces: Vec<_> = ctx
            .faces("faces")?
            .into_iter()
            .map(|(_, index)| index)
            .collect();
        let origin = vector(ctx, "neutral_plane", [0.0; 3])?;
        let pull = vector(ctx, "pull_direction", [0.0, 0.0, 1.0])?;
        return occt::draft_angle(&shape, &faces, &origin, &pull, angle.to_radians())
            .map(Rc::new)
            .map_err(kernel);
    }

    let count = |key, default| {
        Ok::<_, StepError>((ctx.value(key, default, Dimension::Length)? as usize).max(1))
    };
    let too_many = || failed("That pattern would make over 400 copies.");
    let mut copies = occt::shapes_new();
    let mut add = |transform: &occt::Trsf| -> Result<(), StepError> {
        let copy = occt::transform_shape(&shape, transform, true).map_err(kernel)?;
        occt::shapes_push(copies.pin_mut(), &copy);
        Ok(())
    };
    match kind {
        "mirror" => {
            let origin = vector(ctx, "origin", [0.0; 3])?;
            let normal = vector(ctx, "normal", [1.0, 0.0, 0.0])?;
            let mirror = occt::trsf_mirror(&origin, &normal).map_err(kernel)?;
            if ctx.input("keep").and_then(Value::as_bool) == Some(false) {
                return occt::transform_shape(&shape, &mirror, true)
                    .map(Rc::new)
                    .map_err(kernel);
            }
            add(&mirror)?;
        }
        "rectangular_pattern" => {
            let (nx, ny) = (count("count_x", 2.0)?, count("count_y", 1.0)?);
            if nx.saturating_mul(ny) > 400 {
                return Err(too_many());
            }
            if nx == 1 && ny == 1 {
                return Ok(shape);
            }
            let sx = ctx.value("spacing_x", 20.0, Dimension::Length)?;
            let sy = ctx.value("spacing_y", 20.0, Dimension::Length)?;
            let x = vector(ctx, "direction_x", [1.0, 0.0, 0.0])?;
            let y = vector(ctx, "direction_y", [0.0, 1.0, 0.0])?;
            for i in 0..nx {
                for j in 0..ny {
                    if i == 0 && j == 0 {
                        continue;
                    }
                    let by: [f64; 3] =
                        std::array::from_fn(|k| x[k] * sx * i as f64 + y[k] * sy * j as f64);
                    add(&occt::trsf_translation(&by))?;
                }
            }
        }
        "circular_pattern" => {
            let count = count("count", 4.0)?;
            if count > 400 {
                return Err(too_many());
            }
            if count == 1 {
                return Ok(shape);
            }
            let total = ctx.value("total_angle", 360.0, Dimension::Angle)?;
            let axis = vector(ctx, "axis", [0.0, 0.0, 1.0])?;
            let center = vector(ctx, "center", [0.0; 3])?;
            let full = (total.abs() - 360.0).abs() < 1e-9;
            let divisor = if full { count } else { count - 1 };
            for index in 1..count {
                let angle = (total * index as f64 / divisor as f64).to_radians();
                let turn = occt::trsf_rotation(&center, &axis, angle).map_err(kernel)?;
                add(&turn)?;
            }
        }
        _ => unreachable!("unknown advanced modeling step: {kind}"),
    }
    occt::fuse_copies(&shape, &copies)
        .map(Rc::new)
        .map_err(kernel)
}

//! Body-only draft, mirror, patterns and rows from Python's modeling tools.

use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, alias, failed};
use serde_json::Value;
use std::collections::{BTreeMap, HashSet};
use std::f64::consts::FRAC_PI_2;
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

/// Distribute the saved row in world coordinates, retaining each separate body.
pub(crate) fn arrange_row(ctx: &Context) -> Result<BTreeMap<String, Shape>, StepError> {
    if ctx.input("pattern").is_some_and(|pattern| pattern != "row") {
        return Err(failed(
            "This arrangement pattern must be rebuilt, and this version cannot build it yet. Edit it in SimpleCAD 0.4.",
        ));
    }
    if ctx.input("target_face").is_some_and(|face| !face.is_null()) {
        return Err(failed(
            "This arrangement uses a target face, and this version cannot build it yet. Edit it in SimpleCAD 0.4.",
        ));
    }

    let inputs: Vec<_> = ctx
        .input("bodies")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|input| {
            let name = alias(input);
            let shape = ctx.named_shape(&name)?;
            Ok((name, shape))
        })
        .collect::<Result<_, StepError>>()?;
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let offset = [length("dx", 0.0)?, length("dy", 0.0)?, length("dz", 0.0)?];
    let mode = ctx.input("mode").map_or(Some("equal_gaps"), Value::as_str);
    let axis = match ctx.input("axis").and_then(Value::as_str) {
        Some("y") => 1,
        Some("z") => 2,
        _ => 0,
    };
    let mut gap = length("gap", 10.0)?;
    // The Python row evaluates this saved input even for rows without a face.
    length("normal_offset", 0.0)?;
    let equal = mode == Some("equal_gaps");
    if inputs.len() < if equal { 3 } else { 2 } {
        return Err(failed(if equal {
            "Equal spacing needs at least three objects."
        } else {
            "A fixed gap needs at least two objects."
        }));
    }
    if !equal && mode != Some("fixed_gap") {
        return Err(failed("That arrangement mode is not available."));
    }
    if !equal && gap < 0.0 {
        return Err(failed("The clear gap cannot be negative."));
    }

    let kernel = |e: occt::Exception| failed(e.what());
    let turns = [
        occt::trsf_identity(),
        occt::trsf_rotation(&[0.0; 3], &[0.0, 0.0, -1.0], FRAC_PI_2).map_err(kernel)?,
        occt::trsf_rotation(&[0.0; 3], &[0.0, 1.0, 0.0], FRAC_PI_2).map_err(kernel)?,
    ];
    let mut items = Vec::with_capacity(inputs.len());
    for (name, shape) in inputs {
        let mut ends = [(0.0, 0.0); 3];
        for (bounds, turn) in ends.iter_mut().zip(&turns) {
            let turned = occt::transform_shape(&shape, turn, true).map_err(kernel)?;
            let box_bounds = occt::bounds(&turned).map_err(kernel)?;
            *bounds = (box_bounds[0], box_bounds[3]);
        }
        items.push((name, shape, ends));
    }
    let center = |(low, high): (f64, f64)| (low + high) / 2.0;
    let width = |(low, high): (f64, f64)| high - low;
    items.sort_by(|a, b| center(a.2[axis]).total_cmp(&center(b.2[axis])));
    let mut cursor = items[0].2[axis].0;
    if equal {
        let mut seen = HashSet::new();
        let occupied: f64 = items
            .iter()
            .filter(|(name, _, _)| seen.insert(name))
            .map(|(_, _, ends)| width(ends[axis]))
            .sum();
        let clear =
            (items[items.len() - 1].2[axis].1 - cursor - occupied) / (items.len() - 1) as f64;
        if clear < -1e-6 {
            return Err(failed(
                "The outer objects are too close for clear equal gaps. Move them farther apart or use Fixed gap.",
            ));
        }
        gap = clear.max(0.0);
    }
    let count = items.len();
    let centerline: [f64; 3] = std::array::from_fn(|cross| {
        items
            .iter()
            .map(|(_, _, ends)| center(ends[cross]))
            .sum::<f64>()
            / count as f64
    });
    let mut outputs = BTreeMap::new();
    for (name, shape, ends) in items {
        let width = width(ends[axis]);
        let mut desired = centerline;
        desired[axis] = cursor + width / 2.0;
        cursor += width;
        cursor += gap;
        let shift: [f64; 3] = std::array::from_fn(|i| desired[i] - center(ends[i]) + offset[i]);
        let shape =
            occt::transform_shape(&shape, &occt::trsf_translation(&shift), true).map_err(kernel)?;
        outputs.insert(name, Rc::new(shape));
    }
    ctx.set_message(format!(
        "Arranged {count} objects with {} mm gaps.",
        crate::align::format_g(gap)
    ));
    Ok(outputs)
}

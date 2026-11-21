//! Body-only draft, mirror, patterns and rows from Python's modeling tools.

use crate::align::{Frame, arrangement_frame, rotation_between, truthy};
use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, alias, failed, kernel};
use crate::vector::{V, add, dot, scale, sub};
use serde_json::Value;
use std::collections::{BTreeMap, HashSet};
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
        "path_pattern" => {
            let count = count("count", 4.0)?;
            if count > 400 {
                return Err(too_many());
            }
            let profile = crate::sketch::modeling::read_profile(ctx.sketch("path")?)?;
            let offsets = occt::sketch_path_offsets(&profile, count as u32).map_err(kernel)?;
            if offsets.is_empty() {
                return Ok(shape);
            }
            for translation in offsets.as_chunks::<3>().0 {
                add(&occt::trsf_translation(translation))?;
            }
        }
        _ => unreachable!("unknown advanced modeling step: {kind}"),
    }
    occt::fuse_copies(&shape, &copies)
        .map(Rc::new)
        .map_err(kernel)
}

const WORLD_AXES: [V; 3] = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]];

fn projected_extent(shape: &Shape, turn: &occt::Trsf) -> Result<(f64, f64), StepError> {
    let turned = occt::transform_shape(shape, turn, true).map_err(kernel)?;
    let bounds = occt::bounds(&turned).map_err(kernel)?;
    Ok((bounds[0], bounds[3]))
}

fn translated(shape: &Shape, shift: V) -> Result<Shape, StepError> {
    occt::transform_shape(shape, &occt::trsf_translation(&shift), true)
        .map(Rc::new)
        .map_err(kernel)
}

/// Bring the support face or lowest part of the body down to the target plane.
fn seat_row_body(
    shape: Shape,
    frame: &Frame,
    normal_turn: &occt::Trsf,
    orient: bool,
) -> Result<(Shape, bool), StepError> {
    if orient {
        let faces = occt::faces(&shape).map_err(kernel)?;
        let distance = |f: &occt::FaceData| dot(sub(f.center, frame.center), frame.normal).abs();
        let support = faces
            .iter()
            .enumerate()
            .filter(|(_, face)| face.planar)
            .min_by(|(i, a), (j, b)| {
                b.area
                    .total_cmp(&a.area)
                    .then_with(|| distance(a).total_cmp(&distance(b)))
                    .then_with(|| i.cmp(j))
            });
        if let Some((index, support)) = support {
            let turn = rotation_between(support.normal, scale(frame.normal, -1.0), support.center)?;
            let placed = Rc::new(occt::transform_shape(&shape, &turn, true).map_err(kernel)?);
            let support_face = occt::sub_face(&shape, index as u32).map_err(kernel)?;
            let seated_face = occt::transform_shape(&support_face, &turn, true).map_err(kernel)?;
            let seated = occt::plane_frame(&seated_face).map_err(kernel)?;
            let gap = dot(sub(frame.center, seated.center), frame.normal);
            return Ok((translated(&placed, scale(frame.normal, gap))?, false));
        }
    }
    let (low, _) = projected_extent(&shape, normal_turn)?;
    let gap = dot(frame.center, frame.normal) - low;
    Ok((translated(&shape, scale(frame.normal, gap))?, orient))
}

/// Distribute separately named bodies along an axis in world or target-face coordinates.
pub(crate) fn arrange_row(ctx: &Context) -> Result<BTreeMap<String, Shape>, StepError> {
    if ctx.input("pattern").is_some_and(|pattern| pattern != "row") {
        return Err(failed(
            "This arrangement pattern must be rebuilt, and this version cannot build it yet. Edit it in SimpleCAD 0.4.",
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
    let mode = ctx.input("mode").map_or(Some("equal_gaps"), Value::as_str);
    let axis = ctx.input("axis").and_then(Value::as_str).unwrap_or("x");
    let target = ctx
        .input("target_face")
        .filter(|selected| !selected.is_null())
        .map(|_| {
            let (body, index) = ctx.face("target_face")?;
            let stable = ctx
                .input("frame_mode")
                .is_some_and(|mode| mode == "reference");
            arrangement_frame(&body, index, stable)
        })
        .transpose()?;
    let ([u, v, normal], axis, group_offset) = match target {
        Some(frame) => (
            [frame.x_axis, frame.y_axis, frame.normal],
            usize::from(axis != "x"),
            add(
                add(
                    scale(frame.x_axis, length("move_x", 0.0)?),
                    scale(frame.y_axis, length("move_y", 0.0)?),
                ),
                scale(frame.normal, length("move_normal", 0.0)?),
            ),
        ),
        None => (
            WORLD_AXES,
            match axis {
                "y" => 1,
                "z" => 2,
                _ => 0,
            },
            [length("dx", 0.0)?, length("dy", 0.0)?, length("dz", 0.0)?],
        ),
    };
    let mut gap = length("gap", 10.0)?;
    let normal_offset = length("normal_offset", 0.0)?;
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

    let orient = ctx.input("orient").is_none_or(|value| truthy(Some(value)));
    let axes = [u, v, normal];
    let turns = [
        rotation_between(u, WORLD_AXES[0], [0.0; 3])?,
        rotation_between(v, WORLD_AXES[0], [0.0; 3])?,
        rotation_between(normal, WORLD_AXES[0], [0.0; 3])?,
    ];
    let mut items = Vec::with_capacity(inputs.len());
    let mut warnings = Vec::new();
    for (name, mut shape) in inputs {
        if let Some(frame) = &target {
            let (placed, warn) = seat_row_body(shape, frame, &turns[2], orient)?;
            shape = if normal_offset == 0.0 {
                placed
            } else {
                translated(&placed, scale(normal, normal_offset))?
            };
            if warn {
                warnings.push(format!(
                    "{name} has no flat support face; its rotation was preserved."
                ));
            }
        }
        let mut ends = [(0.0, 0.0); 3];
        for (extent, turn) in ends.iter_mut().zip(&turns) {
            *extent = projected_extent(&shape, turn)?;
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
    let centerline: V = std::array::from_fn(|cross| {
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
        let dimensions = if target.is_some() { 2 } else { 3 };
        let mut shift = [0.0; 3];
        for i in 0..dimensions {
            shift = add(shift, scale(axes[i], desired[i] - center(ends[i])));
        }
        outputs.insert(name, translated(&shape, add(shift, group_offset))?);
    }
    if let Some(frame) = target {
        let mut worst = 0.0_f64;
        let surface = [frame.x_bounds, frame.y_bounds];
        for shape in outputs.values() {
            for i in 0..2 {
                let (start, end) = projected_extent(shape, &turns[i])?;
                let (low, high) = surface[i];
                let middle = dot(frame.center, axes[i]);
                worst = worst.max(middle + low - start).max(end - middle - high);
            }
        }
        if worst > 1e-3 {
            warnings.push(format!(
                "The row runs {worst:.1} mm past the edge of the target face."
            ));
        }
    }
    let summary = format!(
        "Arranged {count} objects with {} mm gaps.",
        crate::align::format_g(gap)
    );
    if warnings.is_empty() {
        ctx.set_message(summary);
    } else {
        ctx.set_message(format!("{summary} {}", warnings.join(" ")));
    }
    Ok(outputs)
}

//! Body-only draft, mirror, patterns and rows from Python's modeling tools.

use crate::align::{
    Frame, arrangement_frame, face_reference_frame, rotation_between, support_face, truthy,
};
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
        if let Some(index) = support_face(&faces, Some((frame.center, frame.normal))) {
            let support = &faces[index];
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
        return arrange_pattern(ctx);
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

const PATTERNS: [&str; 6] = ["grid", "linear", "radial", "staggered", "packed", "custom"];

/// `ArrangeFeature` with a pattern (`_solve` and `arrange_on_face`): the bodies laid out on a face or the build
/// plate. A step whose inputs ask for a preview also says how the layout fits, as 0.4's `preview_parts` does.
fn arrange_pattern(ctx: &Context) -> Result<BTreeMap<String, Shape>, StepError> {
    let names: Vec<String> = ctx
        .input("bodies")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(alias)
        .collect();
    let items = names
        .iter()
        .map(|name| ctx.named_shape(name))
        .collect::<Result<Vec<_>, _>>()?;
    let text = |key: &str| ctx.input(key).and_then(Value::as_str);
    let switch = |key: &str, default: bool| ctx.input(key).map_or(default, |v| truthy(Some(v)));
    let length = |key: &str, default: f64| ctx.value(key, default, Dimension::Length);
    let angle = |key: &str| ctx.value(key, 0.0, Dimension::Angle);

    let target_ref = ctx.input("target_face").filter(|v| !v.is_null());
    let (target, target_body) = if let Some(reference) = target_ref {
        let (body, index) = ctx.face("target_face")?;
        let name = reference["__ref__"]["body"]
            .as_str()
            .unwrap_or("")
            .to_string();
        (occt::sub_face(&body, index).map_err(kernel)?, Some(name))
    } else if text("target") == Some("plate") {
        let profile: Value =
            serde_json::from_str(crate::thread::PRINTER).expect("embedded printer profile");
        let volume = &profile["build_volume"];
        let size = |axis: &str| volume[axis].as_f64().expect("build volume");
        (
            occt::plate_face(size("x"), size("y")).map_err(kernel)?,
            None,
        )
    } else {
        return Err(failed(
            "Pick the face to arrange the objects on. Click a flat face, or the round side of a cylinder, or use the build plate.",
        ));
    };
    if items.is_empty() {
        return Err(failed("Select at least one object to arrange."));
    }
    let pattern = text("pattern").unwrap_or("grid");
    if !PATTERNS.contains(&pattern) {
        return Err(failed("That arrangement pattern is not available."));
    }

    // `_options` and `_settings`.
    let gridded = ["custom", "grid", "staggered"].contains(&pattern);
    let count = |key: &str| {
        let value = ctx.input(key).and_then(Value::as_f64).unwrap_or(0.0);
        if gridded { (value as i32).max(0) } else { 0 }
    };
    let gap_y = match ctx.input("gap_y") {
        Some(value) if pattern == "custom" && !value.is_null() => {
            Some(length("gap_y", 0.0)?.max(0.0))
        }
        _ => None,
    };
    let plane = occt::plane_frame(&target).map_err(kernel)?;
    let mut frame = [0.0; 12];
    if plane.planar {
        let reference = face_reference_frame(occt::sub_face(&target, 0).map_err(kernel)?)?;
        for (i, axis) in [
            reference.center,
            reference.x_axis,
            reference.y_axis,
            reference.normal,
        ]
        .iter()
        .enumerate()
        {
            frame[i * 3..i * 3 + 3].copy_from_slice(axis);
        }
    }
    let spec = occt::ArrangeSpec {
        pattern: pattern.into(),
        fixed: text("spacing") == Some("fixed"),
        gap: length("gap", 5.0)?.max(0.0),
        gap_y: gap_y.unwrap_or(0.0),
        has_gap_y: gap_y.is_some(),
        direction: angle("direction")?,
        align: text("align").unwrap_or("center").into(),
        rows: count("rows"),
        columns: count("columns"),
        fill_columns: text("fill") == Some("columns"),
        evenly: text("distribute") == Some("evenly"),
        rotation: angle("rotation")?,
        auto_rotate: switch("auto_rotate", false),
        radius: length("radius", 0.0)?.max(0.0),
        start_angle: angle("start_angle")?,
        face_center: switch("face_center", false),
        contain: switch("contain", true),
        manual: text("boundary") == Some("manual"),
        margin: length("margin", 0.0)?,
        insets: [
            length("inset_left", 0.0)?,
            length("inset_right", 0.0)?,
            length("inset_top", 0.0)?,
            length("inset_bottom", 0.0)?,
        ],
        avoid_holes: switch("avoid_holes", true),
        avoid_bodies: switch("avoid_bodies", true),
        orient: switch("orient", true),
        standoff: length("normal_offset", 0.0)?,
        offset: [
            length("move_x", 0.0)?,
            length("move_y", 0.0)?,
            length("move_normal", 0.0)?,
        ],
        suggest: switch("preview", false),
        frame,
    };

    // Each body's face to stand on: the one picked for it, else the largest flat face (nearest the target's plane
    // when that is flat).
    let mut contacts = BTreeMap::new();
    if let Some(Value::Array(refs)) = ctx.input("contact_faces") {
        for (reference, (_, index)) in refs.iter().zip(ctx.faces("contact_faces")?) {
            contacts.insert(
                reference["__ref__"]["body"]
                    .as_str()
                    .unwrap_or("")
                    .to_string(),
                index as i32,
            );
        }
    }
    let target_plane = plane.planar.then_some((plane.center, plane.normal));
    let mut supports = Vec::with_capacity(items.len());
    let mut shapes = occt::shapes_new();
    for (name, shape) in names.iter().zip(&items) {
        occt::shapes_push(shapes.pin_mut(), shape);
        supports.push(match contacts.get(name) {
            Some(index) => *index,
            None if spec.orient => {
                let faces = occt::faces(shape).map_err(kernel)?;
                support_face(&faces, target_plane).map_or(-1, |index| index as i32)
            }
            None => -1,
        });
    }
    let mut edges = occt::shapes_new();
    for (body, index) in ctx.edges("boundary_edges")? {
        let edge = occt::sub_edge(&body, index).map_err(kernel)?;
        occt::shapes_push(edges.pin_mut(), &edge);
    }
    // Other bodies to keep clear of: only those bound to the step, as in 0.4.
    let mut others: Vec<_> = ctx
        .bodies()
        .filter(|(name, _)| {
            !name.starts_with("__") && Some(*name) != target_body.as_ref() && !names.contains(name)
        })
        .collect();
    others.sort_by(|a, b| a.0.cmp(b.0));
    let mut other_shapes = occt::shapes_new();
    for (_, shape) in others {
        occt::shapes_push(other_shapes.pin_mut(), shape);
    }

    let out = occt::arrange_face(&shapes, &supports, &target, &edges, &other_shapes, &spec)
        .map_err(kernel)?;
    let ordered: Vec<&str> = out
        .order
        .iter()
        .map(|&i| names[i as usize].as_str())
        .collect();
    const WORDS: [&str; 3] = [
        "is outside the boundary",
        "overlaps",
        "is closer than the gap to",
    ];
    let problems: Vec<(usize, usize, Option<usize>)> = out
        .problems
        .chunks(3)
        .map(|p| (p[0] as usize, p[1] as usize, usize::try_from(p[2]).ok()))
        .collect();
    let reasons: Vec<(&str, String)> = problems
        .iter()
        .map(|&(slot, kind, other)| {
            let words = WORDS[kind];
            let reason = match other {
                Some(other) => format!("{words} {}", ordered[other]),
                None => words.to_string(),
            };
            (ordered[slot], reason)
        })
        .collect();
    let summary = if problems.is_empty() {
        let n = ordered.len();
        let detail = match pattern {
            "radial" => format!("ring r {:.1} mm", out.radius),
            "packed" => "packed".into(),
            "linear" => "in a row".into(),
            _ => format!(
                "{} × {} {}",
                out.rows,
                out.columns,
                if pattern == "staggered" {
                    "staggered"
                } else {
                    "grid"
                }
            ),
        };
        format!(
            "{n} {} · {detail} · {:.2} mm gaps",
            if n == 1 { "object" } else { "objects" },
            out.gap
        )
    } else {
        let collide = problems.iter().filter(|p| p.1 != 0).count();
        let outside = problems.len() - collide;
        let mut bits = Vec::new();
        if collide > 0 {
            bits.push(format!(
                "{collide} {}",
                if collide == 1 {
                    "object collides"
                } else {
                    "objects collide"
                }
            ));
        }
        if outside > 0 {
            bits.push(format!("{outside} outside the boundary"));
        }
        bits.join(" · ")
    };
    let mut warnings: Vec<String> = out
        .unseated
        .iter()
        .map(|&slot| {
            format!(
                "{} has no flat face to stand on; its turn was kept.",
                ordered[slot as usize]
            )
        })
        .collect();
    if !problems.is_empty() {
        // Something upstream changed since this was applied. Placing the bodies anyway keeps the model building; the
        // warning says why it needs attention rather than leaving an overlap unexplained.
        warnings.push(format!(
            "The arrangement no longer fits: {summary}. Edit the arrangement to choose a layout that does."
        ));
    }
    let message = format!("Arranged {summary}.");
    ctx.set_message(if warnings.is_empty() {
        message
    } else {
        format!("{message} {}", warnings.join(" "))
    });

    if spec.suggest {
        let patch_value = |key: &str, value: &str| match value {
            "true" => Value::Bool(true),
            "false" => Value::Bool(false),
            _ if key == "rows" || key == "columns" => {
                value.parse::<i64>().map_or(Value::Null, Value::from)
            }
            _ => value
                .parse::<f64>()
                .map_or_else(|_| Value::from(value), Value::from),
        };
        let suggestions: Vec<Value> = out
            .offers
            .iter()
            .map(|offer| {
                let patch: serde_json::Map<String, Value> = offer
                    .keys
                    .iter()
                    .zip(&offer.values)
                    .map(|(k, v)| (k.clone(), patch_value(k, v)))
                    .collect();
                serde_json::json!({ "label": offer.label, "patch": patch })
            })
            .collect();
        let mut rings = Vec::new();
        let mut points = out.boundary.chunks(3);
        for &count in &out.boundary_rings {
            rings.push(
                points
                    .by_ref()
                    .take(count as usize)
                    .map(|p| [p[0], p[1], p[2]])
                    .collect::<Vec<_>>(),
            );
        }
        ctx.set_note(serde_json::json!({
            "fits": problems.is_empty(),
            "summary": summary,
            "problems": reasons.iter().map(|(name, reason)| serde_json::json!({ "name": name, "reason": reason })).collect::<Vec<_>>(),
            "suggestions": suggestions,
            "gap": out.gap, "rows": out.rows, "columns": out.columns, "radius": out.radius,
            "order": ordered,
            "axes": [&out.axes[0..3], &out.axes[3..6], &out.axes[6..9]],
            "boundary": rings,
        }));
    }
    Ok(out
        .shapes
        .into_iter()
        .zip(&ordered)
        .map(|(body, name)| (name.to_string(), Rc::new(body.shape)))
        .collect())
}

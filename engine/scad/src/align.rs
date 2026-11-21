//! Placing one body against another, as the Python app's `AlignFeature`
//! (`simplecad/kernel/operations.py`) and `simplecad/kernel/align.py` do it:
//! the same vector arithmetic in the same order, and every `gp_Trsf` composed
//! in OCCT itself, so a placement re-solves to the same bits.

use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, failed};
use crate::vector::{V, add, cross, dot, length, perpendicular, scale, sub, unit};
use occt::{CylinderData, PlaneFrame, Trsf, UniquePtr};
use serde_json::Value;
use std::rc::Rc;

/// An extent along one axis: low, high.
type Span = (f64, f64);

const PARALLEL_TOLERANCE: f64 = 1e-9;
const KERNEL: &str = "The align operation did not succeed on this geometry. Try adjusting the inputs slightly, or undo and take a different approach.";

fn kernel(_: occt::Exception) -> StepError {
    failed(KERNEL)
}

fn identity() -> UniquePtr<Trsf> {
    occt::trsf_identity()
}

fn translation(by: V) -> UniquePtr<Trsf> {
    occt::trsf_translation(&by)
}

fn then(first: &Trsf, second: &Trsf) -> UniquePtr<Trsf> {
    occt::trsf_multiplied(second, first)
}

fn point(trsf: &Trsf, p: V) -> V {
    let out = occt::trsf_point(trsf, &p);
    [out[0], out[1], out[2]]
}

/// `rotation_between`: turns `source` onto `target` about `pivot`.
pub(crate) fn rotation_between(
    source: V,
    target: V,
    pivot: V,
) -> Result<UniquePtr<Trsf>, StepError> {
    let source = unit(source)?;
    let target = unit(target)?;
    let mut axis = cross(source, target);
    let sine = length(axis);
    let cosine = dot(source, target);
    let angle;
    if sine < PARALLEL_TOLERANCE {
        if cosine > 0.0 {
            return Ok(identity());
        }
        axis = perpendicular(source)?;
        angle = std::f64::consts::PI;
    } else {
        axis = unit(axis)?;
        angle = sine.atan2(cosine);
    }
    occt::trsf_rotation(&pivot, &axis, angle).map_err(kernel)
}

/// `spun`: `transform`, then a right-handed turn of `degrees` about `axis` at `pivot`.
fn spun(
    transform: UniquePtr<Trsf>,
    axis: V,
    pivot: V,
    degrees: f64,
) -> Result<UniquePtr<Trsf>, StepError> {
    if degrees == 0.0 {
        return Ok(transform);
    }
    let spin = occt::trsf_rotation(&pivot, &unit(axis)?, degrees.to_radians()).map_err(kernel)?;
    Ok(then(&transform, &spin))
}

#[derive(Clone, Copy)]
struct Planar {
    center: V,
    normal: V,
}

/// `seating_rotation`.
fn seating_rotation(
    moving: Planar,
    target_normal: V,
    flip: bool,
    rotation: f64,
    seat: bool,
) -> Result<UniquePtr<Trsf>, StepError> {
    let wanted = if flip {
        target_normal
    } else {
        scale(target_normal, -1.0)
    };
    let rotate = if seat {
        rotation_between(moving.normal, wanted, moving.center)?
    } else {
        translation([0.0, 0.0, 0.0])
    };
    spun(rotate, target_normal, moving.center, rotation)
}

/// A face with its planar reading, if it has one (`analyse_plane`).
struct Face {
    shape: UniquePtr<occt::Shape>,
    plane: PlaneFrame,
}

impl Face {
    fn read(shape: &occt::Shape, index: u32) -> Result<Self, StepError> {
        let shape = occt::sub_face(shape, index).map_err(kernel)?;
        let plane = occt::plane_frame(&shape).map_err(kernel)?;
        Ok(Self { shape, plane })
    }

    fn planar(&self) -> Option<Planar> {
        self.plane.planar.then_some(Planar {
            center: self.plane.center,
            normal: self.plane.normal,
        })
    }
}

#[derive(Clone, Copy)]
pub(crate) struct Frame {
    pub(crate) center: V,
    pub(crate) normal: V,
    pub(crate) x_axis: V,
    pub(crate) y_axis: V,
    pub(crate) x_bounds: (f64, f64),
    pub(crate) y_bounds: (f64, f64),
}

impl Frame {
    fn point(&self, x: f64, y: f64, normal: f64) -> V {
        std::array::from_fn(|i| {
            self.center[i] + self.x_axis[i] * x + self.y_axis[i] * y + self.normal[i] * normal
        })
    }
}

/// `_projected_bounds`: the shape's extent along `axis`, from `center`.
pub(crate) fn projected_bounds(
    shape: &occt::Shape,
    axis: V,
    center: V,
) -> Result<(f64, f64), StepError> {
    let rotate = rotation_between(axis, [1.0, 0.0, 0.0], center)?;
    let turned = occt::transform_shape(shape, &rotate, false).map_err(kernel)?;
    let extent = occt::x_extent(&turned).map_err(kernel)?;
    let p = point(&rotate, center);
    Ok((extent[0] - p[0], extent[1] - p[0]))
}

fn flat_target() -> StepError {
    failed("Placement needs a flat target face. Select a planar face on the receiving object.")
}

/// `planar_frame`: the face's own X/Y frame and its UV extent about its centre.
fn planar_frame(face: &Face) -> Result<Frame, StepError> {
    let p = &face.plane;
    if !p.planar {
        return Err(flat_target());
    }
    let x_axis = unit(p.x_dir)?;
    let y_axis = unit(cross(p.normal, x_axis))?;
    let center_delta = sub(p.center, p.origin);
    let center_x = dot(center_delta, x_axis);
    let center_y = dot(center_delta, y_axis);
    let [u_min, u_max, mut v_min, mut v_max] = p.uv;
    if dot(p.y_dir, y_axis) < 0.0 {
        (v_min, v_max) = (-v_max, -v_min);
    }
    Ok(Frame {
        center: p.center,
        normal: p.normal,
        x_axis,
        y_axis,
        x_bounds: (u_min - center_x, u_max - center_x),
        y_bounds: (v_min - center_y, v_max - center_y),
    })
}

/// `reference_frame`: V nearest world up (world Y for a level face), U right-handed.
fn reference_frame(face: &Face) -> Result<Frame, StepError> {
    let legacy = planar_frame(face)?;
    let (u_axis, v_axis) = reference_axes(&legacy)?;
    Ok(Frame {
        x_axis: u_axis,
        y_axis: v_axis,
        x_bounds: projected_bounds(&face.shape, u_axis, legacy.center)?,
        y_bounds: projected_bounds(&face.shape, v_axis, legacy.center)?,
        ..legacy
    })
}

fn reference_axes(frame: &Frame) -> Result<(V, V), StepError> {
    let candidates = [frame.x_axis, frame.y_axis];
    let mut guide = [0.0, 0.0, 1.0];
    let reach = |guide: V| candidates.iter().map(move |a| dot(*a, guide).abs());
    if reach(guide).fold(f64::NEG_INFINITY, f64::max) < 0.25 {
        guide = [0.0, 1.0, 0.0];
    }
    // `max` keeps the first of equals.
    let mut v_axis = candidates[0];
    if dot(candidates[1], guide).abs() > dot(v_axis, guide).abs() {
        v_axis = candidates[1];
    }
    if dot(v_axis, guide) < 0.0 {
        v_axis = scale(v_axis, -1.0);
    }
    Ok((unit(cross(v_axis, frame.normal))?, v_axis))
}

/// `view_frame` chooses signed, authored face axes from the projected view.
/// Quantizing the axes keeps edge gaps and the saved recipe tied to the face.
fn view_axes(base: &Frame, direction: V, up: V) -> Option<(V, V)> {
    let normalize = |v| {
        let magnitude = length(v);
        (magnitude.is_finite() && magnitude >= 1e-12).then(|| scale(v, 1.0 / magnitude))
    };
    let normal = base.normal;
    let up = normalize(up)?;
    let right = normalize(cross(normalize(direction)?, up))?;
    let flatten = |v| sub(v, scale(normal, dot(v, normal)));
    let mut screen_up = flatten(up);
    let mut screen_right = flatten(right);
    if length(screen_up) < 1e-5 {
        screen_up = cross(normal, screen_right);
    }
    if length(screen_right) < 1e-5 {
        screen_right = cross(screen_up, normal);
    }
    if length(screen_up) < 1e-5 || length(screen_right) < 1e-5 {
        return None;
    }
    let screen_up = normalize(screen_up)?;
    let screen_right = normalize(screen_right)?;
    let axes = [
        base.x_axis,
        scale(base.x_axis, -1.0),
        base.y_axis,
        scale(base.y_axis, -1.0),
    ];
    let nearest = |axis, best, guide| {
        // Python's max keeps the first of equals.
        if dot(axis, guide) > dot(best, guide) {
            axis
        } else {
            best
        }
    };
    let v = axes
        .into_iter()
        .fold(axes[0], |best, axis| nearest(axis, best, screen_up));
    let mut u = axes
        .into_iter()
        .filter(|axis| dot(*axis, v).abs() < 0.5)
        .reduce(|best, axis| nearest(axis, best, screen_right))?;
    if dot(cross(u, v), normal) < 0.0 {
        u = scale(u, -1.0);
    }
    Some((u, v))
}

fn numbers(value: &Value) -> Option<Vec<f64>> {
    value
        .as_array()
        .map(|l| l.iter().map(|v| v.as_f64().unwrap_or(f64::NAN)).collect())
}

fn vector(value: Option<&Value>) -> Result<Option<V>, StepError> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(v) => match numbers(v) {
            Some(n) if n.len() == 3 => Ok(Some([n[0], n[1], n[2]])),
            _ => Err(failed(KERNEL)),
        },
    }
}

/// `frame_from_basis`: a saved frame rebuilt on `face`.
fn frame_from_basis(
    face: &Face,
    basis: &Value,
    world_u: Option<V>,
    world_normal: Option<V>,
) -> Result<Frame, StepError> {
    let base = planar_frame(face)?;
    let invalid = || failed("The saved face orientation is invalid.");
    let entries = numbers(basis)
        .filter(|e| e.len() == 4)
        .ok_or_else(invalid)?;
    let (u, v);
    if entries.iter().all(|e| [0.0, 1.0, -1.0].contains(e)) {
        let axes = [
            ((1.0, 0.0), base.x_axis),
            ((-1.0, 0.0), scale(base.x_axis, -1.0)),
            ((0.0, 1.0), base.y_axis),
            ((0.0, -1.0), scale(base.y_axis, -1.0)),
        ];
        let find = |key: (f64, f64)| axes.iter().find(|(k, _)| *k == key).map(|(_, a)| *a);
        let mut found_u = find((entries[0], entries[1])).ok_or_else(invalid)?;
        let mut found_v = find((entries[2], entries[3])).ok_or_else(invalid)?;
        if let (Some(world_u), Some(world_normal)) = (world_u, world_normal)
            && dot(base.normal, unit(world_normal)?) > 0.999
        {
            let handed = if dot(cross(found_u, found_v), base.normal) >= 0.0 {
                1.0
            } else {
                -1.0
            };
            let authored = unit(world_u)?;
            let mut best = axes[0].1;
            for (_, axis) in &axes[1..] {
                if dot(*axis, authored) > dot(best, authored) {
                    best = *axis;
                }
            }
            found_u = best;
            found_v = scale(unit(cross(base.normal, found_u))?, handed);
        }
        (u, v) = (found_u, found_v);
    } else {
        u = unit(add(
            scale(base.x_axis, entries[0]),
            scale(base.y_axis, entries[1]),
        ))?;
        v = unit(add(
            scale(base.x_axis, entries[2]),
            scale(base.y_axis, entries[3]),
        ))?;
    }
    if dot(u, v).abs() > 1e-5 {
        return Err(failed("The saved face orientation is not perpendicular."));
    }
    Ok(Frame {
        x_axis: u,
        y_axis: v,
        x_bounds: projected_bounds(&face.shape, u, base.center)?,
        y_bounds: projected_bounds(&face.shape, v, base.center)?,
        ..base
    })
}

/// `moving_bounds_for_frame`: the turned moving part's extent in the target's frame.
fn moving_bounds(
    frame: &Frame,
    body: &occt::Shape,
    moving: Option<Planar>,
    operation: &str,
    flip: bool,
    rotation: f64,
) -> Result<(Span, Span), StepError> {
    let moving = moving.ok_or_else(|| failed("Edge distances need a flat mounting face."))?;
    let rotate = seating_rotation(moving, frame.normal, flip, rotation, operation == "stack")?;
    let outline = occt::transform_shape(body, &rotate, false).map_err(kernel)?;
    let center = point(&rotate, moving.center);
    Ok((
        projected_bounds(&outline, frame.x_axis, center)?,
        projected_bounds(&outline, frame.y_axis, center)?,
    ))
}

/// `anchor_coordinates` then `anchor_point`.
fn anchor_point(frame: &Frame, preset: &str, normal: f64) -> Result<V, StepError> {
    const PRESETS: [&str; 9] = [
        "top_left",
        "top",
        "top_right",
        "left",
        "center",
        "right",
        "bottom_left",
        "bottom",
        "bottom_right",
    ];
    if !PRESETS.contains(&preset) {
        return Err(failed(format!("Unknown placement anchor: '{preset}'.")));
    }
    let words: Vec<&str> = preset.split('_').collect();
    let u = match words.iter().find(|w| ["left", "right"].contains(w)) {
        Some(&"left") => frame.x_bounds.0,
        Some(_) => frame.x_bounds.1,
        None => 0.0,
    };
    let v = match words.iter().find(|w| ["bottom", "top"].contains(w)) {
        Some(&"bottom") => frame.y_bounds.0,
        Some(_) => frame.y_bounds.1,
        None => 0.0,
    };
    Ok(frame.point(u, v, normal))
}

/// `_anchored`: an edge or centre offset; positive edge values point inward.
fn anchored(bounds: (f64, f64), anchor: &str, value: f64) -> f64 {
    match anchor {
        "left" | "bottom" => bounds.0 + value,
        "right" | "top" => bounds.1 - value,
        _ => value,
    }
}

/// Python's `format(value, "g")`.
pub fn format_g(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() { "-0" } else { "0" }.into();
    }
    if !value.is_finite() {
        return if value.is_nan() {
            "nan".into()
        } else if value > 0.0 {
            "inf".into()
        } else {
            "-inf".into()
        };
    }
    let strip = |s: &str| -> String {
        if s.contains('.') {
            s.trim_end_matches('0').trim_end_matches('.').to_string()
        } else {
            s.to_string()
        }
    };
    let scientific = format!("{value:.5e}");
    let (mantissa, exponent) = scientific.split_once('e').unwrap_or((&scientific, "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    if !(-4..6).contains(&exponent) {
        let sign = if exponent < 0 { '-' } else { '+' };
        return format!("{}e{sign}{:02}", strip(mantissa), exponent.abs());
    }
    strip(&format!("{value:.*}", (5 - exponent) as usize))
}

fn offset_text(offset: f64) -> String {
    if offset != 0.0 {
        format!(", offset {} mm", format_g(offset))
    } else {
        String::new()
    }
}

/// Python's truthiness of a saved input.
pub(crate) fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|x| x != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `str()` of a saved input, with `default` when it is missing.
fn text(value: Option<&Value>, default: &str) -> String {
    match value {
        None => default.to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Null) => "None".into(),
        Some(Value::Bool(b)) => if *b { "True" } else { "False" }.into(),
        Some(other) => other.to_string(),
    }
}

/// An anchor input: `None` when missing or null, as `inputs.get` gives it.
fn anchor(value: Option<&Value>) -> Option<String> {
    match value {
        None | Some(Value::Null) => None,
        other => Some(text(other, "")),
    }
}

/// A solved placement: the transform, and the sentence describing it.
pub struct Placement {
    pub transform: UniquePtr<Trsf>,
    pub description: String,
}

/// `AlignFeature.execute`'s solve: where the step puts its body.
pub fn solve(ctx: &Context) -> Result<(Shape, String, Placement), StepError> {
    let (moving, moving_index) = ctx.face("moving_face")?;
    let (target, target_index) = ctx.face("target_face")?;
    solve_faces(
        ctx,
        Face::read(&moving, moving_index)?,
        Face::read(&target, target_index)?,
    )
}

pub(crate) fn seat_joint(
    moving: (&occt::Shape, u32),
    target: (&occt::Shape, u32),
    x: f64,
    y: f64,
) -> Result<UniquePtr<Trsf>, StepError> {
    let moving = Face::read(moving.0, moving.1)?;
    let target = Face::read(target.0, target.1)?;
    let moving = moving
        .planar()
        .ok_or_else(|| failed("Clips need planar faces."))?;
    let frame = planar_frame(&target)?;
    let seat = seating_rotation(moving, frame.normal, false, 0.0, true)?;
    let destination = frame.point(
        anchored(frame.x_bounds, "center", x),
        anchored(frame.y_bounds, "center", y),
        0.0,
    );
    Ok(then(&seat, &translation(sub(destination, moving.center))))
}

pub(crate) fn joint_anchors(
    target: (&occt::Shape, u32),
    anchors: &[[f64; 2]],
) -> Result<Vec<f64>, StepError> {
    let target = Face::read(target.0, target.1)?;
    let frame = planar_frame(&target)?;
    Ok(anchors
        .iter()
        .flat_map(|[x, y]| frame.point(*x, *y, 0.0))
        .collect())
}

fn anchor_transform(
    moving: &Face,
    target: &Face,
    [from, to]: [&str; 2],
    [u, v, offset]: V,
    flip: bool,
    rotation: f64,
    seat: bool,
) -> Result<UniquePtr<Trsf>, StepError> {
    let moving_frame = reference_frame(moving)?;
    let target_frame = reference_frame(target)?;
    let source = anchor_point(&moving_frame, from, 0.0)?;
    let destination = add(
        anchor_point(&target_frame, to, offset)?,
        add(scale(target_frame.x_axis, u), scale(target_frame.y_axis, v)),
    );
    let moving = moving.planar().ok_or_else(flat_target)?;
    let rotate = seating_rotation(moving, target.plane.normal, flip, rotation, seat)?;
    let shift = translation(sub(destination, point(&rotate, source)));
    Ok(then(&rotate, &shift))
}

pub(crate) fn seat_section(
    replacement: (&occt::Shape, u32),
    target: (&occt::Shape, u32),
    anchors: [&str; 2],
    offsets: [f64; 2],
) -> Result<UniquePtr<Trsf>, StepError> {
    let moving = Face::read(replacement.0, replacement.1)?;
    let target = Face::read(target.0, target.1)?;
    if !moving.plane.planar || !target.plane.planar {
        return Err(failed(
            "Section Replace needs one flat face on each object.",
        ));
    }
    anchor_transform(
        &moving,
        &target,
        anchors,
        [offsets[0], offsets[1], 0.0],
        false,
        0.0,
        true,
    )
}

pub(crate) fn arrangement_frame(
    body: &occt::Shape,
    index: u32,
    stable: bool,
) -> Result<Frame, StepError> {
    let face = Face::read(body, index)?;
    if stable {
        reference_frame(&face)
    } else {
        planar_frame(&face)
    }
}

fn solve_faces(
    ctx: &Context,
    moving_face: Face,
    target_face: Face,
) -> Result<(Shape, String, Placement), StepError> {
    let body = ctx.shape("body")?;
    let value = |key: &str| ctx.value(key, 0.0, Dimension::Length);
    let input = |key: &str| ctx.input(key);
    let operation = match input("operation") {
        Some(op) if truthy(Some(op)) => Some(text(Some(op), "")),
        _ => None,
    };
    let offset = value("offset")?;
    let flip = truthy(input("flip"));
    let (x, y) = (value("x")?, value("y")?);
    let x_anchor = text(input("x_anchor"), "center");
    let y_anchor = text(input("y_anchor"), "center");
    let moving_anchor = anchor(input("moving_anchor"));
    let target_anchor = anchor(input("target_anchor"));
    let u = input("u").map(|_| value("u")).transpose()?;
    let v = input("v").map(|_| value("v")).transpose()?;
    let placement_mode = text(input("placement_mode"), "anchor");
    let horizontal = text(input("horizontal"), "center");
    let vertical = text(input("vertical"), "center");
    let horizontal_gap = value("horizontal_gap")?;
    let vertical_gap = value("vertical_gap")?;
    let rotation = ctx.value("rotation", 0.0, Dimension::Angle)?;

    // `describe_face`: planar first, else cylindrical.
    let cylinder = |f: &Face| -> Result<Option<CylinderData>, StepError> {
        if f.plane.planar {
            return Ok(None);
        }
        let c = occt::cylinder_data(&f.shape).map_err(kernel)?;
        Ok(c.cylindrical.then_some(c))
    };
    let moving_cylinder = cylinder(&moving_face)?;
    let target_cylinder = cylinder(&target_face)?;
    let moving = moving_face.planar();
    let target = target_face.planar();
    let operation = operation.unwrap_or_else(|| {
        match (
            moving.is_some() && target.is_some(),
            moving_cylinder.is_some() && target_cylinder.is_some(),
        ) {
            (true, _) => "stack",
            (_, true) => "concentric",
            _ => "align",
        }
        .into()
    });
    if (moving.is_none() && moving_cylinder.is_none())
        || (target.is_none() && target_cylinder.is_none())
    {
        return Err(failed(
            "Those faces cannot be aligned. Pick a flat face or a cylindrical face on each part.",
        ));
    }
    let name = match input("body") {
        Some(b) => crate::rebuild::alias(b),
        None => String::new(),
    };
    let done = |transform, description: String| {
        Ok((
            body.clone(),
            name.clone(),
            Placement {
                transform,
                description,
            },
        ))
    };

    if operation == "concentric" {
        let (Some(m), Some(t)) = (moving_cylinder, target_cylinder) else {
            return Err(failed(
                "Concentric needs a cylindrical face on both parts. Select a shaft and a hole.",
            ));
        };
        let wanted = if flip {
            scale(t.direction, -1.0)
        } else {
            t.direction
        };
        let pivot = m.origin;
        let rotate = spun(
            rotation_between(m.direction, wanted, pivot)?,
            wanted,
            pivot,
            rotation,
        )?;
        let axis = unit(wanted)?;
        let delta = sub(t.origin, m.origin);
        let perpendicular = sub(delta, scale(axis, dot(delta, axis)));
        let along = scale(axis, dot(delta, axis));
        let shift = translation(add(add(perpendicular, along), scale(axis, offset)));
        let description = format!(
            "Aligned axes (⌀{:.2} into ⌀{:.2}).",
            m.radius * 2.0,
            t.radius * 2.0
        );
        return done(then(&rotate, &shift), description);
    }
    let (Some(moving), Some(target)) = (moving, target) else {
        return Err(failed(
            "Stack needs a flat face on both parts. Select a planar face on each, or use Concentric for round features.",
        ));
    };
    if placement_mode == "edge_gap" {
        let basis = match input("target_basis") {
            None | Some(Value::Null) => {
                return Err(failed(
                    "This placement has lost the face orientation it was made with. Re-place the part with Align & Stack.",
                ));
            }
            Some(b) => b,
        };
        let frame = frame_from_basis(
            &target_face,
            basis,
            vector(input("target_u"))?,
            vector(input("target_normal"))?,
        )?;
        if !["left", "center", "right"].contains(&horizontal.as_str())
            || !["bottom", "center", "top"].contains(&vertical.as_str())
        {
            return Err(failed("Choose a valid face side for placement."));
        }
        let (moving_u, moving_v) =
            moving_bounds(&frame, &body, Some(moving), &operation, flip, rotation)?;
        let coordinate = |target: (f64, f64), object: (f64, f64), side: &str, gap: f64| match side {
            "left" | "bottom" => target.0 + gap - object.0,
            "right" | "top" => target.1 - gap - object.1,
            _ => (target.0 + target.1) / 2.0 - (object.0 + object.1) / 2.0 + gap,
        };
        let cu = coordinate(frame.x_bounds, moving_u, &horizontal, horizontal_gap);
        let cv = coordinate(frame.y_bounds, moving_v, &vertical, vertical_gap);
        let rotate = seating_rotation(moving, target.normal, flip, rotation, operation == "stack")?;
        let destination = frame.point(cu, cv, offset);
        let shift = translation(sub(destination, moving.center));
        return done(then(&rotate, &shift), "Placed from face edges.".into());
    }
    if moving_anchor.is_some() || target_anchor.is_some() || u.is_some() || v.is_some() {
        let pick = |a: &Option<String>| match a.as_deref() {
            Some(s) if !s.is_empty() => s.to_string(),
            _ => "center".to_string(),
        };
        let (from, to) = (pick(&moving_anchor), pick(&target_anchor));
        let transform = anchor_transform(
            &moving_face,
            &target_face,
            [&from, &to],
            [u.unwrap_or(0.0), v.unwrap_or(0.0), offset],
            flip,
            rotation,
            operation != "center",
        )?;
        let description = format!("Placed {from} anchor on {to}{}.", offset_text(offset));
        return done(transform, description);
    }
    // Saved before anchors existed: edge distances in the face's own frame.
    let frame = planar_frame(&target_face)?;
    let destination = frame.point(
        anchored(frame.x_bounds, &x_anchor, x),
        anchored(frame.y_bounds, &y_anchor, y),
        offset,
    );
    if operation == "center" {
        let spin = seating_rotation(moving, target.normal, false, rotation, false)?;
        let shift = translation(sub(destination, moving.center));
        return done(then(&spin, &shift), "Centred on the target face.".into());
    }
    let rotate = seating_rotation(moving, target.normal, flip, rotation, true)?;
    let shift = translation(sub(destination, moving.center));
    let gap = offset_text(offset);
    let description = if flip {
        format!("Stacked, flipped{gap}.")
    } else {
        format!("Stacked face to face{gap}.")
    };
    done(then(&rotate, &shift), description)
}

/// The current body and the face the user picked on it.
pub struct PickedFace<'a> {
    pub name: &'a str,
    pub shape: &'a occt::Shape,
    pub index: u32,
}

fn store_target_frame(
    inputs: &mut Value,
    target: &Face,
    view: Option<(V, V)>,
) -> Result<(), StepError> {
    let base = planar_frame(target)?;
    let (u_axis, v_axis) = match view.and_then(|(direction, up)| view_axes(&base, direction, up)) {
        Some(axes) => axes,
        None => reference_axes(&base)?,
    };
    let snapped = |value: f64| {
        [0.0, 1.0, -1.0]
            .into_iter()
            .find(|v| (value - v).abs() <= 1e-9)
            .unwrap_or(value)
    };
    inputs["target_basis"] = serde_json::json!([
        snapped(dot(u_axis, base.x_axis)),
        snapped(dot(u_axis, base.y_axis)),
        snapped(dot(v_axis, base.x_axis)),
        snapped(dot(v_axis, base.y_axis)),
    ]);
    inputs["target_u"] = serde_json::json!(u_axis);
    inputs["target_normal"] = serde_json::json!(base.normal);
    Ok(())
}

pub(crate) fn reframe_target(
    inputs: &mut Value,
    shape: &occt::Shape,
    face: u32,
) -> Result<(), String> {
    if inputs["placement_mode"] == "edge_gap" && inputs["operation"] != "concentric" {
        let target = Face::read(shape, face).map_err(|error| error.message)?;
        store_target_frame(inputs, &target, None).map_err(|error| error.message)?;
    }
    Ok(())
}

/// Solve a new placement directly on picked faces and record its editable recipe.
pub fn place(
    model: &mut crate::document::Model,
    moving: PickedFace<'_>,
    target: PickedFace<'_>,
    mut inputs: Value,
) -> Result<UniquePtr<occt::Shape>, String> {
    if moving.name == target.name {
        return Err("Pick a face on each of two different parts.".into());
    }
    let error = |e: StepError| e.message;
    let moving_face = Face::read(moving.shape, moving.index).map_err(error)?;
    let target_face = Face::read(target.shape, target.index).map_err(error)?;
    let view = inputs.as_object_mut().and_then(|inputs| {
        let direction = inputs.shift_remove("view_direction");
        let up = inputs.shift_remove("view_up");
        vector(direction.as_ref())
            .ok()
            .flatten()
            .zip(vector(up.as_ref()).ok().flatten())
    });
    if inputs["placement_mode"] == "edge_gap" {
        store_target_frame(&mut inputs, &target_face, view).map_err(error)?;
    }
    let reference = |picked: &PickedFace<'_>| -> Result<Value, String> {
        let f =
            occt::face_fingerprint(picked.shape, picked.index).map_err(|e| e.what().to_string())?;
        let direction = f.has_direction.then_some(f.direction);
        let radius = f.has_radius.then_some(f.radius);
        Ok(
            serde_json::json!({ "__ref__": model.face_ref(picked.name, picked.index, serde_json::json!({
            "kind": "face", "geometry": f.geometry, "measure": f.measure, "center": f.center,
            "direction": direction, "radius": radius, "neighbours": [],
        })) }),
        )
    };
    inputs["body"] = serde_json::json!({ "__body__": moving.name });
    inputs["moving_face"] = reference(&moving)?;
    inputs["target_face"] = reference(&target)?;
    let feature = serde_json::json!({ "inputs": inputs });
    let shape = occt::transform_shape(moving.shape, &identity(), false)
        .map_err(|e| e.what().to_string())?;
    let parameters = std::collections::HashMap::new();
    let context = Context::placement(&feature, moving.name, Rc::new(shape), &parameters);
    let (body, _, placement) = solve_faces(&context, moving_face, target_face).map_err(error)?;
    let placed = occt::transform_shape(&body, &placement.transform, true)
        .map_err(|e| e.what().to_string())?;
    let brep = occt::write_brep(&placed).map_err(|e| e.what().to_string())?;
    model.record_align(
        moving.name,
        target.name,
        inputs,
        brep,
        &placement.description,
    )?;
    Ok(placed)
}

/// The step itself: the body placed, geometry copied (`occ.transformed`).
pub fn execute(ctx: &Context) -> Result<(String, Shape), StepError> {
    let (body, name, placement) = solve(ctx)?;
    // ponytail: the overlap warning (`interference_volume`) is only a hint in
    // the window, never saved; it comes with the app's Stack & Place panel.
    ctx.set_message(placement.description);
    let placed = occt::transform_shape(&body, &placement.transform, true).map_err(kernel)?;
    Ok((name, Rc::new(placed)))
}

#[cfg(test)]
mod tests {
    use super::{PickedFace, format_g, place};
    use crate::document::Model;
    use serde_json::json;

    #[test]
    fn numbers_read_as_python_formats_them_with_g() {
        for (value, python) in [
            (2.0, "2"),
            (-6.0, "-6"),
            (0.5, "0.5"),
            (1.25, "1.25"),
            (123456.0, "123456"),
            (1234567.0, "1.23457e+06"),
            (0.0001, "0.0001"),
            (0.00001234, "1.234e-05"),
            (2.0 / 3.0, "0.666667"),
            (-0.0, "-0"),
        ] {
            assert_eq!(format_g(value), python, "{value}");
        }
    }

    #[test]
    fn stacking_uses_the_views_nearest_face_axes_and_saves_only_the_basis() {
        let target = occt::make_box(40.0, 30.0, 20.0).unwrap();
        let moving = occt::make_box(10.0, 8.0, 5.0).unwrap();
        let mut model = Model::new_box(occt::write_brep(&target).unwrap()).unwrap();
        model
            .add_primitive(
                "box",
                "Box",
                json!({ "width": "10", "depth": "8", "height": "5" }),
                occt::write_brep(&moving).unwrap(),
            )
            .unwrap();
        let pick = |shape: &occt::Shape, sign| {
            occt::faces(shape)
                .unwrap()
                .iter()
                .position(|face| face.planar && face.normal[2] * sign > 0.99)
                .unwrap() as u32
        };
        let stack = |model: &mut Model, settings| {
            place(
                model,
                PickedFace {
                    name: "Box2",
                    shape: &moving,
                    index: pick(&moving, -1.0),
                },
                PickedFace {
                    name: "Box",
                    shape: &target,
                    index: pick(&target, 1.0),
                },
                settings,
            )
            .unwrap()
        };
        let settings = json!({
            "operation": "stack", "placement_mode": "edge_gap", "horizontal": "left",
            "view_direction": [0.0, 0.0, -1.0], "view_up": [-1.0, 0.28, 0.0],
        });
        let mut fallback = model.clone();
        let placed = stack(&mut model, settings);
        let recipe = &model.payload["features"][2]["inputs"];
        assert!((recipe["target_u"][1].as_f64().unwrap() - 1.0).abs() < 1e-9);
        assert!(recipe["target_u"][0].as_f64().unwrap().abs() < 1e-9);
        assert!(
            recipe["target_basis"]
                .as_array()
                .unwrap()
                .iter()
                .all(|entry| {
                    entry
                        .as_f64()
                        .is_some_and(|v| [0.0, -1.0, 1.0].contains(&v))
                })
        );
        assert!(recipe.get("view_direction").is_none());
        assert!(recipe.get("view_up").is_none());
        let min_y = |shape| occt::bounds(shape).unwrap()[1];
        assert!((min_y(&placed) - min_y(&target)).abs() < 1e-6);

        let placed = stack(
            &mut fallback,
            json!({ "operation": "stack", "placement_mode": "edge_gap", "horizontal": "left" }),
        );
        let min_x = |shape| occt::bounds(shape).unwrap()[0];
        assert!((min_x(&placed) - min_x(&target)).abs() < 1e-6);
    }
}

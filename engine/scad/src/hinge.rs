//! SimpleCAD 0.4's hinges (`kernel/hinge.py`), built from one picked straight edge of the fixed part.
//!
//! Pin, print-in-place and snap-on hinges add knuckles to both parts and a pin; a living hinge joins the two
//! into one print with a thin strip. In the cross-section frame `(u, w)`, with X along the hinge line, `u` points
//! out of the back face and `w` out of the seam face toward the swinging part. The axis is at `(r + gap, 0)`.

use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, alias, failed, kernel};
use occt::UniquePtr;
use serde_json::Value;
use std::collections::BTreeMap;
use std::rc::Rc;

type Vec3 = [f64; 3];

/// How far the knuckle roots reach into the part.
const ROOT: f64 = 0.3;
/// The swing is checked every this many degrees, then refined near a contact.
const SWEEP_STEP: f64 = 10.0;

fn dot(a: Vec3, b: Vec3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn cross(a: Vec3, b: Vec3) -> Vec3 {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

struct Frame {
    /// A point on the hinge line (the edge's start).
    origin: Vec3,
    /// Along the hinge line.
    x: Vec3,
    /// Out of the seam face, toward the swinging part.
    w: Vec3,
    length: f64,
    /// Out of the back face.
    u: Vec3,
}

impl Frame {
    fn point(&self, x: f64, u: f64, w: f64) -> Vec3 {
        std::array::from_fn(|i| self.origin[i] + x * self.x[i] + u * self.u[i] + w * self.w[i])
    }
}

/// `hinge_frame`: the hinge line and its two faces, read off edge `edge` of the fixed part.
fn frame(
    body_a: &occt::Shape,
    edge: u32,
    body_b: &occt::Shape,
    flip: bool,
) -> Result<Frame, StepError> {
    let picked = occt::hinge_edge(body_a, edge).map_err(kernel)?;
    if !picked.straight {
        return Err(failed(
            "A hinge runs along a straight edge; this one is curved. Pick a straight edge where the two parts meet.",
        ));
    }
    let (mut p0, p1) = (picked.start, picked.end);
    let length = (0..3).map(|i| (p1[i] - p0[i]).powi(2)).sum::<f64>().sqrt();
    let mut x: Vec3 = std::array::from_fn(|i| (p1[i] - p0[i]) / length);
    let faces = occt::faces(body_a).map_err(kernel)?;
    let mut unique: Vec<Vec3> = Vec::new();
    for index in picked.faces {
        let face = &faces[index as usize];
        if face.planar && unique.iter().all(|other| dot(face.normal, *other) < 0.999) {
            unique.push(face.normal);
        }
    }
    if unique.len() != 2 {
        return Err(failed(
            "A hinge edge needs a flat face on each side of it. Pick an edge between two flat faces, like a box's top back edge.",
        ));
    }
    let b = occt::bounds(body_b).map_err(kernel)?;
    let toward_b: Vec3 = std::array::from_fn(|i| (b[i] + b[i + 3]) / 2.0 - p0[i]);
    // The seam face is the one facing the swinging part; the knuckles go outside the other one.
    unique.sort_by(|m, n| dot(*n, toward_b).total_cmp(&dot(*m, toward_b)));
    let (mut seam, mut back) = (unique[0], unique[1]);
    if flip {
        (seam, back) = (back, seam);
    }
    // Run along the edge in whichever direction makes (x, u, w) right-handed, so u points out of the back face.
    if dot(cross(seam, x), back) < 0.0 {
        p0 = p1;
        x = x.map(|c| -c);
    }
    Ok(Frame {
        origin: p0,
        x,
        w: seam,
        length,
        u: back,
    })
}

/// Local (x, u, w) primitives, as the Python app builds them.
fn cylinder(
    u: f64,
    w: f64,
    radius: f64,
    x0: f64,
    x1: f64,
) -> Result<UniquePtr<occt::Shape>, StepError> {
    occt::make_cylinder_on(&[x0, u, w], &[1.0, 0.0, 0.0], radius, x1 - x0).map_err(kernel)
}

fn cuboid(from: Vec3, to: Vec3) -> Result<UniquePtr<occt::Shape>, StepError> {
    occt::make_box_between(&from, &to).map_err(kernel)
}

fn boolean(
    operation: u8,
    a: &occt::Shape,
    b: &occt::Shape,
) -> Result<UniquePtr<occt::Shape>, StepError> {
    let mut tools = occt::shapes_new();
    occt::shapes_push(tools.pin_mut(), b);
    occt::combine(operation, a, &tools, 0.0).map_err(kernel)
}

/// `_rotated_box`: a box turned clockwise by `angle` degrees about the hinge axis, the way the lid opens.
#[allow(clippy::too_many_arguments)]
fn rotated_box(
    axis_u: f64,
    cx: f64,
    cw: f64,
    half_t: f64,
    w0: f64,
    w1: f64,
    angle: f64,
    x0: f64,
    x1: f64,
) -> Result<UniquePtr<occt::Shape>, StepError> {
    let shape = cuboid(
        [x0, axis_u - half_t + cx, w0 + cw],
        [x1, axis_u + half_t + cx, w1 + cw],
    )?;
    let turn = occt::trsf_rotation(&[0.0, axis_u, 0.0], &[1.0, 0.0, 0.0], -angle.to_radians())
        .map_err(kernel)?;
    occt::transform_shape(&shape, &turn, true).map_err(kernel)
}

/// `segments`: (start, end, swinging?) for each knuckle, alternating fixed and swinging.
fn segments(length: f64, count: f64, gap: f64) -> Result<Vec<(f64, f64, bool)>, StepError> {
    let count = (count as i64).max(2) as usize;
    let each = (length - (count - 1) as f64 * gap) / count as f64;
    if each < 2.0 {
        return Err(failed(format!(
            "{count} knuckles leave each one {each:.1} mm long. Use fewer knuckles or a longer hinge."
        )));
    }
    Ok((0..count)
        .map(|index| {
            let x = index as f64 * (each + gap);
            (x, x + each, index % 2 == 1)
        })
        .collect())
}

/// `first_contact`: the first angle in (start_at, open_max] where the swinging part, turned about the axis, meets
/// the fixed one; coarse steps, then a 1° search back from the first contact.
fn first_contact(
    fixed: &occt::Shape,
    swinging: &occt::Shape,
    axis_point: Vec3,
    axis: Vec3,
    open_max: f64,
    start_at: f64,
) -> Result<Option<f64>, StepError> {
    let hits = |angle: f64| -> Result<bool, StepError> {
        let turn = occt::trsf_rotation(&axis_point, &axis, -angle.to_radians()).map_err(kernel)?;
        let turned = occt::transform_shape(swinging, &turn, true).map_err(kernel)?;
        Ok(occt::common_volume(fixed, &turned).map_err(kernel)? > 1e-3)
    };
    let mut angles = Vec::new();
    let mut angle = start_at + SWEEP_STEP;
    while angle < open_max {
        angles.push(angle);
        angle += SWEEP_STEP;
    }
    angles.push(open_max);
    let mut previous = start_at;
    for angle in angles {
        if hits(angle)? {
            let mut fine = previous + 1.0;
            while fine < angle {
                if hits(fine)? {
                    return Ok(Some(fine));
                }
                fine += 1.0;
            }
            return Ok(Some(angle));
        }
        previous = angle;
    }
    Ok(None)
}

/// `HingeFeature.execute`.
pub(crate) fn build(ctx: &Context<'_>) -> Result<BTreeMap<String, Shape>, StepError> {
    let name = |key| ctx.input(key).map(alias).unwrap_or_default();
    let (first, second) = (name("body_a"), name("body_b"));
    if first.is_empty() || second.is_empty() || first == second {
        return Err(failed(
            "A hinge joins two different parts: pick the edge on the fixed part and the part that swings.",
        ));
    }
    let body_a = ctx.shape("body_a")?;
    let body_b = ctx.shape("body_b")?;
    let (owner, edge) = ctx.edge("edge")?;
    let kind = ctx
        .input("type")
        .and_then(Value::as_str)
        .unwrap_or("knuckle");
    let label = match kind {
        "pin" => "Pin hinge",
        "knuckle" => "Print-in-place",
        "snap" => "Snap-on",
        "living" => "Living hinge",
        _ => return Err(failed(format!("Unknown hinge type: {kind}"))),
    };
    let flip = ctx
        .input("flip")
        .is_some_and(|v| v.as_bool().unwrap_or(false) || v.as_str() == Some("true"));
    let frame = frame(&owner, edge, &body_b, flip)?;
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let knuckle_count = ctx.value("knuckles", 5.0, Dimension::Scalar)?;
    let (knuckle_od, pin) = (length("knuckle_od", 8.0)?, length("pin", 3.0)?);
    let axial_clearance = length("axial_clearance", 0.4)?;
    let open_max = ctx.value("open_max", 120.0, Dimension::Angle)?;
    let (flex, flex_length, min_wall) = (
        length("flex", 0.5)?,
        length("flex_length", 3.0)?,
        length("min_wall", 0.8)?,
    );
    // A named fit is the printer's (calibrated) clearance; anything else is a length.
    let clearance = match ctx.input("clearance") {
        None => crate::calibration::fit("sliding").expect("embedded fit"),
        Some(Value::String(fit))
            if matches!(fit.as_str(), "snug" | "sliding" | "loose" | "press") =>
        {
            crate::calibration::fit(fit).expect("embedded fit")
        }
        Some(_) => length("clearance", 0.3)?,
    };
    let length_input = length("length", 0.0)?;
    let span = if length_input != 0.0 {
        length_input
    } else {
        frame.length
    };
    if span > frame.length + 1e-6 {
        return Err(failed(format!(
            "The hinge is longer than its {:.1} mm edge.",
            frame.length
        )));
    }
    let start = (frame.length - span) / 2.0;
    let place = occt::trsf_displacement(&frame.origin, &frame.w, &frame.x).map_err(kernel)?;
    let world = |shape: &occt::Shape| occt::transform_shape(shape, &place, true).map_err(kernel);

    if kind == "living" {
        return living(
            ctx,
            &body_a,
            &body_b,
            &first,
            (flex, flex_length),
            start,
            span,
            &world,
        );
    }

    let r = knuckle_od / 2.0;
    let gap = (clearance / 2.0).max(0.25);
    let bore = pin + clearance;
    let wall = r - bore / 2.0;
    if clearance <= 0.0 {
        return Err(failed(
            "A hinge that turns needs clearance; it would fuse solid. Use the Sliding or Loose fit.",
        ));
    }
    if wall < min_wall {
        return Err(failed(format!(
            "A {pin} mm pin in a {knuckle_od} mm knuckle leaves {wall:.2} mm of wall; the minimum is {min_wall} mm. Use a thinner pin or larger knuckles."
        )));
    }
    let axis_u = r + gap;
    let knuckles = segments(span, knuckle_count, axial_clearance)?;
    if !(10.0..=180.0).contains(&open_max) {
        return Err(failed("The opening limit must be between 10° and 180°."));
    }

    let mut fixed_parts = Vec::new();
    let mut swinging_parts = Vec::new();
    for &(x0, x1, swings) in &knuckles {
        let (x0, x1) = (x0 + start, x1 + start);
        let parts = if swings {
            &mut swinging_parts
        } else {
            &mut fixed_parts
        };
        parts.push(cylinder(axis_u, 0.0, r, x0, x1)?);
        let (w0, w1) = if swings { (0.0, r) } else { (-r, 0.0) };
        parts.push(cuboid([x0, -ROOT, w0], [x1, axis_u, w1])?);
    }
    let stop = ctx
        .input("stop")
        .is_some_and(|v| v.as_bool().unwrap_or(false) || v.as_str() == Some("true"));
    if stop {
        // A bar along the swinging part, above its knuckles, meets a shoulder on the fixed knuckles at exactly the
        // opening limit.
        let (bar_r0, thick, half) = (r + gap + 0.4, (r * 0.5).max(1.5), (r * 0.25).max(1.0));
        swinging_parts.push(cuboid(
            [start, axis_u - half, bar_r0],
            [start + span, axis_u + half, bar_r0 + thick],
        )?);
        for &(x0, x1, swings) in &knuckles {
            if swings {
                swinging_parts.push(cuboid(
                    [x0 + start, axis_u - half, r - 0.5],
                    [x1 + start, axis_u + half, bar_r0 + 0.01],
                )?);
            } else {
                fixed_parts.push(rotated_box(
                    axis_u,
                    half + 1.0,
                    0.0,
                    1.0,
                    r - 0.5,
                    bar_r0 + thick,
                    open_max,
                    x0 + start,
                    x1 + start,
                )?);
            }
        }
    }
    let mut a = occt::transform_shape(&body_a, &occt::trsf_identity(), false).map_err(kernel)?;
    for part in &fixed_parts {
        a = boolean(0, &a, &*world(part)?)?;
    }
    let mut b = occt::transform_shape(&body_b, &occt::trsf_identity(), false).map_err(kernel)?;
    for part in &swinging_parts {
        b = boolean(0, &b, &*world(part)?)?;
    }
    let bore_cylinder = world(&*cylinder(
        axis_u,
        0.0,
        bore / 2.0,
        start - 1.0,
        start + span + 1.0,
    )?)?;
    b = boolean(1, &b, &bore_cylinder)?;
    let mut outputs = BTreeMap::new();
    if kind == "pin" {
        a = boolean(1, &a, &bore_cylinder)?;
        let names = ctx
            .input("names")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let pin_name = names
            .first()
            .map(alias)
            .filter(|name| !name.is_empty())
            .ok_or_else(|| {
                failed(
                    "The hinge is missing a name for its pin. Open the hinge and apply it again.",
                )
            })?;
        let dropped = ctx.feature["dropped"]
            .as_array()
            .is_some_and(|list| list.iter().any(|n| alias(n) == pin_name));
        if !dropped {
            let rod = world(&*cylinder(
                axis_u,
                0.0,
                pin / 2.0,
                start + 0.2,
                start + span - 0.2,
            )?)?;
            outputs.insert(pin_name, Rc::new(rod));
        }
    } else {
        a = boolean(
            0,
            &a,
            &*world(&*cylinder(axis_u, 0.0, pin / 2.0, start, start + span)?)?,
        )?;
    }
    let mut note = String::new();
    if kind == "snap" {
        let mouth = pin * 0.85;
        for &(x0, x1, swings) in &knuckles {
            if swings {
                let opening = cuboid(
                    [x0 + start - 0.01, axis_u, -mouth / 2.0],
                    [x1 + start + 0.01, axis_u + r + 1.0, mouth / 2.0],
                )?;
                b = boolean(1, &b, &*world(&opening)?)?;
            }
        }
        // Each side of the C bends like a quarter-ring cantilever, as the snap tabs are estimated: 1.5 t δ / L².
        let arm = std::f64::consts::PI * (r + bore / 2.0) / 4.0;
        let strain = 1.5 * wall * ((pin - mouth) / 2.0) / arm.powi(2);
        if strain > 0.02 {
            note = format!(
                " The clip bends {:.1}% to snap on; PLA cracks at about 2%, PETG about 4%.{}",
                strain * 100.0,
                if strain > 0.04 {
                    " Use larger knuckles or a thinner pin."
                } else {
                    " Print it in PETG."
                }
            );
        }
    }
    let (a, b) = (
        occt::unify(&a).map_err(kernel)?,
        occt::unify(&b).map_err(kernel)?,
    );
    if !occt::is_valid(&a).map_err(kernel)? || !occt::is_valid(&b).map_err(kernel)? {
        return Err(failed(
            "The hinge did not leave valid printable parts. Try smaller knuckles or move the hinge.",
        ));
    }
    let axis_point = frame.point(0.0, axis_u, 0.0);
    if let Some(hit) = first_contact(&a, &b, axis_point, frame.x, open_max, 0.0)? {
        return Err(failed(format!(
            "{second} hits {first} at {hit:.0}° of its {open_max:.0}° swing. Lower the opening limit, use larger knuckles to move the hinge out, or flip it to the other face."
        )));
    }
    if stop && first_contact(&a, &b, axis_point, frame.x, open_max + 4.0, open_max + 2.0)?.is_none()
    {
        note.push_str(" The stop does not catch; the part swings past the limit.");
    }
    ctx.set_message(format!(
        "{label}, {} knuckles, opens to {open_max:.0}°{}; {clearance:.2} mm clearance.{note}",
        knuckles.len(),
        if stop { " against a stop" } else { "" }
    ));
    outputs.insert(first, Rc::new(a));
    outputs.insert(second, Rc::new(b));
    Ok(outputs)
}

/// `HingeFeature._living`: a thin strip, with a pad on each side, joining the two parts into one print.
#[allow(clippy::too_many_arguments)]
fn living(
    ctx: &Context<'_>,
    body_a: &occt::Shape,
    body_b: &occt::Shape,
    first: &str,
    (flex, span): (f64, f64),
    start: f64,
    length: f64,
    world: &dyn Fn(&occt::Shape) -> Result<UniquePtr<occt::Shape>, StepError>,
) -> Result<BTreeMap<String, Shape>, StepError> {
    if !(0.3..=1.2).contains(&flex) {
        return Err(failed(format!(
            "A {flex} mm living hinge will either tear or not bend. 0.4–0.8 mm suits most printers."
        )));
    }
    if occt::minimum_distance(body_a, body_b).is_ok_and(|gap| gap < 0.2) {
        return Err(failed(
            "For a living hinge the two parts must stand apart; touching, they would print as one solid. Leave a gap of at least 0.4 mm between them.",
        ));
    }
    let pad = (flex * 4.0).max(2.0);
    let height = (span + 2.0 * pad).max(8.0);
    let (x0, x1) = (start, start + length);
    let strip = cuboid([x0, -ROOT, -span / 2.0 - 0.5], [x1, flex, span / 2.0 + 0.5])?;
    let pad_a = cuboid([x0, -ROOT, -height / 2.0], [x1, pad, -span / 2.0])?;
    let pad_b = cuboid([x0, -ROOT, span / 2.0], [x1, pad, height / 2.0])?;
    let mut one = occt::transform_shape(body_a, &occt::trsf_identity(), false).map_err(kernel)?;
    for part in [pad_a, strip, pad_b] {
        one = boolean(0, &one, &*world(&part)?)?;
    }
    let one = occt::unify(&*boolean(0, &one, body_b)?).map_err(kernel)?;
    if !occt::is_valid(&one).map_err(kernel)? {
        return Err(failed(
            "The living hinge did not leave a valid printable part.",
        ));
    }
    ctx.set_message(format!(
        "Living hinge, {flex} mm thick, {length:.0} mm long. Living hinges suit PP, PETG or TPU. In PLA they crack after a few bends."
    ));
    Ok(BTreeMap::from([(first.to_string(), Rc::new(one))]))
}

//! Replay saved two-sided printed connector joints on their authored mating faces.

use crate::align::{format_g, joint_anchors, joint_frame, seat_joint, truthy};
use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, alias, failed};
use occt::{ClipPlacementSpec, ClipSpec, SlideSpec};
use serde_json::Value;
use std::collections::BTreeMap;
use std::rc::Rc;

fn kernel(error: occt::Exception) -> StepError {
    failed(error.what())
}

/// SimpleCAD 0.4's version-2 joint styles this engine builds, with the names its message uses.
const STYLES: [(&str, &str); 3] = [
    ("connector", "printed connector"),
    ("snap_tab", "snap tab"),
    ("tab_slot", "tab & slot"),
];

/// SimpleCAD 0.4's sliding joints (`slides.py`), with the names its message uses.
const SLIDES: [(&str, &str); 3] = [
    ("dovetail", "dovetail rail"),
    ("t_rail", "T-rail"),
    ("tongue_groove", "tongue-and-groove rail"),
];

fn choice(ctx: &Context<'_>, key: &str, default: &str) -> String {
    ctx.input(key)
        .and_then(Value::as_str)
        .unwrap_or(default)
        .to_ascii_lowercase()
}

/// The tab & slot key (0.4's `KEY_PRESETS`, scaled by retention and material), on the shared joint settings.
fn key(ctx: &Context<'_>) -> Result<ClipSpec, StepError> {
    let material = choice(ctx, "material", "petg");
    if !matches!(material.as_str(), "pla" | "petg") {
        return Err(failed("Clip material must be PLA or PETG."));
    }
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let fit = choice(ctx, "clearance", "snug");
    let clearance = if matches!(fit.as_str(), "press" | "snug" | "sliding" | "loose") {
        crate::calibration::fit(&fit).expect("embedded clip clearance")
    } else {
        length("clearance", 0.15)?
    };
    let (depth_clearance, lead_in, min_wall) = (
        length("depth_clearance", 0.3)?,
        length("lead_in", 0.4)?,
        length("min_wall", 0.8)?,
    );
    if min_wall < 0.4 {
        return Err(failed("The minimum wall must be at least 0.4 mm."));
    }
    if depth_clearance < 0.0 || lead_in < 0.0 {
        return Err(failed("Depth clearance and lead-in cannot be negative."));
    }
    if clearance < -0.2 {
        return Err(failed(
            "That clearance is tighter than any printer can press together.",
        ));
    }
    let preset = match choice(ctx, "size", "medium").as_str() {
        "small" => [6.0, 2.4, 6.0, 0.3],
        "medium" => [8.0, 3.0, 8.0, 0.4],
        "large" => [12.0, 4.0, 11.0, 0.5],
        _ => return Err(failed("Unknown key size or retention.")),
    };
    let hold = match choice(ctx, "retention", "standard").as_str() {
        "easy" => 0.7,
        "standard" => 1.0,
        "firm" => 1.3,
        _ => return Err(failed("Unknown key size or retention.")),
    };
    let softer = if material == "pla" { 0.7 } else { 1.0 };
    let spec = ClipSpec {
        arm_width: 0.0,
        slot: 0.0,
        thickness: 0.0,
        engagement: 0.0,
        bridge: 0.0,
        barb: 0.0,
        removal_angle: 0.0,
        clearance,
        depth_clearance,
        lead_in,
        min_wall,
        style: 2,
        key_width: length("key_width", preset[0])?,
        key_thickness: length("key_thickness", preset[1])?,
        key_length: length("key_length", preset[2])?,
        bump: length("bump", preset[3] * hold * softer)?,
    };
    if spec.key_width.min(spec.key_thickness).min(spec.key_length) <= 0.0 || spec.bump < 0.0 {
        return Err(failed("Key dimensions must be greater than zero."));
    }
    if spec.bump > spec.key_thickness * 0.3 {
        return Err(failed(
            "The detent bump is too large for the key. Use a thicker key or an easier retention.",
        ));
    }
    Ok(spec)
}

fn dimensions(ctx: &Context<'_>, style: u8) -> Result<ClipSpec, StepError> {
    let choice = |key, default| choice(ctx, key, default);
    let size = choice("size", "medium");
    let [arm, slot, thickness, mut engagement, bridge, mut barb] = match size.as_str() {
        "small" => [1.2, 0.8, 2.0, 6.0, 1.0, 0.4],
        "medium" => [1.8, 1.0, 3.0, 8.0, 1.2, 0.6],
        "large" => [2.4, 1.2, 4.0, 12.0, 1.6, 0.8],
        _ => return Err(failed(format!("Unknown clip size: {size}"))),
    };
    let material = choice("material", "petg");
    let allowed_strain = match material.as_str() {
        "pla" => {
            engagement *= 1.25;
            barb *= 0.65;
            0.02
        }
        "petg" => 0.04,
        _ => return Err(failed("Clip material must be PLA or PETG.")),
    };
    let retention = choice("retention", "standard");
    let (factor, angle) = match retention.as_str() {
        "easy" => (0.75, 25.0),
        "standard" => (1.0, 35.0),
        "firm" => (1.25, 45.0),
        _ => return Err(failed(format!("Unknown clip retention: {retention}"))),
    };
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let fit = choice("clearance", "snug");
    let clearance = if matches!(fit.as_str(), "press" | "snug" | "sliding" | "loose") {
        crate::calibration::fit(&fit).expect("embedded clip clearance")
    } else {
        length("clearance", 0.15)?
    };
    let spec = ClipSpec {
        arm_width: length("arm_width", arm)?,
        slot: length("slot", slot)?,
        thickness: length("thickness", thickness)?,
        engagement: length("engagement", engagement)?,
        bridge: length("bridge", bridge)?,
        barb: length("barb", barb * factor)?,
        removal_angle: ctx.value("removal_angle", angle, Dimension::Angle)?,
        clearance,
        depth_clearance: length("depth_clearance", 0.3)?,
        lead_in: length("lead_in", 0.4)?,
        min_wall: length("min_wall", 0.8)?,
        style,
        key_width: 0.0,
        key_thickness: 0.0,
        key_length: 0.0,
        bump: 0.0,
    };
    if [
        spec.arm_width,
        spec.slot,
        spec.thickness,
        spec.engagement,
        spec.bridge,
        spec.barb,
    ]
    .into_iter()
    .any(|value| value <= 0.0)
    {
        return Err(failed("Clip dimensions must be greater than zero."));
    }
    if spec.min_wall < 0.4 {
        return Err(failed("The minimum wall must be at least 0.4 mm."));
    }
    if spec.depth_clearance < 0.0 || spec.lead_in < 0.0 {
        return Err(failed("Depth clearance and lead-in cannot be negative."));
    }
    if spec.clearance < -0.2
        || spec.thickness + spec.clearance <= 0.0
        || spec.slot + 2.0 * spec.arm_width + spec.clearance <= 0.0
    {
        return Err(failed("That clearance makes the socket too tight."));
    }
    if !(10.0..=70.0).contains(&spec.removal_angle) {
        return Err(failed(
            "The removal ramp angle must be between 10° and 70°.",
        ));
    }
    let strain = 1.5 * spec.arm_width * spec.barb / spec.engagement.powi(2);
    if strain > allowed_strain {
        return Err(failed(format!(
            "These arms would bend {:.1}% to snap in. Choose an easier retention, a longer engagement or thinner arms.",
            strain * 100.0
        )));
    }
    Ok(spec)
}

/// `_fit_clearance`: a named fit is the printer's measured one; anything else is a length.
fn fit_clearance(ctx: &Context<'_>, default: &str) -> Result<f64, StepError> {
    let named = match ctx.input("clearance") {
        None => Some(default.to_string()),
        Some(Value::String(text)) => Some(text.to_ascii_lowercase()),
        _ => None,
    };
    match named {
        Some(fit) if matches!(fit.as_str(), "press" | "snug" | "sliding" | "loose") => {
            Ok(crate::calibration::fit(&fit).expect("embedded clip clearance"))
        }
        _ => ctx.value("clearance", 0.15, Dimension::Length),
    }
}

/// `resolve_rail_spec` and the placement `execute_slide` reads, for the rail style `style`.
fn rail_spec(ctx: &Context<'_>, style: u8) -> Result<SlideSpec, StepError> {
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let clearance = fit_clearance(ctx, "sliding")?;
    let detent = if ctx.input("detent").is_some() {
        length("detent", -1.0)?
    } else {
        -1.0
    };
    let female_mode = match ctx
        .input("female_mode")
        .and_then(Value::as_str)
        .unwrap_or("auto")
    {
        "auto" => 0,
        "cut" => 1,
        "raised" => 2,
        mode => return Err(failed(format!("Unknown channel mode: {mode}"))),
    };
    let insertion = match ctx.input("insertion").and_then(Value::as_str).unwrap_or("") {
        "" => 4,
        "+u" => 0,
        "-u" => 1,
        "+v" => 2,
        "-v" => 3,
        other => return Err(failed(format!("Unknown slide direction: {other}"))),
    };
    // `int(... or 2)`: a History edit saves the count as text.
    let rails = ctx
        .input("rails")
        .and_then(|v| {
            v.as_i64()
                .or_else(|| v.as_f64().map(|f| f as i64))
                .or_else(|| v.as_str()?.trim().parse().ok())
        })
        .filter(|count| *count != 0)
        .unwrap_or(2);
    let spec = SlideSpec {
        style,
        width: length("rail_width", 8.0)?,
        height: length("rail_height", 3.0)?,
        angle: length("rail_angle", 15.0)?,
        clearance,
        depth_clearance: length("depth_clearance", 0.3)?,
        lead_in: length("lead_in", 0.4)?,
        min_wall: length("min_wall", 0.8)?,
        lip: length("lip", 2.0)?,
        stop: ctx.input("stop").is_none() || truthy(ctx.input("stop")),
        detent,
        rails: rails.clamp(1, 8) as u32,
        rail_length: length("rail_length", 0.0)?,
        edge_offset: length("edge_offset", 1.0)?,
        insertion,
        female_mode,
        male_on_a: ctx.input("male").and_then(Value::as_str) != Some("b"),
        seated: truthy(ctx.input("seat")),
    };
    if spec.clearance <= 0.0 {
        return Err(failed(format!(
            "A sliding joint needs clearance to slide; {:.2} mm will bind.",
            spec.clearance
        )));
    }
    if spec.width <= 0.0 || spec.height <= 0.0 {
        return Err(failed(
            "The rail needs a width and a height greater than zero.",
        ));
    }
    if style == 0 {
        if !(2.0..=35.0).contains(&spec.angle) {
            return Err(failed(format!(
                "A dovetail flank of {}° will not hold.",
                format_g(spec.angle)
            )));
        }
        let neck = spec.width - 2.0 * spec.height * spec.angle.to_radians().tan();
        if neck < 1f64.max(2.0 * spec.min_wall) {
            return Err(failed(format!(
                "At {}° a {} mm tall dovetail leaves a {neck:.2} mm neck.",
                format_g(spec.angle),
                format_g(spec.height)
            )));
        }
    }
    if style == 1 && spec.width * 0.5 < 1.0 {
        return Err(failed("This T-rail's stem would be under 1 mm wide."));
    }
    if spec.lip < spec.min_wall {
        return Err(failed(format!(
            "The channel walls ({} mm) are thinner than the minimum wall ({} mm).",
            format_g(spec.lip),
            format_g(spec.min_wall)
        )));
    }
    if (spec.detent < 0.0 && ctx.input("detent").is_some()) || spec.lead_in < 0.0 {
        return Err(failed("Detent and lead-in cannot be negative."));
    }
    Ok(spec)
}

/// The two bodies and faces a joint is built from, part A first seated on B when asked.
struct JointParts {
    body_a: Shape,
    body_b: Shape,
    face_a: occt::UniquePtr<occt::Shape>,
    face_b: occt::UniquePtr<occt::Shape>,
    /// Face B as picked: its body and its face number there.
    target: (Shape, u32),
}

fn joint_parts(ctx: &Context<'_>) -> Result<JointParts, StepError> {
    let mut body_a = ctx.shape("body_a")?;
    let body_b = ctx.shape("body_b")?;
    let (owner_a, index_a) = ctx.face("face_a")?;
    let (owner_b, index_b) = ctx.face("face_b")?;
    let mut face_a = occt::sub_face(&owner_a, index_a).map_err(kernel)?;
    let face_b = occt::sub_face(&owner_b, index_b).map_err(kernel)?;
    if truthy(ctx.input("seat")) {
        let length = |key, default| ctx.value(key, default, Dimension::Length);
        let transform = seat_joint(
            (&owner_a, index_a),
            (&owner_b, index_b),
            length("seat_x", 0.0)?,
            length("seat_y", 0.0)?,
        )?;
        body_a = Rc::new(occt::transform_shape(&body_a, &transform, true).map_err(kernel)?);
        face_a = occt::transform_shape(&face_a, &transform, true).map_err(kernel)?;
    }
    Ok(JointParts {
        body_a,
        body_b,
        face_a,
        face_b,
        target: (owner_b, index_b),
    })
}

/// `resolve_spec`: a version-1 clip's connector, from its size, retention and material.
fn legacy_spec(ctx: &Context<'_>) -> Result<ClipSpec, StepError> {
    let size = choice(ctx, "size", "medium");
    let [arm, slot, thickness, mut engagement, bridge, mut barb] = match size.as_str() {
        "small" => [1.2, 0.8, 2.0, 6.0, 1.0, 0.4],
        "medium" => [1.8, 1.0, 3.0, 8.0, 1.2, 0.6],
        "large" => [2.4, 1.2, 4.0, 12.0, 1.6, 0.8],
        _ => return Err(failed(format!("Unknown clip size: {size}"))),
    };
    let retention = choice(ctx, "retention", "standard");
    let (factor, angle) = match retention.as_str() {
        "easy" => (0.75, 25.0),
        "standard" => (1.0, 35.0),
        "firm" => (1.25, 45.0),
        _ => return Err(failed(format!("Unknown clip retention: {retention}"))),
    };
    match choice(ctx, "material", "petg").as_str() {
        "pla" => {
            engagement *= 1.25;
            barb *= 0.65;
        }
        "petg" => {}
        _ => return Err(failed("Clip material must be PLA or PETG.")),
    }
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let spec = ClipSpec {
        arm_width: length("arm_width", arm)?,
        slot: length("slot", slot)?,
        thickness: length("thickness", thickness)?,
        engagement: length("engagement", engagement)?,
        bridge: length("bridge", bridge)?,
        barb: length("barb", barb * factor)?,
        removal_angle: ctx.value("removal_angle", angle, Dimension::Angle)?,
        clearance: fit_clearance(ctx, "snug")?,
        depth_clearance: 0.0,
        lead_in: 0.0,
        min_wall: 0.0,
        style: 0,
        key_width: 0.0,
        key_thickness: 0.0,
        key_length: 0.0,
        bump: 0.0,
    };
    if [
        spec.arm_width,
        spec.slot,
        spec.thickness,
        spec.engagement,
        spec.bridge,
        spec.barb,
    ]
    .into_iter()
    .any(|value| value <= 0.0)
    {
        return Err(failed("Clip dimensions must be greater than zero."));
    }
    if spec.slot + 2.0 * spec.arm_width + spec.clearance <= 0.0
        || spec.thickness + spec.clearance <= 0.0
    {
        return Err(failed("That clearance makes the socket smaller than zero."));
    }
    if !(10.0..=70.0).contains(&spec.removal_angle) {
        return Err(failed(
            "The removal ramp angle must be between 10° and 70°.",
        ));
    }
    Ok(spec)
}

/// `layout_points`: the face-local clip positions a manual, row or grid layout gives.
fn layout_points(ctx: &Context<'_>, anchors: &[[f64; 2]]) -> Result<Vec<[f64; 2]>, StepError> {
    const MAX: usize = 64;
    let integer = |key, default: i64| {
        ctx.input(key)
            .and_then(|v| v.as_i64().or_else(|| v.as_f64().map(|f| f as i64)))
            .unwrap_or(default)
    };
    let mode = ctx
        .input("layout")
        .and_then(Value::as_str)
        .unwrap_or("manual");
    if mode == "manual" {
        return Ok(anchors.iter().take(MAX).copied().collect());
    }
    if anchors.len() < 2 {
        return Ok(Vec::new());
    }
    let (first, second) = (anchors[0], anchors[1]);
    match mode {
        "row" => {
            let count = integer("count", 2).clamp(1, MAX as i64) as usize;
            let (dx, dy) = (second[0] - first[0], second[1] - first[1]);
            if ctx.input("spacing_mode").and_then(Value::as_str) == Some("fixed") {
                let length = dx.hypot(dy);
                if length < 1e-9 {
                    return Ok(vec![first]);
                }
                let step = ctx.value("spacing", 10.0, Dimension::Length)?;
                let (ux, uy) = (dx / length, dy / length);
                return Ok((0..count)
                    .map(|i| {
                        [
                            first[0] + ux * step * i as f64,
                            first[1] + uy * step * i as f64,
                        ]
                    })
                    .collect());
            }
            if count == 1 {
                return Ok(vec![first]);
            }
            let last = (count - 1) as f64;
            Ok((0..count)
                .map(|i| {
                    [
                        first[0] + dx * i as f64 / last,
                        first[1] + dy * i as f64 / last,
                    ]
                })
                .collect())
        }
        "grid" => {
            let rows = integer("rows", 2).max(1) as usize;
            let columns = integer("columns", 2).max(1) as usize;
            if rows * columns > MAX {
                return Err(failed(format!(
                    "A clip joint supports at most {MAX} connectors."
                )));
            }
            let fraction = |i: usize, n: usize| {
                if n == 1 {
                    0.0
                } else {
                    i as f64 / (n - 1) as f64
                }
            };
            Ok((0..rows)
                .flat_map(|row| {
                    (0..columns).map(move |column| {
                        let (u, v) = (fraction(column, columns), fraction(row, rows));
                        [
                            first[0] + (second[0] - first[0]) * u,
                            first[1] + (second[1] - first[1]) * v,
                        ]
                    })
                })
                .collect())
        }
        _ => Err(failed(format!("Unknown clip layout: {mode}"))),
    }
}

/// `_execute_legacy`: version-1 clips, kept as SimpleCAD 0.4 builds them. Part A is set down on B,
/// then each clip point gets a socket in both parts and a separate printed connector.
fn legacy(ctx: &Context<'_>) -> Result<BTreeMap<String, Shape>, StepError> {
    let name = |key| ctx.input(key).map(alias).unwrap_or_default();
    let (name_a, name_b) = (name("body_a"), name("body_b"));
    if name_a.is_empty() || name_b.is_empty() || name_a == name_b {
        return Err(failed("A clip joint needs two different bodies."));
    }
    let first = ctx.shape("body_a")?;
    let second = ctx.shape("body_b")?;
    let (owner_a, index_a) = ctx.face("face_a")?;
    let (owner_b, index_b) = ctx.face("face_b")?;
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let transform = seat_joint(
        (&owner_a, index_a),
        (&owner_b, index_b),
        length("align_x", 0.0)?,
        length("align_y", 0.0)?,
    )?;
    let aligned = occt::transform_shape(&first, &transform, true).map_err(kernel)?;
    let picked_a = occt::sub_face(&owner_a, index_a).map_err(kernel)?;
    let face_a = occt::transform_shape(&picked_a, &transform, true).map_err(kernel)?;
    let face_b = occt::sub_face(&owner_b, index_b).map_err(kernel)?;
    let mut frame = joint_frame((&owner_b, index_b))?;
    let spec = legacy_spec(ctx)?;
    frame.push(ctx.value("rotation", 0.0, Dimension::Angle)?);
    let read = |value: &Value| {
        value
            .as_f64()
            .or_else(|| value.as_str()?.parse::<f64>().ok())
            .filter(|value| value.is_finite())
            .ok_or_else(|| failed("One of the clip anchors could not be read."))
    };
    let anchors = ctx
        .input("anchors")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[])
        .iter()
        .map(|point| Ok([read(&point[0])?, read(&point[1])?]))
        .collect::<Result<Vec<_>, StepError>>()?;
    let points = layout_points(ctx, &anchors)?;
    if points.is_empty() {
        return Err(failed("Place at least one clip on the target face."));
    }
    if points.len() > 64 {
        return Err(failed("A clip joint supports at most 64 connectors."));
    }
    let names: Vec<String> = ctx
        .input("connector_names")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[])
        .iter()
        .map(alias)
        .collect();
    if names.len() < points.len() {
        return Err(failed("The clip joint is missing connector output names."));
    }
    let dropped = &ctx.feature["dropped"];
    let active: Vec<([f64; 2], &String)> = points
        .into_iter()
        .zip(&names)
        .filter(|(_, name)| {
            !dropped
                .as_array()
                .is_some_and(|d| d.iter().any(|n| n == name.as_str()))
        })
        .collect();
    let flat: Vec<f64> = active.iter().flat_map(|(point, _)| *point).collect();
    let result = occt::clip_legacy(&aligned, &second, &face_a, &face_b, &spec, &frame, &flat)
        .map_err(kernel)?;
    let mut outputs = BTreeMap::from([
        (name_a, Rc::new(result.first)),
        (name_b, Rc::new(result.second)),
    ]);
    for ((_, name), connector) in active.iter().zip(result.connectors) {
        outputs.insert((*name).clone(), Rc::new(connector.shape));
    }
    ctx.set_message(format!(
        "Aligned both parts and created {} removable clip(s).",
        active.len()
    ));
    Ok(outputs)
}

/// `execute_slide`: rails on one part, channels on the other, one step writing both.
fn slide(ctx: &Context<'_>, style: usize) -> Result<BTreeMap<String, Shape>, StepError> {
    let name = |key| ctx.input(key).map(alias).unwrap_or_default();
    let (name_a, name_b) = (name("body_a"), name("body_b"));
    if name_a.is_empty() || name_b.is_empty() || name_a == name_b {
        return Err(failed(
            "A sliding joint needs faces on two different parts.",
        ));
    }
    let spec = rail_spec(ctx, style as u8)?;
    let JointParts {
        body_a,
        body_b,
        face_a,
        face_b,
        ..
    } = joint_parts(ctx)?;
    let result = occt::clip_slides(&body_a, &body_b, &face_a, &face_b, &spec).map_err(kernel)?;
    let count = result.rails;
    let mut message = format!(
        "{count} {}{}, {:.0} mm long, in a {} channel with {:.2} mm clearance.",
        SLIDES[style].1,
        if count == 1 { "" } else { "s" },
        result.engagement,
        if result.raised { "raised" } else { "cut" },
        spec.clearance
    );
    for warning in &result.warnings {
        message.push(' ');
        message.push_str(warning);
    }
    ctx.set_message(message);
    let mut first = result.first;
    // A preview can show part A slid out of its mate, the way it comes apart.
    if truthy(ctx.input("slid_apart")) {
        let away = result.exit_a.map(|c| c * result.travel);
        first = occt::transform_shape(&first, &crate::align::translation(away), true)
            .map_err(kernel)?;
    }
    Ok(BTreeMap::from([
        (name_a, Rc::new(first)),
        (name_b, Rc::new(result.second)),
    ]))
}

pub(crate) fn build(ctx: &Context<'_>) -> Result<BTreeMap<String, Shape>, StepError> {
    let style = ctx
        .input("style")
        .and_then(Value::as_str)
        .unwrap_or("snap_tab");
    // `ClipJointFeature.execute`: no version is a clip made before Join Parts.
    if !truthy(ctx.input("clip_version")) {
        return legacy(ctx);
    }
    if let Some(code) = SLIDES.iter().position(|(name, _)| *name == style) {
        return slide(ctx, code);
    }
    let Some(code) = STYLES.iter().position(|(name, _)| *name == style) else {
        return Err(failed(format!("Unknown joint style: {style}")));
    };
    let name = |key| ctx.input(key).map(alias).unwrap_or_default();
    let (name_a, name_b) = (name("body_a"), name("body_b"));
    if name_a.is_empty() || name_b.is_empty() || name_a == name_b {
        return Err(failed("Joining needs faces on two different parts."));
    }
    let clip = if code == 2 {
        key(ctx)?
    } else {
        dimensions(ctx, code as u8)?
    };
    let JointParts {
        body_a,
        body_b,
        face_a,
        face_b,
        target: (owner_b, index_b),
    } = joint_parts(ctx)?;
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let manual = ctx.input("placement").and_then(Value::as_str) == Some("manual");
    let anchors = if manual {
        let points = ctx
            .input("anchors")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        let read = |value: &Value| {
            value
                .as_f64()
                .or_else(|| value.as_str()?.parse::<f64>().ok())
                .filter(|value| value.is_finite())
                .ok_or_else(|| failed("One of the clip anchors could not be read."))
        };
        let points = points
            .iter()
            .map(|point| Ok([read(&point[0])?, read(&point[1])?]))
            .collect::<Result<Vec<_>, StepError>>()?;
        joint_anchors((&owner_b, index_b), &points)?
    } else {
        Vec::new()
    };
    let count = ctx
        .input("count")
        .and_then(Value::as_i64)
        .unwrap_or(0)
        .clamp(0, 64) as u32;
    let settings = ClipPlacementSpec {
        count,
        spacing: length("spacing", 80.0)?,
        edge_offset: length("edge_offset", 1.0)?,
        shift: length("shift", 0.0)?,
        rotation: ctx.value("rotation", 0.0, Dimension::Angle)?,
        male_on_a: ctx.input("male").and_then(Value::as_str) != Some("b"),
    };
    let result = occt::clip_connectors(
        &body_a, &body_b, &face_a, &face_b, &clip, &settings, &anchors,
    )
    .map_err(kernel)?;
    let names = ctx
        .input("connector_names")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[]);
    let count = result.placed as usize;
    if code == 0 && names.len() < count {
        return Err(failed(
            "The joint is missing names for its printed connectors.",
        ));
    }
    let mut outputs = BTreeMap::from([
        (name_a, Rc::new(result.first)),
        (name_b, Rc::new(result.second)),
    ]);
    for (name, connector) in names.iter().zip(result.connectors) {
        let name = name
            .as_str()
            .filter(|name| !name.is_empty())
            .ok_or_else(|| failed("The joint is missing names for its printed connectors."))?;
        outputs.insert(name.into(), Rc::new(connector.shape));
    }
    let suffix = if count == 1 { "" } else { "s" };
    ctx.set_message(format!("Joined with {count} {}{suffix}.", STYLES[code].1));
    Ok(outputs)
}

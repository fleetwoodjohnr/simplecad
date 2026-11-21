//! Replay saved two-sided printed connector joints on their authored mating faces.

use crate::align::{joint_anchors, seat_joint, truthy};
use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, alias, failed};
use occt::{ClipPlacementSpec, ClipSpec};
use serde_json::Value;
use std::collections::BTreeMap;
use std::rc::Rc;
use std::sync::OnceLock;

const FITS: &str = include_str!("../../../simplecad/data/fits.json");

fn kernel(error: occt::Exception) -> StepError {
    failed(error.what())
}

fn dimensions(ctx: &Context<'_>) -> Result<ClipSpec, StepError> {
    let choice = |key, default| {
        ctx.input(key)
            .and_then(Value::as_str)
            .unwrap_or(default)
            .to_ascii_lowercase()
    };
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
    static TABLE: OnceLock<Value> = OnceLock::new();
    let table = TABLE.get_or_init(|| serde_json::from_str(FITS).expect("embedded fits table"));
    let fit = choice("clearance", "snug");
    let clearance = if matches!(fit.as_str(), "press" | "snug" | "sliding" | "loose") {
        table["fits"][&fit]["clearance"]
            .as_f64()
            .expect("embedded clip clearance")
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

pub(crate) fn build(ctx: &Context<'_>) -> Result<BTreeMap<String, Shape>, StepError> {
    if ctx.input("clip_version").and_then(Value::as_u64) != Some(2)
        || ctx.input("style").and_then(Value::as_str) != Some("connector")
    {
        return Err(failed(
            "This clip style has to be rebuilt, and this version cannot build it yet. Edit it in SimpleCAD 0.4.",
        ));
    }
    let name = |key| ctx.input(key).map(alias).unwrap_or_default();
    let (name_a, name_b) = (name("body_a"), name("body_b"));
    if name_a.is_empty() || name_b.is_empty() || name_a == name_b {
        return Err(failed("Joining needs faces on two different parts."));
    }
    let clip = dimensions(ctx)?;
    let mut body_a = ctx.shape("body_a")?;
    let body_b = ctx.shape("body_b")?;
    let (owner_a, index_a) = ctx.face("face_a")?;
    let (owner_b, index_b) = ctx.face("face_b")?;
    let mut face_a = occt::sub_face(&owner_a, index_a).map_err(kernel)?;
    let face_b = occt::sub_face(&owner_b, index_b).map_err(kernel)?;
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    if truthy(ctx.input("seat")) {
        let transform = seat_joint(
            (&owner_a, index_a),
            (&owner_b, index_b),
            length("seat_x", 0.0)?,
            length("seat_y", 0.0)?,
        )?;
        body_a = Rc::new(occt::transform_shape(&body_a, &transform, true).map_err(kernel)?);
        face_a = occt::transform_shape(&face_a, &transform, true).map_err(kernel)?;
    }
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
    let count = result.connectors.len();
    if names.len() < count {
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
    ctx.set_message(format!("Joined with {count} printed connector{suffix}."));
    Ok(outputs)
}

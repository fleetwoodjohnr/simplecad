//! Seat a replacement on a target face and cut its outer profile through the target.
use crate::align::{projected_bounds, seat_section};
use crate::evaluate::Dimension::Length;
use crate::rebuild::{Context, Shape, StepError, alias, failed, kernel};
use crate::vector::scale;
use serde_json::Value;
use std::rc::Rc;

pub(crate) fn build(ctx: &Context) -> Result<(String, Shape), StepError> {
    let target = ctx.shape("target")?;
    let replacement = ctx.shape("replacement")?;
    let (target_body, target_index) = ctx.face("target_face")?;
    let (mounting_body, mounting_index) = ctx.face("replacement_face")?;
    let boundary = ctx.value("boundary", 0.5, Length)?;
    let setback = ctx.value("setback", 0.0, Length)?;
    if boundary < 0.0 {
        return Err(failed("The replacement boundary cannot be negative."));
    }
    let anchor = |key| ctx.input(key).and_then(Value::as_str).unwrap_or("center");
    let placement = seat_section(
        (&mounting_body, mounting_index),
        (&target_body, target_index),
        [anchor("moving_anchor"), anchor("target_anchor")],
        [ctx.value("u", 0.0, Length)?, ctx.value("v", 0.0, Length)?],
    )?;
    let target_face = occt::sub_face(&target_body, target_index).map_err(kernel)?;
    let info = occt::plane_frame(&target_face).map_err(kernel)?;
    let mounting_face = occt::sub_face(&mounting_body, mounting_index).map_err(kernel)?;
    let seated = occt::transform_shape(&replacement, &placement, true).map_err(kernel)?;
    let seated_face = occt::transform_shape(&mounting_face, &placement, true).map_err(kernel)?;
    let (_, exterior) = projected_bounds(&seated, info.normal, info.center)?;
    let insertion = occt::trsf_translation(&scale(info.normal, -(exterior.max(0.0) + setback)));
    let placed = occt::transform_shape(&seated, &insertion, true).map_err(kernel)?;

    let (_, target_depth) = projected_bounds(&target, scale(info.normal, -1.0), info.center)?;
    let cutter = occt::section_cutter(
        &seated_face,
        &info.normal,
        boundary,
        target_depth.max(0.01) + 0.2,
    )
    .map_err(kernel)?;
    let mut tools = occt::shapes_new();
    occt::shapes_push(tools.pin_mut(), &cutter);
    let removed = occt::combine(2, &target, &tools, 0.0).map_err(kernel)?;
    let remaining = occt::combine(1, &target, &tools, 0.0).map_err(kernel)?;
    let joined = occt::fuse(&remaining, &placed).map_err(kernel)?;
    let result = occt::unify(&joined).map_err(kernel)?;

    if !occt::is_valid(&result).map_err(kernel)? || occt::solid_count(&result) != 1 {
        return Err(failed(
            "The replacement does not make one valid connected body. Reduce the boundary or setback, or move the replacement farther onto the face.",
        ));
    }
    if occt::volume(&removed) <= 1e-8 {
        return Err(failed(
            "The replacement boundary does not overlap the target. Move it onto the selected target face.",
        ));
    }
    ctx.set_message("Replaced the target section and integrated the inserted body.".into());
    Ok((
        ctx.input("target").map(alias).unwrap_or_default(),
        Rc::new(result),
    ))
}

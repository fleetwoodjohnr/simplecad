//! Generate the matching hardware in the legacy recipes from the same thread and ISO tables.

use super::{Size, Thread, boolean, shift, specified_size};
use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, alias, failed, kernel};
use occt::{CylinderData, Shape as Solid, UniquePtr};
use serde_json::Value;
use std::collections::BTreeMap;
use std::f64::consts::{FRAC_PI_6, TAU};
use std::rc::Rc;
use std::sync::OnceLock;

const UP: [f64; 3] = [0., 0., 1.];
const FASTENERS: &str = include_str!("../../../../simplecad/data/fasteners.json");

pub(super) fn matching_size(ctx: &Context<'_>) -> Result<&'static Size, StepError> {
    specified_size(ctx)
        .ok_or_else(|| failed("This feature does not know which thread to match. Select a size."))
}

fn hex_prism(across: f64, height: f64) -> Result<UniquePtr<Solid>, StepError> {
    let radius = across / (2. * FRAC_PI_6.cos());
    let points: [f64; 12] = std::array::from_fn(|i| {
        let angle = TAU * (i / 2) as f64 / 6.;
        if i % 2 == 0 {
            radius * angle.cos()
        } else {
            radius * angle.sin()
        }
    });
    occt::polygon_profile(&points, height).map_err(kernel)
}

fn nominal_axis(radius: f64, length: f64, internal: bool) -> CylinderData {
    CylinderData {
        cylindrical: true,
        internal,
        radius,
        length,
        direction: UP,
        origin: [0.; 3],
    }
}

fn dimensions(size: &Size) -> &'static Value {
    static TABLE: OnceLock<Value> = OnceLock::new();
    let table = TABLE
        .get_or_init(|| serde_json::from_str(FASTENERS).expect("embedded fastener dimensions"));
    &table["hex"][size.name.split('x').next().unwrap_or(&size.name).trim()]
}

pub(crate) fn build(kind: &str, ctx: &Context<'_>) -> Result<Shape, StepError> {
    let size = matching_size(ctx)?;
    let nut = kind == "matching_nut";
    let thread = Thread::with_size(ctx, size, nut)?;
    let data = dimensions(size);
    let proportion = |key: &str, ratio: f64| {
        data[key]
            .as_f64()
            .unwrap_or_else(|| (size.diameter * ratio * 100.).round_ties_even() / 100.)
    };
    let across = proportion("across_flats", 1.6);
    let threaded = if nut {
        let height = ctx.value("height", 0., Dimension::Length)?;
        let height = if height == 0. {
            proportion("nut_height", 0.87)
        } else {
            height
        };
        if height <= 0. {
            return Err(failed("A nut needs a height greater than zero."));
        }
        let radius = thread.bore_diameter() / 2.;
        let blank = hex_prism(across, height)?;
        let drill = occt::make_cylinder(radius, height + 4.).map_err(kernel)?;
        let drill = shift(&drill, UP, -2.)?;
        let drilled = boolean(1, &blank, &drill)?;
        ctx.set_message(format!("{} nut", size.name));
        thread.apply(
            &drilled,
            nominal_axis(radius, height, true),
            [0.; 3],
            height,
        )?
    } else {
        let length = ctx.value("length", size.diameter * 3., Dimension::Length)?;
        if length <= 0. {
            return Err(failed("A bolt needs a length greater than zero."));
        }
        let thread_length = super::thread_length(ctx, "thread_length", length)?;
        let (head, height) = if ctx.input("head").and_then(Value::as_str) == Some("socket") {
            let diameter = proportion("socket_head_diameter", 1.5);
            let height = proportion("socket_head_height", 1.);
            (
                occt::make_cylinder(diameter / 2., height).map_err(kernel)?,
                height,
            )
        } else {
            let height = proportion("head_height", 0.7);
            (hex_prism(across, height)?, height)
        };
        let radius = size.diameter / 2.;
        let shank = occt::make_cylinder(radius, length).map_err(kernel)?;
        let shank = shift(&shank, UP, height)?;
        let blank = boolean(0, &head, &shank)?;
        ctx.set_message(format!("{} x {length} mm bolt", size.name));
        thread.apply(
            &blank,
            nominal_axis(radius, length, false),
            [0., 0., height + length - thread_length],
            thread_length,
        )?
    };
    let result = occt::unify(&threaded).map_err(kernel)?;
    let position = [
        ctx.value("x", 0., Dimension::Length)?,
        ctx.value("y", 0., Dimension::Length)?,
        ctx.value("z", 0., Dimension::Length)?,
    ];
    if position.iter().all(|value| value.abs() <= 1e-12) {
        return Ok(Rc::new(result));
    }
    let placement = occt::trsf_translation(&position);
    occt::transform_shape(&result, &placement, false)
        .map(Rc::new)
        .map_err(kernel)
}

pub(crate) fn connection(ctx: &Context<'_>) -> Result<BTreeMap<String, Shape>, StepError> {
    let geometry = |key| {
        let (shape, index) = ctx.face(key)?;
        let face = occt::sub_face(&shape, index).map_err(kernel)?;
        let data = occt::cylinder_data(&face).map_err(kernel)?;
        if !data.cylindrical {
            return Err(failed(
                "A threaded connection needs a round face on each part.",
            ));
        }
        Ok(data)
    };
    let a = geometry("face_a")?;
    let b = geometry("face_b")?;
    if a.internal == b.internal {
        return Err(failed("Select one shaft and one hole so they can mate."));
    }
    let male = if a.internal { b } else { a };
    let female = if a.internal { a } else { b };
    let male_thread = Thread::read(ctx, male.radius * 2., false)?;
    let female_thread = Thread::with_size(ctx, male_thread.size, true)?;
    let length = super::thread_length(ctx, "length", male.length.min(female.length))?;
    let mut parts = BTreeMap::new();
    for (key, geometry) in [("body_a", a), ("body_b", b)] {
        let name = ctx
            .input(key)
            .map(alias)
            .ok_or_else(|| failed("The thread needs both parts."))?;
        let body = ctx.shape(key)?;
        let thread = if geometry.internal {
            &female_thread
        } else {
            &male_thread
        };
        let length = length.min(geometry.length);
        parts.insert(
            name,
            thread.apply(&body, geometry, geometry.origin, length)?,
        );
    }
    ctx.set_message(format!("{} threaded pair", male_thread.designation()));
    Ok(parts)
}

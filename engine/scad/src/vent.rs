//! Hexagonal vents cut as a single boolean, with each fitted hole in one tool compound.

use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, failed};
use std::f64::consts::PI;
use std::rc::Rc;

fn kernel(error: occt::Exception) -> StepError {
    failed(error.what())
}

struct Grid {
    across: f64,
    wall: f64,
    margin: f64,
}

impl Grid {
    fn tool(
        &self,
        width: f64,
        height: f64,
        depth: f64,
        target: &str,
    ) -> Result<occt::UniquePtr<occt::Shape>, StepError> {
        let root3 = 3.0_f64.sqrt();
        let radius = self.across / root3;
        let pitch = self.across + self.wall;
        let row_step = pitch * root3 / 2.0;
        let half_width = width / 2.0 - self.margin;
        let half_height = height / 2.0 - self.margin;
        if half_width < self.across / 2.0 || half_height < radius {
            return Err(no_holes(target));
        }
        let rows = (half_height / row_step) as i32;
        let columns = (half_width / pitch) as i32;
        if (2_i64 * i64::from(rows) + 1).saturating_mul(2_i64 * i64::from(columns) + 3) > 40_000 {
            return Err(failed(
                "This grille is too large to rebuild. Increase the hole size or reduce the vent area.",
            ));
        }
        let prototype = {
            let mut points = [0.0; 12];
            for (index, point) in points.as_chunks_mut::<2>().0.iter_mut().enumerate() {
                let angle = PI / 6.0 + index as f64 * PI / 3.0;
                *point = [radius * angle.cos(), radius * angle.sin()];
            }
            occt::polygon_profile(&points, depth).map_err(kernel)?
        };
        let mut holes = occt::shapes_new();
        for row in -rows..=rows {
            let y = f64::from(row) * row_step;
            if y.abs() + radius > half_height {
                continue;
            }
            let offset = if row % 2 == 0 { 0.0 } else { pitch / 2.0 };
            for column in (-columns - 1)..=columns {
                let x = f64::from(column) * pitch + offset;
                if x.abs() + self.across / 2.0 <= half_width {
                    let hole = occt::transform_shape(
                        &prototype,
                        &occt::trsf_translation(&[x, y, -depth / 2.0]),
                        true,
                    )
                    .map_err(kernel)?;
                    occt::shapes_push(holes.pin_mut(), &hole);
                }
            }
        }
        Ok(occt::shapes_compound(&holes))
    }
}

fn no_holes(target: &str) -> StepError {
    failed(format!(
        "No vent holes fit inside this {target}. Use a smaller hole size, a thinner wall, or a smaller border."
    ))
}

pub(crate) fn build(ctx: &Context<'_>, cut: bool) -> Result<Shape, StepError> {
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let grid = Grid {
        across: length("across_flats", 5.0)?,
        wall: length("wall", 1.2)?,
        margin: length("margin", 2.0)?,
    };
    if grid.across < 0.8 {
        return Err(failed(format!(
            "A {:.2} mm hole is too small to print cleanly. Use at least 0.8 mm across flats.",
            grid.across
        )));
    }
    if grid.wall < 0.4 {
        return Err(failed(format!(
            "A {:.2} mm wall between holes is thinner than a printed line. Use at least 0.4 mm.",
            grid.wall
        )));
    }
    if grid.margin < 0.0 {
        return Err(failed("The border cannot be negative."));
    }
    let cut_holes = |body: &occt::Shape, holes: &occt::Shape, place: &occt::Trsf| {
        let placed = occt::transform_shape(holes, place, true).map_err(kernel)?;
        let mut tools = occt::shapes_new();
        occt::shapes_push(tools.pin_mut(), &placed);
        occt::combine(1, body, &tools, 0.0).map_err(kernel)
    };
    if cut {
        let body = ctx.shape("body")?;
        let (face_body, index) = ctx.face("face")?;
        let face = occt::sub_face(&face_body, index).map_err(kernel)?;
        let face = occt::plane_frame(&face).map_err(kernel)?;
        if !face.planar {
            return Err(failed(
                "A vent needs a flat face. Pick a planar wall, not a curved one.",
            ));
        }
        let [u_min, u_max, v_min, v_max] = face.uv;
        let bounds = occt::bounds(&body).map_err(kernel)?;
        let span: f64 = (0..3)
            .map(|axis| ((bounds[axis + 3] - bounds[axis]) * face.normal[axis]).abs())
            .sum();
        let span = if span > 0.0 { span } else { 10.0 };
        let holes = grid.tool(
            (u_max - u_min).abs(),
            (v_max - v_min).abs(),
            span * 4.0 + 10.0,
            "face",
        )?;
        let place = occt::trsf_axis_frame(&face.center, &face.normal).map_err(kernel)?;
        return cut_holes(&body, &holes, &place).map(Rc::new);
    }
    let width = length("width", 60.0)?;
    let depth = length("depth", 60.0)?;
    let thickness = length("thickness", 3.0)?;
    for (value, what) in [(width, "width"), (depth, "depth"), (thickness, "thickness")] {
        if value <= 0.0 {
            return Err(failed(format!(
                "The {what} must be greater than zero. Enter a positive {what}."
            )));
        }
    }
    let plate = occt::make_box(width, depth, thickness).map_err(kernel)?;
    let center = occt::trsf_translation(&[-width / 2.0, -depth / 2.0, 0.0]);
    let plate = occt::transform_shape(&plate, &center, true).map_err(kernel)?;
    let holes = grid.tool(width, depth, thickness * 4.0, "plate")?;
    let center_z = occt::trsf_translation(&[0.0, 0.0, thickness / 2.0]);
    let plate = cut_holes(&plate, &holes, &center_z)?;
    let place = occt::trsf_translation(&[length("x", 0.0)?, length("y", 0.0)?, length("z", 0.0)?]);
    occt::transform_shape(&plate, &place, true)
        .map(Rc::new)
        .map_err(kernel)
}

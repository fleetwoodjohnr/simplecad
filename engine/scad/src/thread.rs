//! Physical threads follow the v0.4.4 helical sweep, relief, and fit geometry.
use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, failed, kernel};
use occt::{CylinderData, Shape as Solid, UniquePtr};
use serde_json::Value;
use std::f64::consts::PI;
use std::rc::Rc;
use std::sync::OnceLock;

const NUDGE: f64 = 0.001;
const ENVELOPE_MARGIN: f64 = 0.02;
const DATA: [&str; 6] = [
    include_str!("../../../simplecad/data/threads/printed.json"),
    include_str!("../../../simplecad/data/threads/iso_metric.json"),
    include_str!("../../../simplecad/data/threads/unc.json"),
    include_str!("../../../simplecad/data/threads/unf.json"),
    include_str!("../../../simplecad/data/threads/bsp.json"),
    include_str!("../../../simplecad/data/threads/npt.json"),
];
const FITS: &str = include_str!("../../../simplecad/data/fits.json");
const PRINTER: &str =
    include_str!("../../../simplecad/data/printers/elegoo_centauri_carbon_2.json");

struct Size {
    name: String,
    diameter: f64,
    pitch: f64,
    angle: f64,
    taper: f64,
    preference: usize,
}

fn sizes() -> &'static [Size] {
    static SIZES: OnceLock<Vec<Size>> = OnceLock::new();
    SIZES.get_or_init(|| {
        let mut sizes = Vec::new();
        for (preference, source) in DATA.iter().enumerate() {
            let table: Value = serde_json::from_str(source).expect("embedded thread table");
            for row in table["sizes"].as_array().expect("thread sizes") {
                sizes.push(Size {
                    name: row["designation"]
                        .as_str()
                        .expect("thread designation")
                        .into(),
                    diameter: row["diameter"].as_f64().expect("thread diameter"),
                    pitch: row["pitch"].as_f64().expect("thread pitch"),
                    angle: table["angle"].as_f64().unwrap_or(60.0),
                    taper: table["taper"].as_f64().unwrap_or(0.0),
                    preference,
                });
            }
        }
        sizes
    })
}

pub(crate) struct Thread {
    size: &'static Size,
    iso: bool,
    left_hand: bool,
    clearance: f64,
}

fn depth(pitch: f64, angle: f64, iso: bool) -> f64 {
    if iso {
        (17.0 / 24.0) * (pitch / (2.0 * (angle / 2.0).to_radians().tan()))
    } else {
        (pitch - pitch / 8.0 - pitch / 4.0) / 2.0 / 45_f64.to_radians().tan()
    }
}

fn clearance(value: Option<&Value>) -> Result<f64, StepError> {
    static TABLE: OnceLock<Value> = OnceLock::new();
    let table = TABLE.get_or_init(|| serde_json::from_str(FITS).expect("embedded fits table"));
    if let Some(value) = value.and_then(Value::as_f64) {
        return if value >= 0.0 {
            Ok(value)
        } else {
            Err(failed("Thread clearance cannot be negative."))
        };
    }
    let name = value.and_then(Value::as_str).unwrap_or("normal");
    table["thread_clearance"][name]["clearance"]
        .as_f64()
        .ok_or_else(|| failed(format!("Unknown thread-clearance preset: {name}")))
}

impl Thread {
    pub(crate) fn designation(&self) -> &str {
        &self.size.name
    }

    pub(crate) fn read(ctx: &Context, diameter: f64, internal: bool) -> Result<Self, StepError> {
        let name = ctx.input("designation").and_then(Value::as_str);
        let size = name.and_then(|name| sizes().iter().find(|s| s.name.eq_ignore_ascii_case(name)))
            .or_else(|| {
                let rank = |size: &Size| {
                    (size.diameter - diameter).abs() + size.preference as f64 * 0.25
                };
                sizes().iter().filter(|s| (s.diameter - diameter).abs() <= 2.0)
                    .min_by(|a, b| rank(a).total_cmp(&rank(b))
                        .then(a.preference.cmp(&b.preference))
                        .then(b.pitch.total_cmp(&a.pitch)))
            })
            .ok_or_else(|| failed(format!(
                "No standard thread matches ⌀{diameter:.2} mm. Change the diameter or choose a standard size."
            )))?;
        let iso = ctx.input("form").and_then(Value::as_str) == Some("iso");
        let tooth = depth(size.pitch, size.angle, iso);
        let overhang = 90.0 - (size.pitch * 5.0 / 16.0).atan2(tooth).to_degrees();
        static PROFILE: OnceLock<Value> = OnceLock::new();
        let profile = PROFILE
            .get_or_init(|| serde_json::from_str(PRINTER).expect("embedded printer profile"));
        let minimum = profile["min_feature_size"]
            .as_f64()
            .expect("printer feature size");
        let limit = profile["max_overhang_angle"]
            .as_f64()
            .expect("printer overhang angle");
        if tooth < minimum {
            return Err(failed(format!(
                "{}'s {tooth:.2} mm tooth is below this printer's {minimum:.2} mm feature limit. Choose a coarser pitch or a larger thread.",
                size.name
            )));
        }
        if overhang > limit + 0.5 {
            return Err(failed(format!(
                "Its {overhang:.0}° overhang is beyond this printer's {limit:.0}° support-free limit. Choose the printed form or a coarser size."
            )));
        }
        Ok(Self {
            size,
            iso,
            left_hand: ctx
                .input("left_hand")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            clearance: if internal {
                clearance(ctx.input("clearance"))?
            } else {
                0.0
            },
        })
    }

    pub(crate) fn bore_diameter(&self) -> f64 {
        self.size.diameter + self.clearance
    }

    fn bolt(&self, diameter: f64, length: f64, swell: f64) -> Result<UniquePtr<Solid>, StepError> {
        // Match the sweep dimensions used by the legacy thread-solid cache.
        let rounded = |value: f64| (value * 10_000.0).round_ties_even() / 10_000.0;
        let pitch = rounded(self.size.pitch);
        let major = rounded(diameter) / 2.0;
        let length = rounded(length);
        let swell = rounded(swell);
        let tooth = depth(pitch, self.size.angle, self.iso);
        let minor = major - tooth;
        if minor <= 0.0 || minor - self.size.taper * length / 2.0 <= 0.0 {
            return Err(failed("This pitch is too coarse for that diameter."));
        }
        let core = occt::make_cylinder(minor, length).map_err(kernel)?;
        let core_volume = PI * minor.powi(2) * length;
        let crest = pitch / 16.0 + swell;
        let root = (pitch * 3.0 / 8.0 + swell).min(pitch / 2.0 - 1e-4);
        for overlap in [0.15, 0.5, 1.0] {
            let inner = minor - overlap * tooth;
            let profile = [inner, -root, major, -crest, major, crest, inner, root];
            let spiral = occt::sweep_thread_tooth(minor, pitch, length, self.left_hand, &profile)
                .map_err(kernel)?;
            let bolt = boolean(0, &core, &spiral)?;
            if occt::volume(&bolt) >= core_volume * 0.99 {
                return Ok(bolt);
            }
        }
        Err(failed(
            "The thread could not be built at this diameter and pitch. Try a coarser pitch or a larger diameter.",
        ))
    }

    fn apply(
        &self,
        body: &Solid,
        bore: CylinderData,
        origin: [f64; 3],
        length: f64,
    ) -> Result<Shape, StepError> {
        if length <= 0.0 {
            return Err(failed("A thread needs a positive length."));
        }
        let gap = if bore.internal {
            self.clearance + NUDGE
        } else {
            0.0
        };
        let diameter = self.size.diameter
            + if bore.internal {
                gap
            } else {
                2.0 * ENVELOPE_MARGIN
            };
        let radius = (diameter / 2.0).max(bore.radius) + ENVELOPE_MARGIN;
        let minor = diameter / 2.0 - depth(self.size.pitch, self.size.angle, self.iso);
        let bolt = self.bolt(diameter, length, gap / 2.0)?;
        let envelope = occt::make_cylinder(radius, length).map_err(kernel)?;
        let max_groove =
            PI * length * (1.1 * (radius.powi(2) - minor.powi(2)) + 0.02 * radius.powi(2));
        let mut groove = [0.0, NUDGE, -NUDGE].into_iter().find_map(|delta| {
            let candidate = if delta == 0.0 { None } else { shift(&bolt, [0.0, 0.0, 1.0], delta).ok() };
            if delta != 0.0 && candidate.is_none() {
                return None;
            }
            let shape = boolean(1, &envelope, candidate.as_deref().unwrap_or(&bolt)).ok()?;
            (occt::volume(&shape) < max_groove).then_some(shape)
        }).ok_or_else(|| failed(format!(
            "The {} thread could not be cut cleanly at this size. Try a coarser size or a shorter threaded length.", self.size.name
        )))?;
        if self.size.taper != 0.0 {
            let end_radius = radius - self.size.taper * length / 2.0;
            let cone = occt::make_cone(
                radius + NUDGE + self.size.taper * NUDGE / 2.0,
                end_radius + NUDGE - self.size.taper * NUDGE / 2.0,
                length + 2.0 * NUDGE,
            )
            .map_err(kernel)?;
            let cone = shift(&cone, [0.0, 0.0, 1.0], -NUDGE)?;
            groove = boolean(2, &groove, &cone)?;
        }

        let placement = occt::trsf_axis_frame(&origin, &bore.direction).map_err(kernel)?;
        let lead = radius - minor;
        let relief = 1e-4 < lead && 2.0 * lead < length;
        if relief && bore.internal {
            for (z, bottom, top) in [(0.0, radius, minor), (length - lead, minor, radius)] {
                if let Ok(next) =
                    lead_cone(z, bottom, top, lead).and_then(|cone| boolean(1, &groove, &cone))
                {
                    groove = next;
                }
            }
        }
        let tool = occt::transform_shape(&groove, &placement, true).map_err(kernel)?;
        let mut shape = apply_tool(body, &tool, bore.internal, bore.direction).ok_or_else(|| {
            failed(format!("The {} thread could not be applied to this body. Try a coarser size or a shorter threaded length.", self.size.name))
        })?;
        if relief && !bore.internal {
            for (z, bottom, top) in [(0.0, minor, radius), (length - lead, radius, minor)] {
                if let Ok(next) = (|| {
                    let block = occt::make_cylinder(radius, lead).map_err(kernel)?;
                    let block = shift(&block, [0.0, 0.0, 1.0], z)?;
                    let keep = lead_cone(z, bottom, top, lead)?;
                    let ring = boolean(1, &block, &keep)?;
                    let ring = occt::transform_shape(&ring, &placement, true).map_err(kernel)?;
                    boolean(1, &shape, &ring)
                })() {
                    shape = next;
                }
            }
        }
        Ok(Rc::new(shape))
    }

    pub(crate) fn drill(
        &self,
        ctx: &Context,
        body: &Solid,
        spec: &occt::HoleCut,
    ) -> Result<Shape, StepError> {
        let mut bore = None;
        for index in 0..occt::faces(body).map_err(kernel)?.len() {
            let face = occt::sub_face(body, index as u32).map_err(kernel)?;
            let info = occt::cylinder_data(&face).map_err(kernel)?;
            if !info.internal
                || (info.radius * 2.0 - spec.diameter).abs() > 1e-6
                || dot(info.direction, spec.inward).abs() < 0.999
            {
                continue;
            }
            let offset = std::array::from_fn(|i| spec.position[i] - info.origin[i]);
            let along = dot(offset, info.direction);
            let distance: f64 = (0..3)
                .map(|i| (offset[i] - info.direction[i] * along).powi(2))
                .sum();
            if distance <= 1e-8 && bore.is_none_or(|best: CylinderData| info.length > best.length) {
                bore = Some(info);
            }
        }
        let bore = bore.ok_or_else(|| failed(
            "The hole was made, but its wall could not be measured, so no thread was added. Try a shorter blind hole or select another face."
        ))?;
        let length = thread_length(ctx, "thread_length", bore.length)?;
        self.apply(body, bore, thread_origin(ctx, bore, length), length)
    }
}

pub(crate) fn build(ctx: &Context) -> Result<Shape, StepError> {
    let body = ctx.shape("body")?;
    let (source, index) = ctx.face("face")?;
    let face = occt::sub_face(&source, index).map_err(kernel)?;
    let mut info = occt::cylinder_data(&face).map_err(kernel)?;
    if !info.cylindrical {
        return Err(failed(
            "Threads go on round faces. Select the cylindrical face of a shaft or a hole.",
        ));
    }
    let length = thread_length(ctx, "length", info.length)?;
    let origin = thread_origin(ctx, info, length);
    let diameter = info.radius * 2.0;
    let thread = Thread::read(ctx, diameter, info.internal)?;
    let wanted = if info.internal {
        thread.bore_diameter()
    } else {
        thread.size.diameter
    };
    let resize = ctx
        .input("resize")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let depth = depth(thread.size.pitch, thread.size.angle, thread.iso);
    let bite = if info.internal {
        (wanted + NUDGE) / 2.0 + ENVELOPE_MARGIN
    } else {
        wanted / 2.0
    } - depth;
    if !resize && info.radius <= bite {
        let kind = if info.internal { "hole" } else { "shaft" };
        return Err(failed(format!(
            "This {kind} is ⌀{diameter:.2} mm and {} needs ⌀{wanted:.2} mm, so there is nothing for the thread to bite into. Turn on “Resize to suit the thread”, or choose a size nearer this diameter.",
            thread.size.name
        )));
    }
    if resize && diameter < thread.size.diameter - 0.05 {
        let tool = occt::make_cylinder(wanted / 2.0, length + 2.0 * NUDGE).map_err(kernel)?;
        let start: [f64; 3] = std::array::from_fn(|i| origin[i] - info.direction[i] * NUDGE);
        let placement = occt::trsf_axis_frame(&start, &info.direction).map_err(kernel)?;
        let tool = occt::transform_shape(&tool, &placement, true).map_err(kernel)?;
        let resized = boolean(u8::from(info.internal), &body, &tool)?;
        info.radius = wanted / 2.0;
        thread.apply(&resized, info, origin, length)
    } else {
        thread.apply(&body, info, origin, length)
    }
}

fn thread_length(ctx: &Context, field: &str, face_length: f64) -> Result<f64, StepError> {
    let length = ctx.value(field, face_length, Dimension::Length)?;
    Ok(if length == 0.0 {
        face_length
    } else {
        face_length.min(length)
    })
}

fn thread_origin(ctx: &Context, info: CylinderData, length: f64) -> [f64; 3] {
    let spare = info.length - length;
    let rise = info.direction[2] * info.length;
    let top = ctx
        .input("from_end")
        .and_then(Value::as_str)
        .unwrap_or("top")
        == "top";
    if spare > 1e-9 && rise.abs() > 1e-9 && top == (rise > 0.0) {
        std::array::from_fn(|i| info.origin[i] + info.direction[i] * spare)
    } else {
        info.origin
    }
}

fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a.into_iter().zip(b).map(|(x, y)| x * y).sum()
}

fn boolean(op: u8, body: &Solid, tool: &Solid) -> Result<UniquePtr<Solid>, StepError> {
    let mut tools = occt::shapes_new();
    occt::shapes_push(tools.pin_mut(), tool);
    occt::combine(op, body, &tools, 0.0).map_err(kernel)
}

fn shift(shape: &Solid, axis: [f64; 3], delta: f64) -> Result<UniquePtr<Solid>, StepError> {
    let by = axis.map(|value| value * delta);
    let transform = occt::trsf_translation(&by);
    occt::transform_shape(shape, &transform, true).map_err(kernel)
}

fn lead_cone(z: f64, bottom: f64, top: f64, lead: f64) -> Result<UniquePtr<Solid>, StepError> {
    let cone = occt::make_cone(bottom, top, lead).map_err(kernel)?;
    if z == 0.0 {
        Ok(cone)
    } else {
        shift(&cone, [0.0, 0.0, 1.0], z)
    }
}

fn apply_tool(
    body: &Solid,
    tool: &Solid,
    internal: bool,
    axis: [f64; 3],
) -> Option<UniquePtr<Solid>> {
    let before = occt::volume(body);
    let tool_volume = occt::volume(tool);
    let check = |result: UniquePtr<Solid>| {
        let after = occt::volume(&result);
        let delta = if internal {
            after - before
        } else {
            before - after
        };
        (delta > before.abs() * 1e-6
            && (tool_volume <= 0.0 || delta <= tool_volume * 1.05)
            && (internal || after > before * 0.25))
            .then_some(result)
    };
    let apply = |candidate: &Solid| {
        boolean(u8::from(!internal), body, candidate)
            .ok()
            .and_then(check)
            .or_else(|| {
                internal
                    .then(|| boolean(0, candidate, body).ok().and_then(check))
                    .flatten()
            })
    };
    apply(tool).or_else(|| {
        [NUDGE, -NUDGE].into_iter().find_map(|delta| {
            let shifted = shift(tool, axis, delta).ok()?;
            apply(&shifted)
        })
    })
}

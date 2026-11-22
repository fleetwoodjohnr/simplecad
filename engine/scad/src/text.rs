//! Solid text on a referenced planar face, using the same outlines and placements as 0.4.4.

use crate::evaluate::Dimension;
use crate::rebuild::{Context, Shape, StepError, failed};
use occt::{PlaneFrame, TextCharacter, TextFont, TextResult, TextSpec};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::OnceLock;

const SANS: &[(&str, &str)] = &[
    ("LiberationSans-Regular.ttf", "Liberation Sans"),
    ("OpenSans-Regular.ttf", "Open Sans"),
    ("NotoSans-Regular.ttf", "Noto Sans"),
    ("AdwaitaSans-Regular.ttf", "Adwaita Sans"),
    ("arial.ttf", "Arial"),
];
const SERIF: &[(&str, &str)] = &[
    ("LiberationSerif-Regular.ttf", "Liberation Serif"),
    ("NotoSerif-Regular.ttf", "Noto Serif"),
    ("DejaVuSerif.ttf", "DejaVu Serif"),
    ("times.ttf", "Times New Roman"),
    ("Times New Roman.ttf", "Times New Roman"),
];
const MONO: &[(&str, &str)] = &[
    ("LiberationMono-Regular.ttf", "Liberation Mono"),
    ("AdwaitaMono-Regular.ttf", "Adwaita Mono"),
    ("SourceCodePro-Regular.otf", "Source Code Pro"),
    ("DejaVuSansMono.ttf", "DejaVu Sans Mono"),
    ("consola.ttf", "Consolas"),
    ("cour.ttf", "Courier New"),
    ("Courier New.ttf", "Courier New"),
];

fn collect_fonts(root: &Path, depth: u8, found: &mut Vec<PathBuf>) {
    let Ok(entries) = root.read_dir() else { return };
    let mut paths: Vec<_> = entries.flatten().map(|entry| entry.path()).collect();
    paths.sort_unstable();
    for path in paths {
        if path.is_file() {
            found.push(path);
        } else if depth > 0 && path.is_dir() {
            collect_fonts(&path, depth - 1, found);
        }
    }
}

fn fonts(names: &[(&str, &str)]) -> Vec<TextFont> {
    static SYSTEM_FONTS: OnceLock<Vec<PathBuf>> = OnceLock::new();
    let files = SYSTEM_FONTS.get_or_init(|| {
        let mut roots: Vec<PathBuf> = match std::env::consts::OS {
            "windows" => vec![
                PathBuf::from(std::env::var_os("WINDIR").unwrap_or_else(|| r"C:\Windows".into()))
                    .join("Fonts"),
            ],
            "macos" => vec!["/System/Library/Fonts".into(), "/Library/Fonts".into()],
            _ => vec!["/usr/share/fonts".into(), "/usr/local/share/fonts".into()],
        };
        if let Some(home) = std::env::home_dir() {
            if cfg!(target_os = "macos") {
                roots.push(home.join("Library/Fonts"));
            } else if cfg!(target_os = "linux") {
                roots.push(home.join(".local/share/fonts"));
            }
        }
        let mut found = Vec::new();
        for root in roots {
            collect_fonts(&root, 3, &mut found);
        }
        found
    });
    names
        .iter()
        .flat_map(|(filename, name)| {
            files
                .iter()
                .filter(move |path| {
                    path.file_name()
                        .and_then(|n| n.to_str())
                        .is_some_and(|file| file.eq_ignore_ascii_case(filename))
                })
                .filter_map(move |path| {
                    Some(TextFont {
                        path: path.to_str()?.to_owned(),
                        name: (*name).to_owned(),
                    })
                })
        })
        .collect()
}

fn characters(text: &str) -> Result<Vec<TextCharacter>, StepError> {
    if text.contains(['\n', '\r', '\0']) {
        return Err(failed("Text must be a single line."));
    }
    let characters: Vec<_> = text
        .chars()
        .map(|character| TextCharacter {
            code: u32::from(character),
            space: character.is_whitespace() || matches!(character, '\u{1c}'..='\u{1f}'),
        })
        .collect();
    if characters.iter().all(|character| character.space) {
        return Err(failed("Enter some text before creating it."));
    }
    Ok(characters)
}

fn text_geometry(
    body: &occt::Shape,
    face: PlaneFrame,
    text: &str,
    family: &str,
    mode: &str,
    settings: &TextSpec,
) -> Result<TextResult, StepError> {
    if !face.planar {
        return Err(failed(
            "Text can only be placed on a flat face. Select one planar face.",
        ));
    }
    let characters = characters(text)?;
    let names = match family {
        "sans-serif" => SANS,
        "serif" => SERIF,
        "monospace" => MONO,
        _ => {
            return Err(failed(format!(
                "'{family}' is not an available text family."
            )));
        }
    };
    if !matches!(mode, "raised" | "engraved") {
        return Err(failed(format!("'{mode}' is not a text operation.")));
    }
    if settings.height <= 0.0 {
        return Err(failed("Text height must be greater than zero."));
    }
    if settings.depth <= 0.0 {
        return Err(failed("Text depth must be greater than zero."));
    }
    occt::face_text(body, &face, &characters, &fonts(names), family, settings)
        .map_err(|error| failed(error.what()))
}

pub(crate) fn build(ctx: &Context<'_>) -> Result<Shape, StepError> {
    let string = |key, default| ctx.input(key).and_then(Value::as_str).unwrap_or(default);
    let body = ctx.shape("body")?;
    let (owner, index) = ctx.face("face")?;
    let kernel = |error: occt::Exception| failed(error.what());
    let face = occt::sub_face(&owner, index).map_err(kernel)?;
    let face = occt::plane_frame(&face).map_err(kernel)?;
    let text = string("text", "");
    let family = string("font_family", "sans-serif");
    let mode = string("mode", "raised");
    let length = |key, default| ctx.value(key, default, Dimension::Length);
    let settings = TextSpec {
        height: length("text_height", 8.0)?,
        depth: length("depth", 1.0)?,
        offset_x: length("offset_x", 0.0)?,
        offset_y: length("offset_y", 0.0)?,
        rotation: ctx.value("rotation", 0.0, Dimension::Angle)?,
        raised: mode == "raised",
    };
    let result = text_geometry(&body, face, text, family, mode, &settings)?;
    let action = if settings.raised {
        "Raised"
    } else {
        "Engraved"
    };
    ctx.set_message(format!("{action} {text:?} in {}", result.font));
    Ok(Rc::new(result.shape))
}

/// Raised sans-serif `text` on the plane `face` of `body`, centred `offset` along the plane from its centre.
pub(crate) fn raised_label(
    body: &occt::Shape,
    face: &PlaneFrame,
    text: &str,
    height: f64,
    depth: f64,
    offset: [f64; 2],
) -> Result<occt::UniquePtr<occt::Shape>, String> {
    let settings = TextSpec {
        height,
        depth,
        offset_x: offset[0],
        offset_y: offset[1],
        rotation: 0.0,
        raised: true,
    };
    occt::face_text(
        body,
        face,
        &characters(text).map_err(|error| error.message)?,
        &fonts(SANS),
        "sans-serif",
        &settings,
    )
    .map(|result| result.shape)
    .map_err(|error| error.what().to_string())
}

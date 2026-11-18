//! The project container. Feature data is validated and replayed by the engine.
use std::io::{Cursor, Read, Write};

use zip::{ZipArchive, ZipWriter, write::SimpleFileOptions};

const MODEL_ENTRY: &str = "model.json";
pub const MAX_MODEL_BYTES: usize = 16 * 1024 * 1024;

pub fn read_model(file: &[u8]) -> Result<String, &'static str> {
    let mut archive = ZipArchive::new(Cursor::new(file))
        .map_err(|_| "This is not a readable SimpleCAD project archive.")?;
    let member = archive
        .by_name(MODEL_ENTRY)
        .map_err(|_| "The project is missing model.json.")?;
    if member.size() > MAX_MODEL_BYTES as u64 {
        return Err("The project recipe is larger than this build can open.");
    }
    let mut model = String::new();
    member
        .take(MAX_MODEL_BYTES as u64 + 1)
        .read_to_string(&mut model)
        .map_err(|_| "The project recipe is damaged.")?;
    if model.len() > MAX_MODEL_BYTES {
        return Err("The project recipe is larger than this build can open.");
    }
    Ok(model)
}

pub fn write_model(model: &str) -> Result<Vec<u8>, &'static str> {
    if model.len() > MAX_MODEL_BYTES {
        return Err("The project recipe is larger than this build can save.");
    }
    let mut archive = ZipWriter::new(Cursor::new(Vec::new()));
    let options = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    archive
        .start_file(MODEL_ENTRY, options)
        .map_err(|_| "Could not create the project archive.")?;
    archive
        .write_all(model.as_bytes())
        .map_err(|_| "Could not write the project recipe.")?;
    archive
        .finish()
        .map(|buffer| buffer.into_inner())
        .map_err(|_| "Could not finish the project archive.")
}

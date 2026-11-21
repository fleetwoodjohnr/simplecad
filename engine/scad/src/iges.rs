//! Stage validated IGES input for OCCT's filename-only reader.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;

struct InputFile(PathBuf);

impl Drop for InputFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

pub fn read(bytes: &[u8], name: &str) -> Result<Vec<occt::ImportedBody>, String> {
    let path = std::env::var_os("SIMPLECAD_IGES_SCRATCH_FILE")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            std::env::temp_dir().join(format!("simplecad-{}.iges", uuid::Uuid::new_v4()))
        });
    let staging_error = |_| "Could not prepare a temporary copy of the IGES file.";
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(staging_error)?;
    }
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&path).map_err(staging_error)?;
    let cleanup = InputFile(path);
    let written = file.write_all(bytes);
    drop(file);
    written.map_err(staging_error)?;
    let path = cleanup
        .0
        .to_str()
        .ok_or("The temporary IGES path is unreadable.")?;
    occt::read_iges_file(path, name).map_err(|error| error.what().to_owned())
}

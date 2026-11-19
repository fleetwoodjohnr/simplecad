fn main() {
    napi_build::setup();
    // The OCCT libraries ship next to the addon (app/native, then the app's
    // unpacked resources), so the addon looks for them beside itself. Windows
    // finds DLLs beside the addon without this.
    match std::env::var("CARGO_CFG_TARGET_OS").as_deref() {
        Ok("linux") => println!("cargo:rustc-link-arg=-Wl,-rpath,$ORIGIN"),
        Ok("macos") => println!("cargo:rustc-link-arg=-Wl,-rpath,@loader_path"),
        _ => {}
    }
}

//! The `scad` tool and the tests run against the OCCT install directly
//! (`DEP_OCCT_LIB_DIR`, from the `occt` crate); Windows finds it on PATH.
fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        let lib =
            std::env::var("DEP_OCCT_LIB_DIR").expect("the occt crate reports its library folder");
        println!("cargo:rustc-link-arg=-Wl,-rpath,{lib}");
    }
}

//! Compiles the C++ shim against an OCCT install and links its toolkits.
//!
//! `OCCT_DIR` is the prefix `build-occt.sh` installed into (Unix layout on
//! every OS: `include/opencascade`, `lib`, `bin`).

/// The OCCT toolkits the shim calls into, dependencies after dependents.
const TOOLKITS: &[&str] = &[
    "TKDESTEP",
    "TKXSBase",
    "TKOffset",
    "TKFillet",
    "TKMesh",
    "TKShHealing",
    "TKBO",
    "TKPrim",
    "TKTopAlgo",
    "TKGeomAlgo",
    "TKBRep",
    "TKGeomBase",
    "TKG3d",
    "TKG2d",
    "TKMath",
    "TKernel",
];

fn main() {
    let dir = std::env::var("OCCT_DIR")
        .expect("set OCCT_DIR to an OCCT install made by engine/occt/build-occt.sh");
    let lib = format!("{dir}/lib");

    cxx_build::bridge("src/lib.rs")
        .file("cpp/shim.cpp")
        .file("cpp/step.cpp")
        .file("cpp/thread.cpp")
        .include("cpp")
        .include(format!("{dir}/include/opencascade"))
        .std("c++17")
        .compile("occt-shim");

    println!("cargo:rustc-link-search=native={lib}");
    for toolkit in TOOLKITS {
        println!("cargo:rustc-link-lib=dylib={toolkit}");
    }
    // Tests and examples in this crate run against the install directly.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        println!("cargo:rustc-link-arg=-Wl,-rpath,{lib}");
    }
    println!("cargo:lib_dir={lib}");
    println!("cargo:rerun-if-env-changed=OCCT_DIR");
    println!("cargo:rerun-if-changed=cpp/shim.cpp");
    println!("cargo:rerun-if-changed=cpp/step.cpp");
    println!("cargo:rerun-if-changed=cpp/thread.cpp");
    println!("cargo:rerun-if-changed=cpp/shim.hpp");
}

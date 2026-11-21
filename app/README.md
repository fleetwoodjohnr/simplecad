# SimpleCAD 0.5 preview (Electron + Rust on OCCT)

The next SimpleCAD: the same OCCT 8.0.1 geometry as the released app, driven from Rust, in the Simple Office
shell. It runs beside the released app until it can open and edit every project.

Build OCCT once (cmake, a C++17 compiler, FreeType and RapidJSON; about an hour), then run the preview:

    ../engine/occt/build-occt.sh ~/.cache/simplecad-occt/8.0.1
    npm ci
    npm run engine        # OCCT_DIR defaults to ~/.cache/simplecad-occt/8.0.1
    npm run dev

File → Open project opens a `.scad3` saved by SimpleCAD 0.4 and shows its visible bodies as they were saved.
Select a flat face in the viewport or the inspector, then type a signed distance or drag its handle: push/pull runs
the same operation as the released app. Undo and redo work; saving and the rest of the modeling tools come with the
history port.

`npm run dist` builds an unpacked app with OCCT's libraries beside the addon; `npm run e2e:headless` tests the
desktop under Xvfb on Linux. `cargo test --locked --workspace` in `engine/` tests the OCCT shim.

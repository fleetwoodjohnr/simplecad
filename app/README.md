# SimpleCAD Rust preview

Run the native desktop preview from this directory.

    npm ci
    npm run engine
    npm run dev

File → Open recessed wall example opens a synthetic enclosure with a recessed vent and through-holes. Select a face in the viewport or inspector, then enter a signed distance or drag its handle. Save As writes the model and its undo history to a preview project file.

The kernel currently handles solids built from planes perpendicular to the coordinate axes. Legacy SimpleCAD projects, curved solids, and the remaining modeling tools are pending. Use the existing desktop editor for those projects.

Run npm run dist to build an unpacked preview, or npm run e2e:headless to test the desktop under Xvfb on Linux. Run cargo test --locked --workspace from engine/ for the kernel and project tests.

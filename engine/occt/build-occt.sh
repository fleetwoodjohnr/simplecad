#!/usr/bin/env bash
# Build OCCT as shared libraries for the SimpleCAD engine, the same way on Linux,
# macOS and Windows (Git Bash, inside an MSVC developer environment).
#
#   engine/occt/build-occt.sh <install-prefix> [work-dir]
#
# The engine models and meshes; three.js draws. Build the modelling modules
# and STEP with their dependencies. Adding TKDESTEP alone keeps the unrelated
# DataExchange toolkits and their third-party dependencies out of the bundle.
# The CI cache key hashes this file, so changing it rebuilds OCCT.
set -euo pipefail

VERSION=8_0_1
SHA256=0d6913eae4bcc09a3653ceced6dda1aec11c35a1513d4c06762c9b002092c68a

prefix=${1:?usage: build-occt.sh <install-prefix> [work-dir]}
work=${2:-"$PWD/occt-build"}
mkdir -p "$work" "$prefix"
prefix=$(cd "$prefix" && pwd)
cd "$work"

archive="OCCT-V$VERSION.tar.gz"
[ -f "$archive" ] || curl -fsSL -o "$archive" \
  "https://github.com/Open-Cascade-SAS/OCCT/archive/refs/tags/V$VERSION.tar.gz"
if command -v sha256sum >/dev/null; then
  echo "$SHA256  $archive" | sha256sum -c -
else
  echo "$SHA256  $archive" | shasum -a 256 -c -
fi
[ -d "OCCT-$VERSION" ] || tar -xzf "$archive"

# Each library finds the others beside itself, so the folder works wherever the
# app puts it (Windows looks beside a DLL anyway).
case "$(uname -s)" in
  Linux) relocatable=(-DCMAKE_INSTALL_RPATH='$ORIGIN') ;;
  Darwin) relocatable=(-DCMAKE_INSTALL_RPATH=@loader_path -DCMAKE_INSTALL_NAME_DIR=@rpath) ;;
  *) relocatable=() ;;
esac

cmake -S "OCCT-$VERSION" -B build ${CMAKE_GENERATOR:+-G "$CMAKE_GENERATOR"} \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$prefix" \
  -DBUILD_LIBRARY_TYPE=Shared \
  -DINSTALL_DIR_LAYOUT=Unix \
  ${relocatable[@]+"${relocatable[@]}"} \
  -DBUILD_USE_PCH=OFF \
  -DBUILD_MODULE_FoundationClasses=ON \
  -DBUILD_MODULE_ModelingData=ON \
  -DBUILD_MODULE_ModelingAlgorithms=ON \
  -DBUILD_MODULE_Visualization=OFF \
  -DBUILD_MODULE_ApplicationFramework=OFF \
  -DBUILD_MODULE_DataExchange=OFF \
  -DBUILD_ADDITIONAL_TOOLKITS=TKDESTEP \
  -DBUILD_MODULE_Draw=OFF \
  -DUSE_OPENGL=OFF -DUSE_GLES2=OFF -DUSE_D3D=OFF -DUSE_XLIB=OFF \
  -DUSE_FREETYPE=OFF -DUSE_RAPIDJSON=OFF \
  -DUSE_TK=OFF -DUSE_TBB=OFF -DUSE_FREEIMAGE=OFF -DUSE_VTK=OFF \
  -DUSE_FFMPEG=OFF -DUSE_OPENVR=OFF -DUSE_DRACO=OFF -DUSE_EIGEN=OFF \
  ${OCCT_CMAKE_ARGS:-}

cmake --build build --config Release --parallel "${JOBS:-4}"
cmake --install build --config Release
echo "OCCT $VERSION installed in $prefix"

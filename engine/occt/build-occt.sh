#!/usr/bin/env bash
# Build OCCT as shared libraries for the SimpleCAD engine, the same way on Linux,
# macOS and Windows (Git Bash, inside an MSVC developer environment).
#
#   engine/occt/build-occt.sh <install-prefix> [work-dir]
#
# The engine models and meshes; three.js draws. Build the modelling modules,
# STEP, IGES and the B-Rep font toolkit without OCCT's graphics backends.
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

# Link FreeType statically into OCCT so the addon can load without a
# platform-specific FreeType install or a second set of shared libraries.
font_version=2.14.3
font_sha=36bc4f1cc413335368ee656c42afca65c5a3987e8768cc28cf11ba775e785a5f
font_archive="freetype-$font_version.tar.xz"
[ -f "$font_archive" ] || curl -fsSL -o "$font_archive" \
  "https://download.savannah.gnu.org/releases/freetype/$font_archive"
if command -v sha256sum >/dev/null; then
  echo "$font_sha  $font_archive" | sha256sum -c -
else
  echo "$font_sha  $font_archive" | shasum -a 256 -c -
fi
[ -d "freetype-$font_version" ] || tar -xf "$font_archive"
fonts="$PWD/freetype-install"
cmake -S "freetype-$font_version" -B freetype-build ${CMAKE_GENERATOR:+-G "$CMAKE_GENERATOR"} \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$fonts" -DCMAKE_INSTALL_LIBDIR=lib \
  -DBUILD_SHARED_LIBS=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
  -DFT_DISABLE_ZLIB=ON -DFT_DISABLE_BZIP2=ON -DFT_DISABLE_PNG=ON \
  -DFT_DISABLE_HARFBUZZ=ON -DFT_DISABLE_BROTLI=ON
cmake --build freetype-build --config Release --parallel "${JOBS:-4}"
cmake --install freetype-build --config Release
case "$(uname -s)" in
  MINGW*|MSYS*) font_library="$fonts/lib/freetype.lib" ;;
  *) font_library="$fonts/lib/libfreetype.a" ;;
esac
test -f "$font_library"

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
  '-DBUILD_ADDITIONAL_TOOLKITS=TKDESTEP;TKDEIGES;TKV3d' \
  -DBUILD_MODULE_Draw=OFF \
  -DUSE_OPENGL=OFF -DUSE_GLES2=OFF -DUSE_D3D=OFF -DUSE_XLIB=OFF \
  -DUSE_FREETYPE=ON -DUSE_RAPIDJSON=OFF \
  -D3RDPARTY_FREETYPE_DIR="$fonts" -D3RDPARTY_FREETYPE_LIBRARY="$font_library" \
  -D3RDPARTY_FREETYPE_LIBRARY_DIR="$fonts/lib" \
  -D3RDPARTY_FREETYPE_INCLUDE_DIR_ft2build="$fonts/include/freetype2" \
  -D3RDPARTY_FREETYPE_INCLUDE_DIR_freetype2="$fonts/include/freetype2" \
  -DUSE_TK=OFF -DUSE_TBB=OFF -DUSE_FREEIMAGE=OFF -DUSE_VTK=OFF \
  -DUSE_FFMPEG=OFF -DUSE_OPENVR=OFF -DUSE_DRACO=OFF -DUSE_EIGEN=OFF \
  ${OCCT_CMAKE_ARGS:-}

cmake --build build --config Release --parallel "${JOBS:-4}"
cmake --install build --config Release
mkdir -p "$prefix/share/licenses/freetype"
cp "freetype-$font_version/docs/FTL.TXT" "$prefix/share/licenses/freetype/"
echo "OCCT $VERSION installed in $prefix"

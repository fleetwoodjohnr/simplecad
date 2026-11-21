#!/usr/bin/env bash
# Build the GEOS C library used for planar CAD profiles.
set -euo pipefail

version=3.13.1
sha=df2c50503295f325e7c8d7b783aca8ba4773919cde984193850cf9e361dfd28c
prefix=${1:?usage: build-geos.sh <install-prefix> [work-dir]}
work=${2:-"$PWD/geos-build"}
mkdir -p "$prefix" "$work"
prefix=$(cd "$prefix" && pwd)
cd "$work"
archive="geos-$version.tar.bz2"
[ -f "$archive" ] || curl -fsSL -o "$archive" "https://download.osgeo.org/geos/$archive"
if command -v sha256sum >/dev/null; then
  echo "$sha  $archive" | sha256sum -c -
else
  echo "$sha  $archive" | shasum -a 256 -c -
fi
[ -d "geos-$version" ] || tar -xf "$archive"

case "$(uname -s)" in
  Linux) relocatable=(-DCMAKE_INSTALL_RPATH='$ORIGIN') ;;
  Darwin) relocatable=(-DCMAKE_INSTALL_RPATH=@loader_path -DCMAKE_INSTALL_NAME_DIR=@rpath) ;;
  *) relocatable=() ;;
esac

cmake -S "geos-$version" -B build ${CMAKE_GENERATOR:+-G "$CMAKE_GENERATOR"} \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
  -DCMAKE_INSTALL_LIBDIR=lib -DBUILD_SHARED_LIBS=ON -DBUILD_TESTING=OFF \
  "${relocatable[@]}"
cmake --build build --config Release --parallel "${JOBS:-4}"
cmake --install build --config Release
mkdir -p "$prefix/share/licenses/geos"
cp "geos-$version/COPYING" "$prefix/share/licenses/geos/"

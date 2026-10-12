#!/usr/bin/env bash
# Renders every listing image from the HTML in this folder into ../
# Usage: bash source/render.sh   (from the temp/ folder)
set -e
E="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
SRC="$(cygpath -w "$PWD/source")"
render() {  # page width height scale output
  "$E" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=$4 \
    --window-size=$2,$3 --screenshot="$(cygpath -w "$PWD/$5")" "file:///${SRC//\//}/$1" 2>/dev/null
}
render icon.html 512 512 0.25 icon-128.png
render icon.html 512 512 1 icon-512.png
render thumbnail.html 1920 1080 1 thumbnail-1920x1080.png
for n in 1 2 3 4 5; do
  [ -f "source/carousel-$n.html" ] && render "carousel-$n.html" 1920 1080 1 "carousel-$n.png"
done
echo rendered

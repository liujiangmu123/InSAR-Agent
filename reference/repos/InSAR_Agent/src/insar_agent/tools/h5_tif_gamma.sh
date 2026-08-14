#!/bin/bash

ROOT_DIR="${1:-.}"

for dir in "$ROOT_DIR"/*/; do
    [ -d "$dir" ] || continue
    echo ""
    echo "--- Processing: $(basename "$dir") ---"
    cd "$dir" || continue

    # velocity.h5
    if [ -f "velocity.tif" ]; then
        echo "  [SKIP] velocity.tif already converted."
    elif [ ! -f "velocity.h5" ]; then
        echo "  [MISS] velocity.h5 is missing, skip."
    else
        echo "  [RUN ] save_gdal.py velocity.h5 -d velocity -o velocity.tif --of GTiff"
        save_gdal.py velocity.h5 -d velocity -o velocity.tif --of GTiff
        [ $? -ne 0 ] && echo "  [FAIL] Exit code: $?"
    fi

    # geometry (from inputs/geometryGeo.h5)
    if [ -f "incidenceAngle.tif" ]; then
        echo "  [SKIP] incidenceAngle.tif already converted."
    elif [ ! -f "inputs/geometryGeo.h5" ]; then
        echo "  [MISS] inputs/geometryGeo.h5 is missing, skip."
    else
        echo "  [RUN ] save_gdal.py inputs/geometryGeo.h5 -d incidenceAngle -o incidenceAngle.tif --of GTiff"
        save_gdal.py inputs/geometryGeo.h5 -d incidenceAngle -o incidenceAngle.tif --of GTiff
        [ $? -ne 0 ] && echo "  [FAIL] Exit code: $?"
    fi

    # maskTempCoh.h5
    if [ -f "maskTempCoh.tif" ]; then
        echo "  [SKIP] maskTempCoh.tif already converted."
    elif [ ! -f "maskTempCoh.h5" ]; then
        echo "  [MISS] maskTempCoh.h5 is missing, skip."
    else
        echo "  [RUN ] save_gdal.py maskTempCoh.h5 --of GTiff"
        save_gdal.py maskTempCoh.h5 --of GTiff
        [ $? -ne 0 ] && echo "  [FAIL] Exit code: $?"
    fi

    # temporalCoherence.h5
    if [ -f "temporalCoherence.tif" ]; then
        echo "  [SKIP] temporalCoherence.tif already converted."
    elif [ ! -f "temporalCoherence.h5" ]; then
        echo "  [MISS] temporalCoherence.h5 is missing, skip."
    else
        echo "  [RUN ] save_gdal.py temporalCoherence.h5 --of GTiff"
        save_gdal.py temporalCoherence.h5 --of GTiff
        [ $? -ne 0 ] && echo "  [FAIL] Exit code: $?"
    fi

    # waterMask.h5
    if [ -f "waterMask.tif" ]; then
        echo "  [SKIP] waterMask.tif already converted."
    elif [ ! -f "waterMask.h5" ]; then
        echo "  [MISS] waterMask.h5 is missing, skip."
    else
        echo "  [RUN ] save_gdal.py waterMask.h5 --of GTiff"
        save_gdal.py waterMask.h5 --of GTiff
        [ $? -ne 0 ] && echo "  [FAIL] Exit code: $?"
    fi

    cd - > /dev/null
done

echo ""
echo "All done."

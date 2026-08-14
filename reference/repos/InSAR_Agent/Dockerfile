# InSAR Agent - Docker image with GDAL + MintPy + all deps
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# System dependencies: Python 3.10 + GDAL + build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 \
    python3.10-dev \
    python3-pip \
    gdal-bin \
    libgdal-dev \
    libproj-dev \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1 \
    && python -m pip install --no-cache-dir --upgrade pip setuptools wheel

# Python GDAL binding (version must match system libgdal)
RUN gdal_version=$(gdal-config --version) \
    && python -m pip install --no-cache-dir "gdal==${gdal_version}"

# MintPy + core scientific stack
# numpy<2 required for system GDAL compatibility
RUN python -m pip install --no-cache-dir \
    'numpy<2' \
    mintpy \
    scipy \
    h5py \
    matplotlib \
    geopandas \
    shapely \
    fiona

# Project dependencies
RUN python -m pip install --no-cache-dir \
    asf_search \
    hyp3_sdk \
    openai \
    fastapi \
    uvicorn \
    requests \
    pyproj \
    cdsapi \
    && python -m pip install --no-cache-dir --force-reinstall 'numpy<2'

WORKDIR /app

# Copy project code
COPY pyproject.toml .
COPY src/ src/
COPY web/ web/

# Install the InSAR Agent package in development mode
RUN python -m pip install --no-cache-dir -e .

# Create data directories
RUN mkdir -p /app/projects /app/data /app/output

EXPOSE 8866

RUN printf '#!/bin/sh\nmkdir -p /root\nprintf "url: %s\\nkey: %s\\n" "${CDSAPI_URL:-https://cds.climate.copernicus.eu/api}" "$CDSAPI_KEY" > /root/.cdsapirc 2>/dev/null\nexec python -m uvicorn web.app:app --host 0.0.0.0 --port 8866 --reload --reload-dir /app/web --reload-dir /app/src\n' > /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

"""AOI geometry helpers"""


def make_aoi_wkt(lon: float, lat: float, half_span: float = 0.25) -> str:
    """Construct a bounding box WKT polygon around (lon, lat).

    Used as fallback when no shapefile is provided. The half_span should be
    chosen based on the area of interest (city ~0.25\u00b0, province ~2.5\u00b0).

    Args:
        lon, lat: Center coordinates (EPSG:4326)
        half_span: Half-width in decimal degrees (default 0.25\u00b0, ~25km)

    Returns:
        POLYGON WKT string
    """
    xmin = lon - half_span
    xmax = lon + half_span
    ymin = lat - half_span
    ymax = lat + half_span
    return (
        f'POLYGON(({xmin} {ymin}, {xmax} {ymin}, '
        f'{xmax} {ymax}, {xmin} {ymax}, {xmin} {ymin}))'
    )

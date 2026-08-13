"""Choropleth rendering over the boundary layers built by etl/.

Coverage is the constraint here, not the drawing. Counties and places join to
geometry exactly; school districts only by name; special districts, which are
two thirds of Oregon's governments, have no boundaries in this data at all. So
a map of Oregon's governments is always partial, and the renderer says so on the
map rather than letting a reader infer that a missing polygon means zero.

"No data" therefore gets its own neutral fill, well off the sequential ramp. The
ramp's light end is a mid step rather than near-white for the same reason: a
near-white lightest bin is indistinguishable from an empty one, which turns a
coverage gap into an apparent measurement.
"""

import json
import math
import os

import format as fmt
import viz

BUILD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build")

LAYERS = {
    "county": ("geo/county.geojson", "GEOID"),
    "place": ("geo/place.geojson", "GEOID"),
    "school_district": ("geo/school_district.geojson", "GEOID"),
}

BINS = ["bin-1", "bin-2", "bin-3", "bin-4", "bin-5"]


def load_layer(layer, build=BUILD):
    path, key = LAYERS[layer]
    with open(os.path.join(build, path)) as fh:
        return json.load(fh), key


def _project(features, width, height, padding=12):
    """Plate carree with a standard parallel at the layer's mean latitude.

    Adequate for a single state and free of dependencies. Anything larger would
    want a proper equal-area projection, because area comparisons across a wide
    latitude range are what a choropleth invites.
    """
    lons, lats = [], []
    for feature in features:
        for polygon in feature["geometry"]["coordinates"]:
            for ring in polygon:
                for lon, lat in ring:
                    lons.append(lon)
                    lats.append(lat)
    if not lons:
        raise ValueError("layer has no coordinates")

    mean_lat = (min(lats) + max(lats)) / 2
    scale_x = math.cos(math.radians(mean_lat))
    x0, x1 = min(lons) * scale_x, max(lons) * scale_x
    y0, y1 = -max(lats), -min(lats)

    span_x = (x1 - x0) or 1
    span_y = (y1 - y0) or 1
    scale = min((width - 2 * padding) / span_x, (height - 2 * padding) / span_y)
    offset_x = padding + ((width - 2 * padding) - span_x * scale) / 2
    offset_y = padding + ((height - 2 * padding) - span_y * scale) / 2

    def project(lon, lat):
        return (offset_x + (lon * scale_x - x0) * scale,
                offset_y + (-lat - y0) * scale)

    return project


def _path(geometry, project, tolerance=0.6):
    """Emit an SVG path, dropping points closer together than a pixel or so.

    At map scale, coordinate precision beyond roughly half a pixel is invisible
    and costs a large multiple in file size.
    """
    parts = []
    for polygon in geometry["coordinates"]:
        for ring in polygon:
            points = []
            last = None
            for lon, lat in ring:
                x, y = project(lon, lat)
                if last is None or abs(x - last[0]) + abs(y - last[1]) >= tolerance:
                    points.append((x, y))
                    last = (x, y)
            if len(points) < 3:
                continue
            parts.append("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points) + " Z")
    return " ".join(parts)


def quantile_bins(values, count=5):
    """Quantile breaks. Equal-interval would put nearly every Oregon entity in
    the lowest bin, since these distributions are dominated by small districts."""
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return []
    if len(set(ordered)) <= count:
        return sorted(set(ordered))[:-1]
    return [ordered[int(len(ordered) * i / count)] for i in range(1, count)]


def bin_index(value, breaks):
    for index, threshold in enumerate(breaks):
        if value < threshold:
            return index
    return len(breaks)


LEGEND_BAND = 74


def choropleth(layer, values, title, subtitle, formatter=fmt.percent,
               width=680, height=460, build=BUILD, note=None, only=None):
    """`values` maps a layer geo_id to a number. Anything absent draws as no data.

    `only` restricts the drawing to a set of geo_ids, which is how a place map
    becomes readable: 378 Oregon cities at state scale are dots, and a reader
    comparing dot areas is comparing city land area, which means nothing here.

    Returns the SVG and the accounting: how many polygons carried a value, which
    is the number that keeps a partial map honest.
    """
    collection, key = load_layer(layer, build)
    features = [f for f in collection["features"] if f["geometry"]]
    if only is not None:
        features = [f for f in features if f["properties"].get(key) in only]
    if not features:
        return {"svg": viz.refusal(title, "No boundaries match that extent."),
                "covered": 0, "polygons": 0, "breaks": []}

    numbers = [v for v in values.values() if v is not None]
    breaks = quantile_bins(numbers)

    top, offset = viz.header(title, subtitle, width)
    # Reserve the legend band before projecting, or the map draws over it.
    map_height = height - offset - LEGEND_BAND
    project = _project(features, width, map_height)
    body = [f'<g transform="translate(0,{offset + 4})">']

    covered = 0
    for feature in features:
        geo_id = feature["properties"].get(key)
        name = feature["properties"].get("NAME", geo_id)
        value = values.get(geo_id)
        if value is None:
            fill = viz.token("nodata")
            label = "no data"
        else:
            covered += 1
            fill = viz.token(BINS[bin_index(value, breaks)])
            label = formatter(value)
        path = _path(feature["geometry"], project)
        if not path:
            continue
        # A hairline in the surface colour separates neighbours; it is a gap
        # doing the work, not a border drawn to outline the mark.
        body.append(f'<path d="{path}" fill="{fill}" stroke="{viz.token("surface")}" '
                    f'stroke-width="0.6"><title>{viz.esc(name)}: {viz.esc(label)}</title></path>')
    body.append("</g>")

    # Legend. Discrete bins, so the swatches are an ordinal ramp; the no-data
    # swatch sits well apart from it rather than at its light end, where it
    # would read as the lowest value rather than as an absence.
    legend_y = height - LEGEND_BAND + 18
    swatch = 22
    x = 0
    for index in range(len(breaks) + 1):
        body.append(f'<rect x="{x}" y="{legend_y}" width="{swatch}" height="10" '
                    f'fill="{viz.token(BINS[index])}"/>')
        x += swatch + 2
    ramp_right = x - 2
    # The bin edges, not "lower" and "higher". A reader looking at a shade wants
    # to know what it is worth, and a ramp without numbers makes them guess.
    if numbers:
        body.append(viz.text_el(0, legend_y + 24, formatter(min(numbers)), size=10.5,
                                fill="muted", tabular=True))
        body.append(viz.text_el(ramp_right, legend_y + 24, formatter(max(numbers)),
                                size=10.5, fill="muted", anchor="end", tabular=True))
        # Interior breaks, labelled where they fit without colliding.
        for index, edge in enumerate(breaks):
            edge_x = (swatch + 2) * (index + 1) - 1
            if 30 < edge_x < ramp_right - 30 and len(breaks) <= 2:
                body.append(viz.text_el(edge_x, legend_y + 24, formatter(edge),
                                        size=10.5, fill="muted", anchor="middle",
                                        tabular=True))
    else:
        body.append(viz.text_el(0, legend_y + 24, "lower", size=10.5, fill="muted"))
        body.append(viz.text_el(ramp_right, legend_y + 24, "higher", size=10.5,
                                fill="muted", anchor="end"))

    # The no-data swatch is only shown where something actually has no data.
    # Standing in the legend of a fully covered map, it invites a reader to hunt
    # for a gap that is not there.
    if covered < len(features):
        gap_x = ramp_right + 44
        body.append(f'<rect x="{gap_x}" y="{legend_y}" width="{swatch}" height="10" '
                    f'fill="{viz.token("nodata")}"/>')
        body.append(viz.text_el(gap_x + swatch + 8, legend_y + 9,
                                f"no data ({len(features) - covered})",
                                size=10.5, fill="muted"))

    coverage = f"{covered} of {len(features)} boundaries carry a value"
    body.append(viz.text_el(0, height - 8, note or coverage, size=11, fill="muted"))

    svg = viz.frame(width, height, top + "".join(body), f"{title}. {subtitle or ''} {coverage}")
    return {"svg": svg, "covered": covered, "polygons": len(features), "breaks": breaks}

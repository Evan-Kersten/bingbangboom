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
    # Recovered from precinct assignments rather than published by the Census,
    # so this layer is keyed on pid6 and covers only what the pilot reached.
    "special_district": ("geo/special_district.geojson", "pid6"),
}

BINS = ["bin-1", "bin-2", "bin-3", "bin-4", "bin-5"]

# The ramp a survey estimate is drawn on. Same number of steps and the same
# lightness progression as BINS, in a different hue, because §4 forbids reading
# a condition as a government's record and a picture drawn in the spending
# palette makes that reading anyway. The refusal has to be visible, not only
# written underneath.
SURVEY_BINS = ["survey-1", "survey-2", "survey-3", "survey-4", "survey-5"]


# Boundary files are read-only and parse to tens of megabytes of coordinates.
# Re-reading one per call is what made a whole-corpus export re-parse Oregon's
# 378 place polygons fifteen hundred times.
_LAYERS_LOADED = {}


def load_layer(layer, build=BUILD):
    key = (layer, build)
    if key not in _LAYERS_LOADED:
        path, property_key = LAYERS[layer]
        with open(os.path.join(build, path)) as fh:
            _LAYERS_LOADED[key] = (json.load(fh), property_key)
    return _LAYERS_LOADED[key]


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


def _path(geometry, project, tolerance=0.6, clip=None):
    """Emit an SVG path, dropping points closer together than a pixel or so.

    At map scale, coordinate precision beyond roughly half a pixel is invisible
    and costs a large multiple in file size.

    `clip` is a (width, height) frame, and a ring lying wholly outside it is
    dropped rather than emitted for the renderer to hide. This matters only for
    a framed map and it matters a great deal there: the tolerance is in output
    pixels, so scoping a point map to one county magnifies the whole state by
    about thirty and the simplification that removed most of Oregon's coastline
    at state scale removes nothing at all. Every scale answer in the static
    build carried 96 KB of thirty-five counties drawn off-canvas.
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
            if clip:
                xs = [x for x, _ in points]
                ys = [y for _, y in points]
                # A margin, because a ring that ends just off the edge still
                # contributes the stroke along it.
                if (max(xs) < -8 or min(xs) > clip[0] + 8
                        or max(ys) < -8 or min(ys) > clip[1] + 8):
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
               width=680, height=460, build=BUILD, note=None, only=None,
               highlight=None, bins=BINS):
    """`values` maps a layer geo_id to a number. Anything absent draws as no data.

    `only` restricts the drawing to a set of geo_ids, which is how a place map
    becomes readable: 378 Oregon cities at state scale are dots, and a reader
    comparing dot areas is comparing city land area, which means nothing here.

    `highlight` outlines one boundary. A choropleth of a peer group answers "how
    do these compare"; outlining the one being read about also answers "which of
    these is mine", which is the question a reader actually arrived with. The
    outline is a stroke rather than a different fill, because changing the fill
    would take the entity off the value ramp and hide its own figure.

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
    highlighted = None
    for feature in features:
        geo_id = feature["properties"].get(key)
        name = feature["properties"].get("NAME", geo_id)
        value = values.get(geo_id)
        if value is None:
            fill = viz.token("nodata")
            label = "no data"
        else:
            covered += 1
            fill = viz.token(bins[bin_index(value, breaks)])
            label = formatter(value)
        path = _path(feature["geometry"], project)
        if not path:
            continue
        # A hairline in the surface colour separates neighbours; it is a gap
        # doing the work, not a border drawn to outline the mark.
        body.append(f'<path d="{path}" fill="{fill}" stroke="{viz.token("surface")}" '
                    f'stroke-width="0.6"><title>{viz.esc(name)}: {viz.esc(label)}</title></path>')
        if highlight and geo_id == highlight:
            highlighted = path

    # Drawn last, inside the same transform, so a neighbour's fill cannot
    # overpaint the outline of the boundary the reader came here about.
    #
    # Two strokes, not one. A single dark outline disappears against the top bin
    # and a single light one disappears against the bottom, and the entity a
    # reader is looking for is as likely to be at one end as the other. The pale
    # casing underneath makes the dark line read on every step of the ramp, in
    # both themes.
    if highlighted:
        body.append(f'<path d="{highlighted}" fill="none" stroke="{viz.token("surface")}" '
                    f'stroke-width="4" stroke-linejoin="round" pointer-events="none"/>')
        body.append(f'<path d="{highlighted}" fill="none" stroke="{viz.token("ink")}" '
                    f'stroke-width="1.8" stroke-linejoin="round" pointer-events="none"/>')
    body.append("</g>")

    # Legend. Discrete bins, so the swatches are an ordinal ramp; the no-data
    # swatch sits well apart from it rather than at its light end, where it
    # would read as the lowest value rather than as an absence.
    legend_y = height - LEGEND_BAND + 18
    swatch = 22
    x = 0
    for index in range(len(breaks) + 1):
        body.append(f'<rect x="{x}" y="{legend_y}" width="{swatch}" height="10" '
                    f'fill="{viz.token(bins[index])}"/>')
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
    # Wrapped, with the frame grown to hold it. A note that runs off the right
    # edge is worse than none: the half a reader can see reads as the whole, and
    # this one carries the reason a geography is grey.
    lines = viz.wrap(note or coverage, 92)
    grown = height + max(0, len(lines) - 1) * 14
    y = grown - 8 - (len(lines) - 1) * 14
    for line in lines:
        body.append(viz.text_el(0, y, line, size=11, fill="muted"))
        y += 14

    svg = viz.frame(width, grown, top + "".join(body), f"{title}. {subtitle or ''} {coverage}")
    return {"svg": svg, "covered": covered, "polygons": len(features), "breaks": breaks}


# ----------------------------------------------------- electoral districts

# A district map is not a choropleth. Nothing is being measured, so nothing
# should be coloured as though it were: filling sixteen state house districts
# from a ramp invites a reader to rank them, and there is no quantity to rank.
# Identity is carried by the number at the centre of each shape, which is how a
# ballot names it and how every real district map works.
DISTRICT_BAND = 34

# Cycled, not ramped. Districts are numbered and adjacent numbers are usually
# adjacent ground, so stepping the hue each time is what keeps neighbours apart.
DISTRICT_TINTS = ["d1", "d2", "d3", "d4", "d5", "d6"]


def _centroid(geometry):
    """The centre of the largest ring, for placing a district's number.

    Area-weighted over the biggest polygon rather than over all of them: a
    district with an island would otherwise place its label in the water
    between the two, which is not inside the district at all.
    """
    best_ring, best_area = None, 0.0
    for polygon in geometry["coordinates"]:
        if not polygon:
            continue
        ring = polygon[0]
        area = 0.0
        for i in range(len(ring) - 1):
            area += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
        if abs(area) > best_area:
            best_ring, best_area = ring, abs(area)
    if not best_ring or best_area < 1e-12:
        return None

    cx = cy = 0.0
    for i in range(len(best_ring) - 1):
        cross = (best_ring[i][0] * best_ring[i + 1][1]
                 - best_ring[i + 1][0] * best_ring[i][1])
        cx += (best_ring[i][0] + best_ring[i + 1][0]) * cross
        cy += (best_ring[i][1] + best_ring[i + 1][1]) * cross
    signed = sum(best_ring[i][0] * best_ring[i + 1][1]
                 - best_ring[i + 1][0] * best_ring[i][1]
                 for i in range(len(best_ring) - 1)) / 2
    if abs(signed) < 1e-12:
        return None
    return (cx / (6 * signed), cy / (6 * signed))


def districts(features, title, subtitle, width=680, height=460, note=None,
              highlight=None, coverage_note=None):
    """Draw the pieces of ground that each elect one seat.

    `features` is a list of {"label", "name", "geometry"}. `highlight` names one
    of them, which is the question a reader actually arrives with: not "how are
    these arranged" but "which one is mine". The highlighted district takes the
    series colour and the rest stay quiet, because the others are context and
    colouring them equally would make the reader hunt.
    """
    drawn = [f for f in features if f.get("geometry")]
    if not drawn:
        return {"svg": viz.refusal(title, coverage_note or
                                   "No boundary for these districts is in this data."),
                "districts": 0, "highlighted": None}

    top, offset = viz.header(title, subtitle, width)
    map_height = height - offset - DISTRICT_BAND
    project = _project([{"geometry": f["geometry"]} for f in drawn], width, map_height)
    body = [f'<g transform="translate(0,{offset + 4})">']

    labels, found, position = [], None, 0
    for feature in drawn:
        path = _path(feature["geometry"], project)
        if not path:
            continue
        is_one = highlight is not None and feature["label"] == highlight
        if is_one:
            found = feature
        # No stroke, anywhere. A district is stored as the precincts it is built
        # from, and precinct splits carry slivers, so any stroke draws interior
        # seams as though they were boundaries a reader could vote across. Unstroked,
        # the precincts abut invisibly and the boundary appears exactly where the
        # fill changes — which is geometrically exact and needs no union.
        fill = viz.token("s1" if is_one else DISTRICT_TINTS[position % len(DISTRICT_TINTS)])
        position += 1
        body.append(
            f'<path d="{path}" fill="{fill}" stroke="none">'
            f'<title>{viz.esc(feature.get("name") or feature["label"])}</title></path>')
        centre = _centroid(feature["geometry"])
        if centre:
            labels.append((project(centre[0], centre[1]), feature["label"], is_one))

    # Numbers last and inside the same transform, so no neighbour's fill lands
    # on top of the one identifier this map has.
    for (x, y), label, is_one in labels:
        body.append(viz.text_el(x, y + 4, label, size=11.5, anchor="middle",
                                weight="600" if is_one else "500",
                                fill="surface" if is_one else "ink-2"))
    body.append("</g>")

    line = note or f"{fmt.count(len(drawn))} districts drawn."
    if coverage_note:
        line += " " + coverage_note
    # Wrapped and the frame grown to hold it. A note that runs off the right edge
    # is worse than no note, because the half a reader can see reads as the whole.
    lines = viz.wrap(line, 92)
    grown = height + max(0, (len(lines) - 1)) * 14
    y = grown - 10 - (len(lines) - 1) * 14
    for text in lines:
        body.append(viz.text_el(0, y, text, size=11, fill="muted"))
        y += 14

    svg = viz.frame(width, grown, top + "".join(body), f"{title}. {subtitle or ''} {line}")
    return {"svg": svg, "districts": len(drawn),
            "highlighted": found["label"] if found else None}


# ------------------------------------------------------------------ locator

# Two thirds of Oregon's governments are special districts and 1,010 of them
# have no boundary anywhere in this data. Until now the interface said so and
# stopped, which is honest and useless: a reader looking at a rural fire district
# still wants to know where in Oregon it is.
#
# So the fallback is not a blank. It is the state with the county the district
# files under picked out, said plainly as a filing rather than as a service area
# — §13.11 is explicit that published data assigns a district to the county
# holding most of its assessed value, so the host county under-captures where a
# district actually operates, and the pilot found districts serving Multnomah
# from a Washington County filing. Where service_extent measured the difference,
# both counties are drawn and the sentence says which is which.
LOCATOR_BAND = 46

# Below this many pixels across, a shape is drawn true and then ringed, because
# a one-pixel mark on a state map answers "where" with nothing.
MARKER_FLOOR = 18

# The state behind a locator is scenery, and it is inlined into every one of
# these — at the ordinary 0.6px tolerance that is 80 KB a page and 224 MB across
# the corpus, for coastline nobody reads at 680 pixels wide. Simplified this far
# Oregon is still unmistakably Oregon and costs a quarter of that.
BASE_TOLERANCE = 6.0


_BASE_MARKUP = {}


def _base_markup(base, project, width, height, dim_base, cache_key=None):
    """The state behind a locator, rendered once and reused.

    Identical on every one of these and 35 KB of path data each time. Cached on
    the frame it was drawn for, which used to be the only thing that varied.

    It is not any more. A point map framed to one county projects the same 36
    counties through a completely different transform, and the old key —
    width, height, dimness, count — could not tell those two apart, so the
    second caller got the first caller's paths at the wrong scale. `cache_key`
    is how a caller says which projection this is; passing None skips the cache
    entirely, which is right for a frame drawn once.
    """
    if cache_key is None:
        fill = viz.token("track" if dim_base else "nodata")
        parts = []
        for feature in base:
            path = _path(feature["geometry"], project, tolerance=BASE_TOLERANCE,
                         clip=(width, height))
            if path:
                parts.append(f'<path d="{path}" fill="{fill}" '
                             f'stroke="{viz.token("surface")}" stroke-width="0.6"/>')
        return "".join(parts)

    key = (width, height, dim_base, len(base), cache_key)
    if key not in _BASE_MARKUP:
        fill = viz.token("track" if dim_base else "nodata")
        parts = []
        for feature in base:
            path = _path(feature["geometry"], project, tolerance=BASE_TOLERANCE,
                         clip=(width, height))
            if path:
                parts.append(f'<path d="{path}" fill="{fill}" '
                             f'stroke="{viz.token("surface")}" stroke-width="0.6"/>')
        _BASE_MARKUP[key] = "".join(parts)
    return _BASE_MARKUP[key]


def _extent(geometry, project):
    """The projected bounding box of a geometry, or None if it has no points."""
    xs, ys = [], []
    for polygon in geometry["coordinates"]:
        for ring in polygon:
            for lon, lat in ring:
                x, y = project(lon, lat)
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def locator(base, subject, title, subtitle, width=680, height=420,
            note=None, dim_base=True):
    """Draw one government inside Oregon.

    `base` is the state's counties, drawn quietly for orientation. `subject` is
    what the reader came for: the government's own boundary where one exists,
    or the counties it is filed under where one does not. The difference between
    those two is the whole honesty of the picture, so the caller states it in
    `note` and this function never guesses.
    """
    drawn = [f for f in subject if f.get("geometry")]
    if not drawn:
        return {"svg": viz.refusal(title, note or "No boundary to draw."),
                "drawn": 0}

    top, offset = viz.header(title, subtitle, width)
    map_height = height - offset - LOCATOR_BAND
    # Projected over the base alone, not over base plus subject. Every subject is
    # inside Oregon, so including it cannot widen the frame — but it does make
    # the projection depend on which government is being drawn, which stops the
    # base from being computed once. Held constant, the state is rendered a
    # single time for the whole corpus instead of 1,529 times.
    project = _project([{"geometry": f["geometry"]} for f in base],
                       width, map_height)
    body = [f'<g transform="translate(0,{offset + 4})">']

    # The base is context, not measurement, and it is identical on all 1,529 of
    # these. Drawn at the ordinary tolerance it is 80 KB of path data per page
    # and 224 MB across the corpus, for detail nobody reads at state scale: the
    # reader is looking at which county is filled, not at the shape of its
    # riverbank. Simplified hard, it is a tenth of that and looks the same.
    body.append(_base_markup(base, project, width, map_height, dim_base))

    # A water district is a few hundred metres across and Oregon is 640 km. Drawn
    # honestly at state scale it is one pixel, which locates nothing. So the shape
    # is drawn true and a ring is added around anything too small to find, which
    # is what a locator map is for — the reader is asking where, not how big.
    marks = []
    for feature in drawn:
        path = _path(feature["geometry"], project)
        if not path:
            continue
        body.append(
            f'<path d="{path}" fill="{viz.token("s1")}" stroke="{viz.token("s1")}" '
            f'stroke-width="0.8"><title>{viz.esc(feature.get("name", ""))}</title></path>')
        box = _extent(feature["geometry"], project)
        if box and max(box[2] - box[0], box[3] - box[1]) < MARKER_FLOOR:
            marks.append(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2))

    for cx, cy in marks:
        body.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="11" fill="none" '
                    f'stroke="{viz.token("s1")}" stroke-width="1.6"/>')
        body.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="16" fill="none" '
                    f'stroke="{viz.token("s1")}" stroke-width="0.8" opacity="0.45"/>')
    body.append("</g>")

    lines = viz.wrap(note, 92) if note else []
    grown = height + max(0, len(lines) - 1) * 14
    y = grown - 12 - (len(lines) - 1) * 14
    for line in lines:
        body.append(viz.text_el(0, y, line, size=11, fill="muted"))
        y += 14

    svg = viz.frame(width, grown, top + "".join(body),
                    f"{title}. {subtitle or ''} {note or ''}")
    return {"svg": svg, "drawn": len(drawn)}


# The smallest and largest a pin may be drawn. Below the floor a circle is a
# speck and reads as a rendering artifact; above the ceiling one government
# covers three counties and the map becomes a picture of it alone.
PIN_MIN_R = 2.2
PIN_MAX_R = 11.0

# Five sizes against five quantiles, matching the choropleth's five colour bins.
# Radii, not areas, are spaced so the *areas* step evenly: a circle twice as wide
# reads as four times as much, and stepping the radius evenly is the standard
# way a symbol map overstates its top end.
PIN_LEGEND_BAND = 42
PIN_RADII = [round((PIN_MIN_R ** 2
                    + i * (PIN_MAX_R ** 2 - PIN_MIN_R ** 2) / 4) ** 0.5, 1)
             for i in range(5)]


def points(base, rows, title, subtitle, formatter=fmt.money, width=680,
           height=460, note=None, highlight_pid6=None, frame_to=None):
    """Governments as pins over the county outlines.

    The only form that can draw a special district at all. Two thirds of
    Oregon's governments are special districts, none of them has a boundary
    anywhere in this data, and a choropleth of the layers that do have one is a
    map with two thirds of the state's governments missing from it. A point is
    coarse and it is not nothing.

    A pin is an office. It is the centroid of the city on the government's
    mailing address, so a rural fire district filed at a Eugene address lands in
    Eugene and serves ground outside it entirely. Nothing about a dot on a map
    communicates that, which is why the caller is required to pass `note` and
    why the frame is drawn with a stated basis rather than a bare title.

    Area encodes magnitude, so the radius goes as the square root: a circle
    twice as wide reads as four times as much, and mapping the value to the
    radius directly is the standard way a proportional-symbol map overstates its
    top end. Colour is held constant for the same reason — one hue, one meaning,
    and size doing all the work.
    """
    placed = [r for r in rows if r.get("lon") is not None and r.get("value") is not None]
    if not placed:
        return {"svg": viz.refusal(title, note or "Nothing to place."), "drawn": 0}

    top, offset = viz.header(title, subtitle, width)
    map_height = height - offset - LOCATOR_BAND - PIN_LEGEND_BAND
    # Fit the frame to the extent asked for, not always to the state. A map
    # scoped to one county that draws all of Oregon spends 95% of the picture on
    # ground the reader excluded, and the pins they came for end up in a corner
    # a centimetre across. The base still carries every county, so the
    # neighbours stay visible at the edges and the county is not floating.
    fitted = frame_to or base
    project = _project([{"geometry": f["geometry"]} for f in fitted], width, map_height)
    # The base is every county even when the frame is one of them, so the
    # neighbours show at the edges and the county is not floating in white. That
    # means most of the base falls outside the frame, and without a clip it
    # paints over the title, the legend and the note beneath.
    clip = f"pf-pins-{abs(hash((width, map_height, len(fitted)))) % 100000}"
    body = [f'<clipPath id="{clip}"><rect x="0" y="0" width="{width}" '
            f'height="{map_height}"/></clipPath>',
            f'<g transform="translate(0,{offset + 4})" clip-path="url(#{clip})">']
    # Cached only when the frame is the whole state, which is the case that
    # repeats across the corpus. A county frame is drawn once and computing it
    # is cheaper than reasoning about whether its key is unique.
    body.append(_base_markup(base, project, width, map_height, True,
                             cache_key=None if frame_to else "state"))

    # The county the frame was fitted to, outlined. Fitting to its bounding box
    # brings several neighbours into view at that zoom, and without the outline
    # a reader cannot tell which of the shapes in front of them is the one they
    # scoped to, which makes every pin ambiguous about whether it is inside.
    for feature in (frame_to or []):
        path = _path(feature["geometry"], project, tolerance=BASE_TOLERANCE)
        if path:
            body.append(f'<path d="{path}" fill="{viz.token("nodata")}" '
                        f'stroke="{viz.token("ink-2")}" stroke-width="1.2"/>')

    # Quantile bins, not a continuous scale. Special-district spending runs from
    # a thousand dollars to nine hundred million, and any scale continuous in
    # the value — linear, or square-rooted for area — puts nine hundred of these
    # at the floor and one at the ceiling, which draws a map of Portland with
    # some dust around it. Five sizes against five quantiles is the same
    # decision the choropleth makes for colour, for the same reason, and it is
    # readable: a reader compares a pin to the legend, not to a ruler.
    values = sorted(r["value"] for r in placed)
    breaks = quantile_bins(values, len(PIN_RADII))
    # Eight hundred pins at the size that suits eighty is a green mass over the
    # Willamette Valley and nothing readable underneath it. The top of the scale
    # shrinks as the count grows, so a county of forty districts draws large and
    # legible and the whole state draws small and dense, which is what those two
    # maps respectively are.
    scale = 1.0 if len(placed) <= 120 else max(0.45, (120 / len(placed)) ** 0.4)
    radii = [max(1.4, r * scale) for r in PIN_RADII]
    radius = lambda value: radii[bin_index(value, breaks)]

    # Largest first so a small pin lands on top of a big one rather than
    # underneath it. Without this the dense end of the Willamette Valley reads
    # as three circles instead of forty.
    drawn = sorted(placed, key=lambda r: r["value"], reverse=True)
    marked = None
    for row in drawn:
        x, y = project(row["lon"], row["lat"])
        r = radius(row["value"])
        if highlight_pid6 and row.get("pid6") == highlight_pid6:
            marked = (x, y, r)
        body.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" '
            f'fill="{viz.token("s1")}" fill-opacity="0.45" '
            f'stroke="{viz.token("surface")}" stroke-width="0.4">'
            f'<title>{viz.esc(row.get("name", ""))}: {viz.esc(formatter(row["value"]))}'
            f'</title></circle>')
    if marked:
        x, y, r = marked
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 4:.1f}" fill="none" '
                    f'stroke="{viz.token("ink")}" stroke-width="1.4"/>')

    body.append("</g>")
    body.append(f'<g transform="translate(0,{offset + 4})">')

    # The legend sits under the map rather than on it. Oregon is widest across
    # the north and a legend in the corner of the frame lands on the coast
    # range, where the pins are densest.
    # Relative to the group, which is already translated down past the header.
    legend_y = map_height + 14
    # Indented by half a label. The first entry's text is centred on its circle,
    # and starting at zero put half of "0.227%" at negative x, where the frame
    # clipped it to ".227%" and invented a number.
    x = 16

    # De-duplicated by the label a reader actually sees. Quantile breaks collapse
    # whenever a measure is concentrated: nearly every fire district spends
    # nearly all of its budget on fire, so four of the five breaks format to
    # 100% and a legend printing 100% four times beside four different circles
    # says the sizes mean nothing while looking like it says they mean something.
    # Where the steps genuinely coincide there is one entry, which is true.
    seen, entries = set(), []
    for step, r in zip([values[0]] + list(breaks), radii):
        text = formatter(step)
        if text in seen:
            continue
        seen.add(text)
        entries.append((text, r))
    for text, r in entries:
        body.append(f'<circle cx="{x + PIN_MAX_R:.1f}" cy="{legend_y:.1f}" '
                    f'r="{r:.1f}" fill="{viz.token("s1")}" fill-opacity="0.55"/>')
        body.append(viz.text_el(x + PIN_MAX_R, legend_y + PIN_MAX_R + 12,
                                text, size=10, fill="muted", anchor="middle"))
        x += 2 * PIN_MAX_R + 46
    if len(entries) > 1:
        body.append(viz.text_el(x + 6, legend_y + 4, "and up", size=10, fill="muted"))
    else:
        # One distinct step means every pin is the same size and the sizing is
        # carrying nothing. Saying so is better than a legend of one circle.
        body.append(viz.text_el(x + 6, legend_y + 4,
                                "every one of these reports about the same share",
                                size=10, fill="muted"))
    body.append("</g>")

    lines = viz.wrap(note, 92) if note else []
    grown = height + max(0, len(lines) - 1) * 14
    y = grown - 12 - (len(lines) - 1) * 14
    for line in lines:
        body.append(viz.text_el(0, y, line, size=11, fill="muted"))
        y += 14

    svg = viz.frame(width, grown, top + "".join(body),
                    f"{title}. {subtitle or ''} {note or ''}")
    return {"svg": svg, "drawn": len(placed)}

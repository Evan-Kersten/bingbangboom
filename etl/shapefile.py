"""Minimal ESRI shapefile reader.

Pure standard library. Reads the .dbf attribute table and the .shp polygon
geometry, and emits GeoJSON. This exists so the geographic layer can be built
without a GIS dependency; in deployment DuckDB's spatial extension reads these
files directly and this module is only a fallback.

Handles shape types 5 (Polygon) and 3 (PolyLine), which covers every file in
the repository. Points and multipatch are not implemented.
"""

import struct


def read_dbf(path):
    """Return (fields, rows) from a dBASE III+ table."""
    with open(path, "rb") as fh:
        header = fh.read(32)
        record_count = struct.unpack("<I", header[4:8])[0]
        header_len = struct.unpack("<H", header[8:10])[0]
        record_len = struct.unpack("<H", header[10:12])[0]

        fields = []
        while True:
            descriptor = fh.read(32)
            if descriptor[0:1] in (b"\r", b""):
                break
            name = descriptor[0:11].split(b"\x00")[0].decode("latin-1")
            kind = chr(descriptor[11])
            size = descriptor[16]
            fields.append((name, kind, size))

        fh.seek(header_len)
        rows = []
        for _ in range(record_count):
            record = fh.read(record_len)
            if not record or record[0:1] == b"*":  # deleted
                continue
            offset = 1
            row = {}
            for name, kind, size in fields:
                raw = record[offset:offset + size].decode("latin-1").strip()
                offset += size
                if kind == "N" and raw:
                    try:
                        row[name] = float(raw) if "." in raw else int(raw)
                    except ValueError:
                        row[name] = None
                else:
                    row[name] = raw or None
            rows.append(row)
    return [f[0] for f in fields], rows


def _ring_area(ring):
    """Signed area. Negative is clockwise, which shapefiles use for outer rings."""
    total = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        total += (x2 - x1) * (y2 + y1)
    return total


def read_shp(path):
    """Return a list of GeoJSON geometry dicts, one per record, in file order."""
    with open(path, "rb") as fh:
        blob = fh.read()

    geometries = []
    pos = 100  # main file header
    while pos < len(blob):
        _, content_len = struct.unpack(">ii", blob[pos:pos + 8])
        pos += 8
        end = pos + content_len * 2
        shape_type = struct.unpack("<i", blob[pos:pos + 4])[0]

        if shape_type == 0:  # null shape
            geometries.append(None)
            pos = end
            continue

        if shape_type not in (3, 5):
            raise ValueError(f"unsupported shape type {shape_type} in {path}")

        cursor = pos + 4 + 32  # skip type and bounding box
        num_parts, num_points = struct.unpack("<ii", blob[cursor:cursor + 8])
        cursor += 8

        parts = list(struct.unpack(f"<{num_parts}i", blob[cursor:cursor + 4 * num_parts]))
        cursor += 4 * num_parts

        flat = struct.unpack(f"<{2 * num_points}d", blob[cursor:cursor + 16 * num_points])
        points = [(round(flat[i], 6), round(flat[i + 1], 6)) for i in range(0, len(flat), 2)]

        rings = []
        bounds = parts + [num_points]
        for i in range(num_parts):
            rings.append(points[bounds[i]:bounds[i + 1]])

        if shape_type == 3:
            geometries.append({"type": "MultiLineString", "coordinates": [list(map(list, r)) for r in rings]})
            pos = end
            continue

        # Group rings into polygons. A clockwise ring opens a new polygon;
        # counter-clockwise rings are holes in the polygon that precedes them.
        polygons = []
        for ring in rings:
            coords = [list(p) for p in ring]
            if _ring_area(ring) < 0 or not polygons:
                polygons.append([coords])
            else:
                polygons[-1].append(coords)

        geometries.append({"type": "MultiPolygon", "coordinates": polygons})
        pos = end

    return geometries


def read(path_without_extension):
    """Return a GeoJSON FeatureCollection for a shapefile base path."""
    _, rows = read_dbf(path_without_extension + ".dbf")
    geometries = read_shp(path_without_extension + ".shp")
    if len(rows) != len(geometries):
        raise ValueError(
            f"{path_without_extension}: {len(rows)} attribute rows but {len(geometries)} geometries"
        )
    features = [
        {"type": "Feature", "properties": props, "geometry": geom}
        for props, geom in zip(rows, geometries)
        if geom is not None
    ]
    return {"type": "FeatureCollection", "features": features}

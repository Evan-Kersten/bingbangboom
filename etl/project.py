#!/usr/bin/env python3
"""Lambert Conformal Conic inverse, for the one projected source in this repo.

The Multnomah precinct file is NAD83(HARN) / Oregon State Plane North, in
international feet. Everything else — the Census county, place and school
district layers — is WGS84 longitude and latitude. Mixed on one axis those are
not two coordinate systems, they are one enormous one: the state plane numbers
run into the millions, so projecting the pair together shrinks Oregon to a dot
in the corner and puts the district somewhere off frame.

That was invisible while the recovered districts were only ever drawn among
themselves, which is internally consistent. It appears the moment one of them
has to sit inside the state.

The parameters come from the file's own .prj rather than being assumed:

    PROJECTION      Lambert_Conformal_Conic
    False_Easting   8202099.737532808 (ft)
    Central_Meridian    -120.5
    Standard parallels   44°20', 46°00'
    Latitude of origin   43°40'
    Spheroid        GRS 1980
    Unit            international foot, 0.3048 m

NAD83(HARN) and WGS84 differ by well under a metre in Oregon, which is far
inside the precision a precinct boundary carries, so no datum shift is applied
and the omission is stated rather than hidden.

Snyder, *Map Projections — A Working Manual*, USGS PP 1395, pp. 104-110.
"""

import math

# GRS 1980.
SEMI_MAJOR = 6378137.0
INVERSE_FLATTENING = 298.257222101

FOOT = 0.3048

OREGON_NORTH = {
    "false_easting_ft": 8202099.737532808,
    "false_northing_ft": 0.0,
    "central_meridian": -120.5,
    "standard_parallel_1": 44.0 + 20.0 / 60.0,
    "standard_parallel_2": 46.0,
    "latitude_of_origin": 43.0 + 40.0 / 60.0,
}


def _t(phi, e):
    """Snyder 15-9. The isometric-latitude term the conic is built on."""
    sin_phi = math.sin(phi)
    return (math.tan(math.pi / 4 - phi / 2)
            / ((1 - e * sin_phi) / (1 + e * sin_phi)) ** (e / 2))


def _m(phi, e):
    """Snyder 14-15."""
    sin_phi = math.sin(phi)
    return math.cos(phi) / math.sqrt(1 - e * e * sin_phi * sin_phi)


def inverse(params=None, unit=FOOT):
    """Return a function mapping (easting, northing) to (longitude, latitude).

    The constants depend only on the projection parameters, so they are computed
    once and closed over: this runs per coordinate over a few hundred thousand of
    them, and recomputing two logarithms per point is the difference between a
    build step and a wait.
    """
    p = dict(OREGON_NORTH, **(params or {}))
    flattening = 1 / INVERSE_FLATTENING
    e = math.sqrt(2 * flattening - flattening * flattening)

    phi_1 = math.radians(p["standard_parallel_1"])
    phi_2 = math.radians(p["standard_parallel_2"])
    phi_0 = math.radians(p["latitude_of_origin"])
    lambda_0 = math.radians(p["central_meridian"])

    m_1, m_2 = _m(phi_1, e), _m(phi_2, e)
    t_0, t_1, t_2 = _t(phi_0, e), _t(phi_1, e), _t(phi_2, e)

    # Snyder 15-8: two standard parallels give the cone constant directly; equal
    # parallels would divide by zero, and this projection has two distinct ones.
    n = (math.log(m_1) - math.log(m_2)) / (math.log(t_1) - math.log(t_2))
    big_f = m_1 / (n * t_1 ** n)
    rho_0 = SEMI_MAJOR * big_f * t_0 ** n

    false_easting = p["false_easting_ft"] * unit
    false_northing = p["false_northing_ft"] * unit

    def to_lonlat(easting, northing):
        x = easting * unit - false_easting
        y = rho_0 - (northing * unit - false_northing)
        # Snyder 14-11/15-11: where n is negative the sign of x, y and rho is
        # reversed together. That is a negation, not copysign — copysign(x, n)
        # forces x to n's sign and silently mirrors every point across the
        # central meridian, which put Portland four degrees into eastern Oregon.
        if n < 0:
            x, y = -x, -y
        rho = math.hypot(x, y)
        if rho == 0:
            return math.degrees(lambda_0), 90.0 if n > 0 else -90.0
        theta = math.atan2(x, y)
        t = (rho / (SEMI_MAJOR * abs(big_f))) ** (1 / n)

        # Snyder 3-4 solved by iteration. Six rounds is far past convergence at
        # Oregon's eccentricity; the loop exits earlier on its own.
        phi = math.pi / 2 - 2 * math.atan(t)
        for _ in range(12):
            sin_phi = math.sin(phi)
            updated = math.pi / 2 - 2 * math.atan(
                t * ((1 - e * sin_phi) / (1 + e * sin_phi)) ** (e / 2))
            if abs(updated - phi) < 1e-12:
                phi = updated
                break
            phi = updated
        return math.degrees(theta / n + lambda_0), math.degrees(phi)

    return to_lonlat


def reproject_geometry(geometry, to_lonlat):
    """Rewrite a GeoJSON geometry's coordinates in place-safe fashion."""
    def walk(node):
        if isinstance(node[0], (int, float)):
            return list(to_lonlat(node[0], node[1]))
        return [walk(child) for child in node]

    if not geometry or not geometry.get("coordinates"):
        return geometry
    return dict(geometry, coordinates=walk(geometry["coordinates"]))

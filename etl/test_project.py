#!/usr/bin/env python3
"""The Lambert Conformal Conic inverse, against points whose answer is known.

    python3 etl/test_project.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import project

failures = []
checks = 0


def check(label, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        failures.append(label)


def main():
    to_lonlat = project.inverse()
    p = project.OREGON_NORTH

    # The false easting sits on the central meridian by construction, and zero
    # northing is the latitude of origin. Both are exact, so they are the test
    # that catches a transposed parameter.
    lon, lat = to_lonlat(p["false_easting_ft"], 0.0)
    check("the projection origin returns the central meridian",
          abs(lon - p["central_meridian"]) < 1e-9, str(lon))
    check("and the latitude of origin",
          abs(lat - p["latitude_of_origin"]) < 1e-6, str(lat))

    # A real vertex from the precinct file, in Portland. The bug this test was
    # written for put it at -118.46, four degrees into eastern Oregon, because
    # copysign(x, n) forces x to the cone constant's sign and mirrors every point
    # across the central meridian.
    lon, lat = to_lonlat(7678756.030512, 703492.128937)
    check("a Portland vertex lands west of the central meridian, not east",
          lon < p["central_meridian"], str(lon))
    check("and lands in Portland", -122.9 < lon < -122.3 and 45.3 < lat < 45.8,
          f"({lon}, {lat})")

    # Everything in the source file must land inside Oregon's envelope.
    check("east of the meridian stays east",
          to_lonlat(p["false_easting_ft"] + 100000, 0.0)[0] > p["central_meridian"])
    check("north of the origin stays north",
          to_lonlat(p["false_easting_ft"], 500000)[1] > p["latitude_of_origin"])

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Chart rendering.

The model picks a form and names an entity. It never supplies a title, an axis
label, a color or a number. Everything visible is derived here from the same
tool result that produced the prose, which is what keeps the chart and the
sentence in agreement, and what stops a caption asserting something §4 forbids.
A chart titled "public safety investment and poverty" claims a relationship more
forcefully than a sentence would, and no prompt instruction reliably prevents a
model from writing it. Not having the parameter does.

Charts carry the same refusals as the tools. A bar drawn against a degenerate
peer median is exactly as wrong as saying "2.7 times the median", and worse in
practice, because a picture reads as measurement.

Palette values are the validated reference instance, checked with the skill's
validator against both surfaces:

    categorical slots 1-2   PASS light and dark, worst adjacent CVD ΔE 24.7
    choropleth bins         PASS ordinal gates in both modes

Colors are emitted as CSS custom properties with hex fallbacks, so a page that
defines the tokens gets dark mode automatically and a standalone SVG still
renders in light.
"""

import html
import math
import re

import format as fmt

# ---------------------------------------------------------------- palette

# Public Foundry's identity: deep green ink, a working green for data, and a
# pale mint ground. Validated with the dataviz checker against both surfaces.
#
#   categorical 4-series   PASS light and dark
#   choropleth bins        PASS the ordinal gates in both
#
# Green leads because it is the brand, and blue and orange follow because green
# beside orange collides under deutan while green beside blue does not. That
# ordering is the colourblind-safety mechanism, not a preference.
TOKENS = {
    "surface": ("#FBFDFC", "#0E1512"),
    "panel": ("#FFFFFF", "#151E1A"),
    "page": ("#F2FAF5", "#0A100E"),
    "ink": ("#0B3B2A", "#E8F3ED"),
    "ink-2": ("#3D5C4E", "#A7C4B5"),
    "muted": ("#6B8578", "#7E9A8B"),
    "grid": ("#DCEBE2", "#22302A"),
    "axis": ("#B9D3C5", "#2E4038"),
    "s1": ("#1B7A4C", "#2E9E63"),
    "s2": ("#2A78D6", "#3987E5"),
    "s3": ("#EB6834", "#D95926"),
    "s4": ("#4A3AA7", "#9085E9"),
    "dim": ("#B9D3C5", "#3D5C4E"),
    # District tints. A district map encodes no quantity, so these are a cycle
    # rather than a ramp: nothing here is more than anything else, and a
    # sequential palette would invite a reader to rank pieces of ground. They
    # only have to separate a district from its neighbours — the number at the
    # centre carries identity — so they sit close in lightness and apart in hue.
    "d1": ("#CFE8DA", "#1E4636"),
    "d2": ("#B9DCEA", "#1B3E4E"),
    "d3": ("#E8DAC2", "#463A2B"),
    "d4": ("#D9D0EA", "#332B4E"),
    "d5": ("#CBE4C1", "#2B4427"),
    "d6": ("#EFD5CE", "#4A2F2B"),
    "good": ("#0CA30C", "#0CA30C"),
    "warning": ("#B8791B", "#D2963F"),
    "serious": ("#C4652F", "#E08A55"),
    "critical": ("#A33A32", "#D9736A"),
    "track": ("#E4F1EA", "#22302A"),
    "nodata": ("#DCEBE2", "#22302A"),
    "bin-1": ("#6BC79A", "#125635"),
    "bin-2": ("#43B37F", "#1B7A4C"),
    "bin-3": ("#2E9E63", "#2E9E63"),
    "bin-4": ("#1B7A4C", "#5CC493"),
    "bin-5": ("#125635", "#8FD9B4"),
    # A second ordinal ramp, for what a survey says about a population rather
    # than what a government spends. §4 forbids reading a condition as a
    # government's record, and the caveats say so — but a poverty map drawn in
    # the same greens as a spending map, one scroll apart, invites exactly the
    # reading the sentence underneath is refusing. Different hue, same number of
    # steps, same lightness progression: comparable within itself and obviously
    # not the other thing.
    "survey-1": ("#C9CEE8", "#232A4B"),
    "survey-2": ("#A3ABD8", "#333C69"),
    "survey-3": ("#7C87C4", "#4C5790"),
    "survey-4": ("#5A64A8", "#7C87C4"),
    "survey-5": ("#3C447E", "#A3ABD8"),
}

FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"


def css(selector=".pf-viz"):
    """The token block a host page drops in to get both themes."""
    light = "\n".join(f"  --pf-{name}: {values[0]};" for name, values in TOKENS.items())
    dark = "\n".join(f"    --pf-{name}: {values[1]};" for name, values in TOKENS.items())
    return (f"{selector} {{\n  color-scheme: light;\n{light}\n}}\n"
            f"@media (prefers-color-scheme: dark) {{\n"
            f"  :root:where(:not([data-theme=\"light\"])) {selector} {{\n"
            f"    color-scheme: dark;\n{dark}\n  }}\n}}\n"
            f":root[data-theme=\"dark\"] {selector} {{\n  color-scheme: dark;\n{dark}\n}}\n")


def token(name):
    """A CSS variable with the light value as fallback for standalone SVG."""
    return f"var(--pf-{name}, {TOKENS[name][0]})"


# ------------------------------------------------------------- primitives

def esc(text):
    return html.escape(str(text), quote=True)


def truncate(text, limit=26):
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def text_el(x, y, content, size=12, fill="ink-2", anchor="start", weight="400", tabular=False):
    """Text always wears a text token, never a series color."""
    numeric = ' font-variant-numeric="tabular-nums"' if tabular else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{token(fill)}" text-anchor="{anchor}"'
            f'{numeric}>{esc(content)}</text>')


def bar_path(x, y, width, height, radius=4):
    """Horizontal bar: square at the baseline, 4px rounded at the data end."""
    radius = max(0.0, min(radius, width, height / 2))
    if width <= 0.5:
        return f'<path d="M{x:.1f},{y:.1f} h0.5 v{height:.1f} h-0.5 Z"'
    right = x + width
    return (f'<path d="M{x:.1f},{y:.1f} H{right - radius:.1f} '
            f'A{radius:.1f},{radius:.1f} 0 0 1 {right:.1f},{y + radius:.1f} '
            f'V{y + height - radius:.1f} '
            f'A{radius:.1f},{radius:.1f} 0 0 1 {right - radius:.1f},{y + height:.1f} '
            f'H{x:.1f} Z"')


def frame(width, height, body, label):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="100%" style="max-width:{width}px;height:auto;display:block" '
            f'role="img" aria-label="{esc(label)}" class="pf-viz">{body}</svg>')


def append_note(svg, note, columns=86):
    """Add a line under a finished chart, growing the frame to hold it.

    Used for facts about the drawing that only exist once the drawing is done —
    chiefly a government the reader picked that the chart could not draw. That
    belongs on the picture, because the picture is what gets read and screenshot,
    and a caveat in a panel underneath does not travel with it.
    """
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    if not match:
        return svg
    width, height = float(match.group(1)), float(match.group(2))
    lines = wrap(note, columns)
    grown = height + 6 + len(lines) * 15

    extra = []
    y = height + 14
    for line in lines:
        extra.append(text_el(0, y, line, size=11, fill="muted"))
        y += 15

    svg = svg.replace(f'viewBox="0 0 {match.group(1)} {match.group(2)}"',
                      f'viewBox="0 0 {match.group(1)} {grown:g}"', 1)
    return svg.replace("</svg>", "".join(extra) + "</svg>", 1)


def header(title, subtitle, width):
    parts = [text_el(0, 16, title, size=14, fill="ink", weight="600")]
    if subtitle:
        parts.append(text_el(0, 34, subtitle, size=11.5, fill="muted"))
    return "".join(parts), (46 if subtitle else 28)


# ------------------------------------------------------------------ forms

BAR_THICKNESS = 24          # cap, never fill the slot
BAR_GAP = 2                 # surface gap between adjacent bars
LABEL_COLUMN = 190
VALUE_COLUMN = 78
# The label column is 180px of usable width at 12px. Twenty-six characters of
# "Housing & Community Development" overran it and clipped against the frame.
BAR_LABEL_CHARS = 23


def diverging_bars(title, subtitle, rows, formatter=fmt.percent, width=680,
                   note=None, peer_key="peer_median"):
    """Change against a zero line, which is where the reader's eye belongs.

    A change chart needs a real origin. Running it through the ordinary bar form
    draws a shrinking government as a hairline at the left edge, which loses the
    size of the fall and reads as "almost nothing" rather than "down twelve per
    cent" — the opposite of the finding. Here zero sits inside the plot and bars
    grow away from it in the direction they actually moved.

    Growth and decline share one hue rather than taking green and red. Neither
    direction is good news on its own: a government that spent less may have
    finished a capital project or may have cut a service, and this data cannot
    tell which (§4).
    """
    rows = [r for r in rows if r.get("value") is not None]
    if not rows:
        return None

    top, offset = header(title, subtitle, width)
    slot = BAR_THICKNESS + BAR_GAP + 8
    plot_left = LABEL_COLUMN
    plot_width = width - LABEL_COLUMN - VALUE_COLUMN

    peers = [r[peer_key] for r in rows if r.get(peer_key) is not None]
    values = [r["value"] for r in rows] + peers
    low, high = min(values + [0]), max(values + [0])
    span = (high - low) or 1
    zero_x = plot_left + (0 - low) / span * plot_width
    px = lambda v: plot_left + (v - low) / span * plot_width

    body = [top]
    y = offset + 8
    for row in rows:
        x = px(row["value"])
        left, right = min(zero_x, x), max(zero_x, x)
        body.append(text_el(plot_left - 10, y + BAR_THICKNESS / 2 + 4,
                            truncate(row["label"], BAR_LABEL_CHARS), anchor="end"))
        body.append(bar_path(left, y, max(right - left, 0.5), BAR_THICKNESS, radius=3)
                    + f' fill="{token("s1")}">'
                    + f'<title>{esc(row["label"])}: {esc(formatter(row["value"]))}</title>'
                    + "</path>")
        # The peer median as a tick on the same scale, so a bar is read against
        # its own type rather than against the other bars in the picture.
        if row.get(peer_key) is not None:
            tick = px(row[peer_key])
            body.append(f'<line x1="{tick:.1f}" y1="{y - 2}" x2="{tick:.1f}" '
                        f'y2="{y + BAR_THICKNESS + 2}" stroke="{token("ink-2")}" '
                        f'stroke-width="1.5"><title>peer median '
                        f'{esc(formatter(row[peer_key]))}</title></line>')
        body.append(text_el(width - VALUE_COLUMN + 8, y + BAR_THICKNESS / 2 + 4,
                            formatter(row["value"]), size=11.5, tabular=True))
        y += slot

    # Drawn over the bars: zero is the reference every bar is measured from, and
    # a bar crossing it would otherwise hide where it started.
    body.append(f'<line x1="{zero_x:.1f}" y1="{offset + 2}" x2="{zero_x:.1f}" '
                f'y2="{y - BAR_GAP - 6}" stroke="{token("axis")}" stroke-width="1.5"/>')
    body.append(text_el(zero_x, y + 8, "no change", size=10.5, fill="muted",
                        anchor="middle"))
    y += 20

    if peers:
        body.append(text_el(plot_left, y + 10,
                            "| tick marks the median change for that government type",
                            size=10.5, fill="muted"))
        y += 16
    if note:
        for line in wrap(note, 92):
            body.append(text_el(0, y + 12, line, size=11, fill="muted"))
            y += 14
        y += 4

    return frame(width, y + 12, "".join(body), f"{title}. {subtitle or ''}")


def horizontal_bars(title, subtitle, rows, formatter=fmt.money, width=680,
                    emphasis=None, note=None, peer_key="peer_median"):
    """One hue for every bar: these categories are nominal, so a value ramp
    would double-encode bar length as hue and burn the only free channel.

    `emphasis` names a row to keep in the accent while the rest recede, for the
    case where one row is the point rather than the set being the subject.
    """
    rows = [r for r in rows if r.get("value") is not None]
    if not rows:
        return None

    top, offset = header(title, subtitle, width)
    slot = BAR_THICKNESS + BAR_GAP + 8
    plot_left = LABEL_COLUMN
    plot_width = width - LABEL_COLUMN - VALUE_COLUMN
    # The ticks share the bars' scale, so they set it too. Scaling to the bars
    # alone clamps any median above the longest bar to the plot edge, where it
    # reads as equal to the largest value rather than beyond it — which is the
    # opposite of what the comparison says.
    drawn_peers = [abs(r[peer_key]) for r in rows
                   if r.get(peer_key) is not None and not r.get("peer_degenerate")]
    largest = max([abs(r["value"]) for r in rows] + drawn_peers) or 1

    body = [top]
    y = offset + 8
    for row in rows:
        length = max(0.0, abs(row["value"]) / largest * plot_width)
        colour = "s1" if (emphasis is None or row["label"] == emphasis) else "dim"
        body.append(text_el(plot_left - 10, y + BAR_THICKNESS / 2 + 4,
                            truncate(row["label"], BAR_LABEL_CHARS), anchor="end"))
        body.append(f'{bar_path(plot_left, y, length, BAR_THICKNESS)} '
                    f'fill="{token(colour)}"><title>{esc(row["label"])}: '
                    f'{esc(formatter(row["value"]))}</title></path>')
        # The value rides outside the bar end, so it is never clipped by a
        # short bar and never needs a fit measurement.
        # A peer median rides on the bar as a tick, so the reader sees the
        # comparison without a second chart. Suppressed where the distribution
        # is degenerate, since the tick would then assert a midpoint that is not
        # one.
        peer = row.get(peer_key)
        if peer is not None and not row.get("peer_degenerate") and largest:
            tick = plot_left + min(abs(peer) / largest * plot_width, plot_width)
            body.append(f'<line x1="{tick:.1f}" y1="{y - 2}" x2="{tick:.1f}" '
                        f'y2="{y + BAR_THICKNESS + 2}" stroke="{token("ink-2")}" '
                        f'stroke-width="1.5"><title>peer median '
                        f'{esc(formatter(peer))}</title></line>')

        body.append(text_el(plot_left + length + 8, y + BAR_THICKNESS / 2 + 4,
                            formatter(row["value"]), size=11.5, tabular=True))
        y += slot

    if any(r.get(peer_key) is not None and not r.get("peer_degenerate") for r in rows):
        y += 4
        body.append(f'<line x1="0" y1="{y + 4}" x2="0" y2="{y + 14}" '
                    f'stroke="{token("ink-2")}" stroke-width="1.5"/>')
        body.append(text_el(8, y + 13, "peer median for this government type",
                            size=11, fill="muted"))
        y += 18

    if note:
        y += 6
        # A note is prose and runs past the frame if it is set as one line. The
        # column is the full width at 11px, which is about 96 characters.
        for line in wrap(note, 96):
            body.append(text_el(0, y + 8, line, size=11, fill="muted"))
            y += 14
        y += 2

    return frame(width, y + 8, "".join(body), f"{title}. {subtitle or ''}".strip())


def meters(title, subtitle, rows, width=680):
    """A ratio against a limit is a meter, not a bar chart. The unfilled track
    is a lighter step of the same ramp so state reads across the whole bar."""
    top, offset = header(title, subtitle, width)
    track_height = 10
    plot_left = LABEL_COLUMN
    plot_width = width - LABEL_COLUMN - VALUE_COLUMN

    body = [top]
    y = offset + 10
    for row in rows:
        score = row["value"]
        if score is None:
            continue
        fill_width = max(0.0, min(1.0, score / 100)) * plot_width
        severity = ("critical" if score < 25 else "serious" if score < 50
                    else "warning" if score < 75 else "good")
        body.append(text_el(plot_left - 10, y + track_height, truncate(row["label"]),
                            anchor="end"))
        body.append(f'<rect x="{plot_left}" y="{y + 2}" width="{plot_width}" '
                    f'height="{track_height}" rx="{track_height / 2}" '
                    f'fill="{token("track")}"/>')
        body.append(f'{bar_path(plot_left, y + 2, fill_width, track_height, radius=track_height / 2)} '
                    f'fill="{token(severity)}"><title>{esc(row["label"])}: '
                    f'{score:.0f} of 100</title></path>')
        body.append(text_el(plot_left + plot_width + 8, y + track_height,
                            f"{score:.0f}", size=11.5, tabular=True))
        if row.get("detail"):
            body.append(text_el(plot_left, y + track_height + 18, row["detail"],
                                size=11, fill="muted"))
            y += 18
        y += 30

    return frame(width, y + 4, "".join(body), f"{title}. {subtitle or ''}".strip())


def peer_position(title, subtitle, value, low, median, high, low_name, high_name,
                  formatter=fmt.percent, width=680):
    """One value against its peer range. Emphasis: the entity is the accent, the
    range is context. Callers must not reach this when the median is degenerate;
    render_chart refuses first."""
    top, offset = header(title, subtitle, width)
    plot_left, plot_width = 24, width - 48
    span = (high - low) or 1

    def place(v):
        return plot_left + (max(low, min(high, v)) - low) / span * plot_width

    y = offset + 26
    body = [top]
    body.append(f'<line x1="{plot_left}" y1="{y}" x2="{plot_left + plot_width}" y2="{y}" '
                f'stroke="{token("axis")}" stroke-width="1"/>')

    def anchor_for(x, label_width):
        """Keep a centred label inside the plot at either end."""
        if x - label_width / 2 < plot_left:
            return "start", plot_left
        if x + label_width / 2 > plot_left + plot_width:
            return "end", plot_left + plot_width
        return "middle", x

    median_x = place(median)
    body.append(f'<line x1="{median_x:.1f}" y1="{y - 9}" x2="{median_x:.1f}" y2="{y + 9}" '
                f'stroke="{token("muted")}" stroke-width="1"/>')
    median_label = f"peer median {formatter(median)}"
    anchor, label_x = anchor_for(median_x, len(median_label) * 5.8)
    body.append(text_el(label_x, y + 26, median_label, size=11, fill="muted", anchor=anchor))

    entity_x = place(value)
    # 2px surface ring keeps the marker legible where it crosses the axis.
    body.append(f'<circle cx="{entity_x:.1f}" cy="{y}" r="7" fill="{token("surface")}"/>')
    body.append(f'<circle cx="{entity_x:.1f}" cy="{y}" r="5" fill="{token("s1")}">'
                f'<title>this entity: {esc(formatter(value))}</title></circle>')
    value_label = formatter(value)
    anchor, label_x = anchor_for(entity_x, len(value_label) * 6.6)
    body.append(text_el(label_x, y - 14, value_label, size=12, fill="ink",
                        weight="600", anchor=anchor, tabular=True))

    body.append(text_el(plot_left, y + 44, f"{formatter(low)}  {truncate(low_name, 22)}",
                        size=11, fill="muted"))
    body.append(text_el(plot_left + plot_width, y + 44,
                        f"{truncate(high_name, 22)}  {formatter(high)}",
                        size=11, fill="muted", anchor="end"))

    return frame(width, y + 60, "".join(body), f"{title}. {subtitle or ''}".strip())


def time_series(title, subtitle, series, formatter=fmt.money, width=680, height=250):
    """Two measures of the same unit on one axis. Never a second y-scale: the
    alignment of two scales is arbitrary and invents a relationship."""
    series = [s for s in series if len(s["points"]) > 1]
    if not series:
        return None

    top, offset = header(title, subtitle, width)
    # The bottom band has to hold the tick row and the legend row, or the two
    # land on each other. Sizing the plot around them is the fix; shrinking the
    # font is not.
    left, right = 64, 150
    tick_band, legend_band = 24, 30
    plot_top = offset + 26
    plot_height = height - plot_top - tick_band - legend_band
    plot_width = width - left - right

    years = sorted({p[0] for s in series for p in s["points"]})
    values = [p[1] for s in series for p in s["points"] if p[1] is not None]
    if not years or not values:
        return None
    top_value = max(values) or 1
    year_span = (years[-1] - years[0]) or 1

    def px(year):
        return left + (year - years[0]) / year_span * plot_width

    def py(value):
        return plot_top + plot_height - (value / top_value) * plot_height

    body = [top]
    for step in range(5):
        value = top_value * step / 4
        gy = py(value)
        body.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{left + plot_width}" y2="{gy:.1f}" '
                    f'stroke="{token("grid")}" stroke-width="1"/>')
        body.append(text_el(left - 10, gy + 4, formatter(value), size=10.5,
                            fill="muted", anchor="end", tabular=True))

    # Thin the tick labels rather than letting them overlap. Years land where the
    # data puts them, so an irregular series crowds its early points.
    shown, last_x = [], None
    for year in years:
        x = px(year)
        if last_x is None or x - last_x >= 42 or year == years[-1]:
            if shown and year == years[-1] and x - last_x < 42:
                shown.pop()
            shown.append(year)
            last_x = x
    for year in shown:
        body.append(text_el(px(year), plot_top + plot_height + 18, str(year),
                            size=10.5, fill="muted", anchor="middle", tabular=True))

    # A slot each. Two series used to hard-code s1/s2, which drew a third in the
    # same blue as the second — and the third here is a *different measure of
    # expenditure*, so two indistinguishable lines was the one outcome this
    # chart could not afford.
    slots = ["s1", "s2", "s3", "s4"]
    for index, entry in enumerate(series):
        colour = slots[index % len(slots)]
        points = [(px(y), py(v)) for y, v in entry["points"] if v is not None]
        path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
                        for i, (x, y) in enumerate(points))
        body.append(f'<path d="{path}" fill="none" stroke="{token(colour)}" '
                    f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
        for (x, y), (year, value) in zip(points, [p for p in entry["points"] if p[1] is not None]):
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="transparent">'
                        f'<title>{esc(entry["label"])} {year}: {esc(formatter(value))}</title>'
                        f'</circle>')
        # Direct-label the endpoint only. A value on every point goes unread.
        end_x, end_y = points[-1]
        body.append(f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="6" fill="{token("surface")}"/>')
        body.append(f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="4" fill="{token(colour)}"/>')
        body.append(text_el(end_x + 10, end_y + 4, truncate(entry["label"], 20),
                            size=11, fill="ink-2"))

    # A legend is always present for two or more series, on its own row below
    # the tick band.
    legend_y = plot_top + plot_height + tick_band + 14
    x = left
    for index, entry in enumerate(series):
        colour = slots[index % len(slots)]
        body.append(f'<rect x="{x}" y="{legend_y - 8}" width="10" height="10" rx="2" '
                    f'fill="{token(colour)}"/>')
        body.append(text_el(x + 16, legend_y + 1, entry["label"], size=11, fill="ink-2"))
        x += 22 + len(entry["label"]) * 6.4

    return frame(width, height, "".join(body), f"{title}. {subtitle or ''}".strip())


def nice_ticks(low, high, count=4):
    """Round gridline values covering a range.

    An axis labelled $905, $1,810, $2,715 is arithmetically correct and reads as
    noise; the numbers are there to be compared against, so they land on 1, 2,
    2.5 or 5 times a power of ten.
    """
    span = (high - low) or abs(high) or 1
    raw = span / max(count, 1)
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for multiple in (1, 2, 2.5, 5, 10):
        step = multiple * magnitude
        if raw <= step:
            break
    start = math.floor(low / step) * step
    ticks, value = [], start
    while value <= high + step * 0.001:
        ticks.append(round(value, 10))
        value += step
    return ticks or [low, high]


def wrap(text, columns=84):
    lines, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > columns:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


def refusal(title, reason, width=680):
    """A chart that must not be drawn still returns something, because a blank
    space reads as a failure while a stated reason reads as a rule."""
    lines = wrap(reason)
    body = [text_el(0, 16, title, size=14, fill="ink", weight="600")]
    y = 40
    for line in lines:
        body.append(text_el(14, y, line, size=12, fill="ink-2"))
        y += 17
    body.append(f'<rect x="0" y="26" width="3" height="{y - 40}" rx="1.5" '
                f'fill="{token("warning")}"/>')
    return frame(width, y, "".join(body), f"{title}. {reason}")


# ------------------------------------------------------- peer distribution

def peer_range(title, subtitle, value, stats, formatter=fmt.money, width=680,
               label="this entity"):
    """Where one entity sits in its peer distribution.

    Low, quartiles, median and high as a banded track with the entity marked,
    which is the form the product already uses for a score and which generalises
    to any metric. The quartile band is what stops a reader treating the median
    as a target: half the pool sits inside it.
    """
    top, offset = header(title, subtitle, width)
    left, plot_width = 20, width - 40
    low, high = stats["low"], stats["high"]
    span = (high - low) or 1

    def place(v):
        return left + (max(low, min(high, v)) - low) / span * plot_width

    y = offset + 30
    body = [top]

    # Full range as a recessive track, interquartile as the solid band.
    body.append(f'<rect x="{left}" y="{y - 5}" width="{plot_width}" height="10" rx="5" '
                f'fill="{token("track")}"/>')
    q1, q3 = place(stats["p25"]), place(stats["p75"])
    body.append(f'<rect x="{q1:.1f}" y="{y - 5}" width="{max(2, q3 - q1):.1f}" height="10" '
                f'rx="5" fill="{token("dim")}"><title>middle half of the peer group: '
                f'{esc(formatter(stats["p25"]))} to {esc(formatter(stats["p75"]))}</title></rect>')

    median_x = place(stats["median"])
    body.append(f'<line x1="{median_x:.1f}" y1="{y - 11}" x2="{median_x:.1f}" y2="{y + 11}" '
                f'stroke="{token("ink-2")}" stroke-width="2"/>')

    entity_x = place(value)
    body.append(f'<circle cx="{entity_x:.1f}" cy="{y}" r="8" fill="{token("surface")}"/>')
    body.append(f'<circle cx="{entity_x:.1f}" cy="{y}" r="6" fill="{token("s1")}">'
                f'<title>{esc(label)}: {esc(formatter(value))}</title></circle>')

    def anchored(x, text_value, text_y, size, fill, weight="400"):
        half = len(str(text_value)) * size * 0.29
        anchor, place_x = "middle", x
        if x - half < left:
            anchor, place_x = "start", left
        elif x + half > left + plot_width:
            anchor, place_x = "end", left + plot_width
        return text_el(place_x, text_y, text_value, size=size, fill=fill,
                       anchor=anchor, weight=weight, tabular=True)

    body.append(anchored(entity_x, formatter(value), y - 18, 13, "ink", "600"))
    body.append(anchored(median_x, f"median {formatter(stats['median'])}", y + 28, 11, "muted"))

    body.append(text_el(left, y + 48, formatter(low), size=10.5, fill="muted", tabular=True))
    body.append(text_el(left + plot_width, y + 48, formatter(high), size=10.5,
                        fill="muted", anchor="end", tabular=True))
    body.append(text_el(left, y + 64, f"across {fmt.count(stats['n'])} {stats['peer_group']} peers",
                        size=11, fill="muted"))

    return frame(width, y + 76, "".join(body),
                 f"{title}. {subtitle or ''} Entity at {formatter(value)}, "
                 f"peer median {formatter(stats['median'])}.")


def multi_series(title, subtitle, series, formatter=fmt.money, width=680, height=300,
                 note=None, reference=None, baseline=None):
    """Several entities on one measure over time.

    One axis, one unit, up to four series. Past four the lines converge and the
    legend stops carrying identity, so the caller folds the tail rather than
    generating a fifth hue.

    A reference series draws dashed and recessive behind the rest. It is a
    baseline the other lines are read against, not a fifth entity, and giving it
    a hue of its own would make it compete with them.

    `baseline` sets the value the y-axis is anchored on, for indexed series where
    100 rather than zero is the reference. Passing it releases the zero floor, so
    it is only correct where the number genuinely has a non-zero origin.
    """
    series = [s for s in series if len([p for p in s["points"] if p[1] is not None]) > 1]
    if not series:
        return None
    series = series[:4]
    if reference and len([p for p in reference["points"] if p[1] is not None]) < 2:
        reference = None

    top, offset = header(title, subtitle, width)
    left, right = 68, 150
    tick_band, legend_band = 24, 30 + (18 if note else 0)
    plot_top = offset + 26
    plot_height = height - plot_top - tick_band - legend_band
    plot_width = width - left - right

    lines = series + ([reference] if reference else [])
    years = sorted({p[0] for s in lines for p in s["points"] if p[1] is not None})
    values = [p[1] for s in lines for p in s["points"] if p[1] is not None]
    year_span = (years[-1] - years[0]) or 1

    # A zero baseline is the default because bar-like comparisons of level need
    # one. An index does not: it is read against its own base of 100, and holding
    # the floor at zero pins every line into the top of the plot and hides the
    # divergence the chart exists to show. `baseline` names the reference the
    # axis is honest about; None means zero.
    if baseline is not None:
        floor = min(values + [baseline]) * 0.97
        ceiling = max(values + [baseline]) * 1.03
    else:
        floor, ceiling = 0, max(values) or 1
    domain = (ceiling - floor) or 1

    px = lambda year: left + (year - years[0]) / year_span * plot_width
    py = lambda v: plot_top + plot_height - ((v - floor) / domain) * plot_height

    body = [top]
    for v in nice_ticks(floor, ceiling, 4):
        if v < floor:
            continue
        gy = py(v)
        emphasised = baseline is not None and abs(v - baseline) < 1e-9
        body.append(f'<line x1="{left}" y1="{gy:.1f}" x2="{left + plot_width}" y2="{gy:.1f}" '
                    f'stroke="{token("axis" if emphasised else "grid")}" stroke-width="1"/>')
        body.append(text_el(left - 10, gy + 4, formatter(v), size=10.5,
                            fill="ink-2" if emphasised else "muted",
                            anchor="end", tabular=True))

    shown, last_x = [], None
    for year in years:
        x = px(year)
        if last_x is None or x - last_x >= 44 or year == years[-1]:
            if shown and year == years[-1] and x - last_x < 44:
                shown.pop()
            shown.append(year)
            last_x = x
    for year in shown:
        body.append(text_el(px(year), plot_top + plot_height + 18, str(year), size=10.5,
                            fill="muted", anchor="middle", tabular=True))

    # Drawn first so the entities sit above it: a baseline is read through, not at.
    if reference:
        ref_points = [(px(y), py(v)) for y, v in reference["points"] if v is not None]
        ref_path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
                            for i, (x, y) in enumerate(ref_points))
        body.append(f'<path d="{ref_path}" fill="none" stroke="{token("muted")}" '
                    f'stroke-width="2" stroke-dasharray="6 4" stroke-linecap="round"/>')
        for (x, y), (year, v) in zip(
                ref_points, [p for p in reference["points"] if p[1] is not None]):
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="transparent">'
                        f'<title>{esc(reference["label"])} {year}: '
                        f'{esc(formatter(v))}</title></circle>')

    slots = ["s1", "s2", "s3", "s4"]
    ends = []
    for index, entry in enumerate(series):
        colour = slots[index]
        points = [(px(y), py(v)) for y, v in entry["points"] if v is not None]
        path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
                        for i, (x, y) in enumerate(points))
        # A series measured at two endpoints five years apart is drawn dashed,
        # and its observations are marked. Solid, it is indistinguishable from a
        # line measured every year, and the reader reads a path through years
        # nobody reported. The dash says the segment is interpolation.
        dash = (' stroke-dasharray="5 4"' if entry.get("interpolated") else "")
        body.append(f'<path d="{path}" fill="none" stroke="{token(colour)}" stroke-width="2" '
                    f'stroke-linejoin="round" stroke-linecap="round"{dash}/>')
        for (x, y), (year, v) in zip(points, [p for p in entry["points"] if p[1] is not None]):
            if entry.get("interpolated"):
                body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" '
                            f'fill="{token(colour)}"/>')
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="transparent">'
                        f'<title>{esc(entry["label"])} {year}: {esc(formatter(v))}</title></circle>')
        ends.append((points[-1][1], points[-1][0], colour, entry["label"]))

    # Nudge converging end labels apart rather than letting them overlap. One
    # forward pass over labels already sorted by height: each is pushed to at
    # least a line below the one above it, and never above where it belongs.
    #
    # Deliberately not a loop that re-tests the gap. `(a + 15) - a` is not
    # exactly 15 in binary floating point, so a re-test can find the gap still
    # short, set the same value again, and spin forever. It hung the whole
    # request the first time four cities converged.
    LABEL_GAP = 15
    ends.sort()
    placed = []
    for y, x, colour, name in ends:
        if placed:
            y = max(y, placed[-1] + LABEL_GAP)
        placed.append(y)
        body.append(text_el(x + 12, y + 4, truncate(name, 18), size=11, fill="ink-2"))

    for y, x, colour, name in ends:
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{token("surface")}"/>')
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{token(colour)}"/>')

    legend_y = plot_top + plot_height + tick_band + 14
    x, rows_used = left, 1
    entries = [(slots[i], e["label"], False) for i, e in enumerate(series)]
    if reference:
        entries.append(("muted", reference["label"], True))

    for colour, raw_label, dashed in entries:
        name = truncate(raw_label, 22)
        span = 26 + len(name) * 6.2
        if x > left and x + span > width - 20:      # wrap rather than run off the edge
            x, rows_used = left, rows_used + 1
            legend_y += 18
        if dashed:
            body.append(f'<line x1="{x}" y1="{legend_y - 3}" x2="{x + 12}" y2="{legend_y - 3}" '
                        f'stroke="{token(colour)}" stroke-width="2" stroke-dasharray="4 3"/>')
        else:
            body.append(f'<rect x="{x}" y="{legend_y - 8}" width="10" height="10" rx="2" '
                        f'fill="{token(colour)}"/>')
        body.append(text_el(x + 16, legend_y + 1, name, size=11, fill="ink-2"))
        x += span
    if note:
        body.append(text_el(left, legend_y + 20, note, size=11, fill="muted"))
        rows_used += 1

    return frame(width, max(height, legend_y + 30), "".join(body),
                 f"{title}. {subtitle or ''}".strip())


# The reported total is not always operating plus capital. It reaches within a
# point of it for 1,251 of 1,529 governments and falls short for the rest —
# Oregon's own filing accounts for 79.8% of its total this way. Folding the
# remainder into operating would be the convenient thing and would misstate the
# denominator every share on the page is drawn against, so it is drawn.
SPLIT_TOKENS = ("s1", "s3", "dim")


def split_bar(title, subtitle, parts, headline=None, headline_label=None,
              headline_share=None, peer=None, peer_label=None,
              formatter=fmt.money, width=680, note=None):
    """One quantity divided into named pieces, with the piece that matters said.

    A pie chart of two numbers is a worse bar chart, and a bar chart of two
    numbers loses the thing a reader came for, which is the ratio. This draws
    the whole as one bar so the pieces are read against each other and against
    the total at once, and states the share of interest as a figure above it,
    because a reader who wants the number should never have to estimate it off
    a length.

    `peer` draws the same share for the government's peer group as a second,
    recessive track underneath. It is the only comparison this form makes, and
    it is deliberately not a tick inside the bar: the pieces start at different
    x positions, so a mark placed inside would be read against whichever
    segment it happened to land in.
    """
    parts = [p for p in parts if (p.get("value") or 0) > 0]
    if not parts:
        return None
    total = sum(p["value"] for p in parts)
    if total <= 0:
        return None

    top, offset = header(title, subtitle, width)
    body = [top]
    y = offset

    if headline is not None:
        body.append(text_el(0, y + 30, headline, size=30, fill="ink",
                            weight="600", tabular=True))
        if headline_label:
            body.append(text_el(0, y + 50, headline_label, size=11.5, fill="muted"))
        y += 62

    height = 30
    x = 0.0
    for index, part in enumerate(parts):
        share = part["value"] / total
        segment = share * width
        colour = part.get("token") or SPLIT_TOKENS[min(index, len(SPLIT_TOKENS) - 1)]
        # Square joins between segments, rounded only at the two outer ends, so
        # the bar reads as one quantity cut up rather than as separate bars.
        radius = 4 if index == len(parts) - 1 else 0
        body.append(f'{bar_path(x, y, segment, height, radius=radius)} '
                    f'fill="{token(colour)}"><title>{esc(part["label"])}: '
                    f'{esc(formatter(part["value"]))}, {share * 100:.1f}%'
                    f'</title></path>')
        x += segment
    y += height + 10

    # The legend carries the money, because the segment carries only the share
    # and the two are the reason this is one figure rather than two.
    for index, part in enumerate(parts):
        share = part["value"] / total
        colour = part.get("token") or SPLIT_TOKENS[min(index, len(SPLIT_TOKENS) - 1)]
        body.append(f'<rect x="0" y="{y + 2}" width="10" height="10" rx="2" '
                    f'fill="{token(colour)}"/>')
        body.append(text_el(16, y + 11, part["label"], size=11.5, fill="ink-2"))
        # Two columns, not one string. "$6M   63%" set as a single right-anchored
        # run collapsed its spaces in SVG and read as one number.
        body.append(text_el(width - 56, y + 11, formatter(part["value"]),
                            size=11.5, fill="ink-2", anchor="end", tabular=True))
        body.append(text_el(width, y + 11, fmt.percent(share * 100),
                            size=11.5, fill="ink", anchor="end", tabular=True))
        y += 19
        if part.get("detail"):
            body.append(text_el(16, y + 9, part["detail"], size=11, fill="muted"))
            y += 16

    if peer is not None and headline_share is not None:
        # Two tracks from a common origin, not a tick inside the bar above. The
        # segments up there start at different x positions, so a mark dropped
        # into them is read against whichever one it lands in; these two share a
        # zero and a scale and are the only comparison this form makes. The
        # entity's track wears the capital colour so the eye carries across.
        y += 16
        track = 9
        body.append(text_el(0, y, "Capital share against the peer group",
                            size=11, fill="ink-2", weight="600"))
        y += 10
        for label, value, colour in (
                ("this government", headline_share, parts[1]["token"]
                 if len(parts) > 1 and parts[1].get("token") else SPLIT_TOKENS[1]),
                (peer_label or "peer median", peer, "dim")):
            body.append(f'<rect x="0" y="{y + 4}" width="{width}" height="{track}" '
                        f'rx="{track / 2}" fill="{token("track")}"/>')
            marked = max(0.0, min(1.0, value / 100.0)) * width
            body.append(f'{bar_path(0, y + 4, marked, track, radius=track / 2)} '
                        f'fill="{token(colour)}"><title>{esc(label)}: '
                        f'{value:.1f}%</title></path>')
            y += track + 15
            body.append(text_el(0, y, label, size=11, fill="muted"))
            body.append(text_el(width, y, fmt.percent(value), size=11, fill="muted",
                                anchor="end", tabular=True))
            y += 8

    if note:
        y += 10
        for line in wrap(note, 96):
            body.append(text_el(0, y + 8, line, size=11, fill="muted"))
            y += 14

    return frame(width, y + 10, "".join(body),
                 f"{title}. {subtitle or ''} "
                 + ", ".join(f"{p['label']} {formatter(p['value'])}" for p in parts))

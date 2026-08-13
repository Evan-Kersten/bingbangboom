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

import format as fmt

# ---------------------------------------------------------------- palette

TOKENS = {
    "surface": ("#fcfcfb", "#1a1a19"),
    "ink": ("#0b0b0b", "#ffffff"),
    "ink-2": ("#52514e", "#c3c2b7"),
    "muted": ("#898781", "#898781"),
    "grid": ("#e1e0d9", "#2c2c2a"),
    "axis": ("#c3c2b7", "#383835"),
    "s1": ("#2a78d6", "#3987e5"),
    "s2": ("#eb6834", "#d95926"),
    "dim": ("#c3c2b7", "#52514e"),        # de-emphasis for the emphasis form
    "good": ("#0ca30c", "#0ca30c"),
    "warning": ("#fab219", "#fab219"),
    "serious": ("#ec835a", "#ec835a"),
    "critical": ("#d03b3b", "#d03b3b"),
    # Meter track. The palette's default is a lighter step of the fill's own ramp,
    # which assumes a ramp-hued fill. These meters carry a status fill, so a blue
    # track beside a green fill mixes two systems, and at a score of zero the
    # track is all that shows and reads as a bar. Neutral is the honest track.
    "track": ("#e6e5df", "#2c2c2a"),
    "nodata": ("#e1e0d9", "#2c2c2a"),
    "bin-1": ("#86b6ef", "#184f95"),
    "bin-2": ("#5598e7", "#256abf"),
    "bin-3": ("#2a78d6", "#3987e5"),
    "bin-4": ("#1c5cab", "#6da7ec"),
    "bin-5": ("#104281", "#9ec5f4"),
}

# Single quotes inside the stack: this string is emitted into a double-quoted
# SVG attribute, and a nested double quote closes the attribute early and makes
# the whole document unparseable.
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"


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


def horizontal_bars(title, subtitle, rows, formatter=fmt.money, width=680,
                    emphasis=None, note=None):
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
    largest = max(abs(r["value"]) for r in rows) or 1

    body = [top]
    y = offset + 8
    for row in rows:
        length = max(0.0, abs(row["value"]) / largest * plot_width)
        colour = "s1" if (emphasis is None or row["label"] == emphasis) else "dim"
        body.append(text_el(plot_left - 10, y + BAR_THICKNESS / 2 + 4,
                            truncate(row["label"]), anchor="end"))
        body.append(f'{bar_path(plot_left, y, length, BAR_THICKNESS)} '
                    f'fill="{token(colour)}"><title>{esc(row["label"])}: '
                    f'{esc(formatter(row["value"]))}</title></path>')
        # The value rides outside the bar end, so it is never clipped by a
        # short bar and never needs a fit measurement.
        body.append(text_el(plot_left + length + 8, y + BAR_THICKNESS / 2 + 4,
                            formatter(row["value"]), size=11.5, tabular=True))
        y += slot

    if note:
        y += 6
        body.append(text_el(0, y + 8, note, size=11, fill="muted"))
        y += 16

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
    left, right = 64, 92
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

    for index, entry in enumerate(series):
        colour = "s1" if index == 0 else "s2"
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
        body.append(text_el(end_x + 10, end_y + 4, entry["label"], size=11, fill="ink-2"))

    # A legend is always present for two or more series, on its own row below
    # the tick band.
    legend_y = plot_top + plot_height + tick_band + 14
    x = left
    for index, entry in enumerate(series):
        colour = "s1" if index == 0 else "s2"
        body.append(f'<rect x="{x}" y="{legend_y - 8}" width="10" height="10" rx="2" '
                    f'fill="{token(colour)}"/>')
        body.append(text_el(x + 16, legend_y + 1, entry["label"], size=11, fill="ink-2"))
        x += 22 + len(entry["label"]) * 6.4

    return frame(width, height, "".join(body), f"{title}. {subtitle or ''}".strip())


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

"""Number and style rules from section 4.

Formatting lives here rather than in the prompt so that a figure reads the same
whether it lands in a sentence, an axis tick or a table cell. A model asked to
round consistently will drift; a formatter will not.
"""

import re

# §4 bans these outright. "leverage" and "target" are listed as verbs but are
# checked as whole words, since neither has a defensible use in these answers.
BANNED_WORDS = [
    "significantly", "notably", "truly", "genuinely", "actually", "robust",
    "leverage", "leveraging", "leveraged", "opportunity", "opportunities",
    "target", "targets", "targeting", "targeted", "pursue", "pursuing", "pursued",
]

DASHES = {"—": "em dash", "–": "en dash"}


def money(value):
    """Format a dollar figure to the rounding rules in §4.

    $2.0B, $700M, $410K. Below $1M the figure is given in thousands to the
    nearest ten; below $100K, to the nearest thousand, since rounding $5,000 to
    the nearest ten thousand would destroy it.
    """
    if value is None:
        return None
    negative = value < 0
    value = abs(float(value))

    if value >= 1e9:
        text = f"${value / 1e9:.1f}B"
    elif value >= 1e6:
        text = f"${value / 1e6:.0f}M"
    elif value >= 1e5:
        text = f"${round(value / 1e4) * 10:.0f}K"
    elif value >= 1000:
        text = f"${value / 1e3:.0f}K"
    else:
        text = f"${value:.0f}"
    return "-" + text if negative else text


def percent(value):
    """Whole numbers at or above 10%, one decimal below, where the detail matters."""
    if value is None:
        return None
    return f"{value:.0f}%" if abs(value) >= 10 else f"{value:.1f}%"


def ratio(value):
    """§3 prefers ratios to comparative adjectives: '2.7 times the peer median'."""
    if value is None:
        return None
    return f"{value:.1f} times"


def count(value):
    if value is None:
        return None
    return f"{value:,}"


# §15.3: an uncertainty in a footnote is not read. These marks ride the figure.
MARKS = {
    "match_confidence": ("\u2020", "rests on a match below the high-confidence threshold"),
    "partial_breakdown": ("\u2021", "a share of a partial breakdown"),
    "basis_dependent": ("\u00a7", "a different basis would reorder this"),
}


def mark(value_text, kinds):
    """Append uncertainty marks to a formatted figure.

    Order is fixed so the same qualification always reads the same way, and an
    unmarked figure means the data supports it without qualification. Marking
    everything would carry no information, so callers pass only what applies.
    """
    if not value_text or not kinds:
        return value_text
    glyphs = "".join(MARKS[k][0] for k in MARKS if k in kinds)
    return f"{value_text}{glyphs}" if glyphs else value_text


def mark_legend(kinds):
    """The one short line that sits beneath a marked block."""
    return [f"{MARKS[k][0]} {MARKS[k][1]}" for k in MARKS if k in kinds]


def check_style(text):
    """Return §4 style violations in generated prose.

    Used by the eval harness to assert on answers mechanically, rather than
    hoping the model remembered the banned list.
    """
    violations = []
    for character, name in DASHES.items():
        if character in text:
            violations.append({"kind": "dash", "detail": name})
    lowered = text.lower()
    for word in BANNED_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            violations.append({"kind": "banned_word", "detail": word})
    # §4 wants readable figures, so an unrounded long number is a violation too.
    for match in re.finditer(r"\$\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?", text):
        violations.append({"kind": "unrounded_figure", "detail": match.group(0)})
    return violations

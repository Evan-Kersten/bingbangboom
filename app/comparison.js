/* Comparisons assembled in the browser, for the pre-rendered build.
 *
 * The served interface calls the Python tool layer for these four charts. A
 * pre-rendered build has no server, and every set of four governments across
 * twelve service areas is far more pages than it can carry, so the figures ship
 * as data and the comparison is assembled here.
 *
 * This file duplicates two things from the Python and nothing else: the
 * per-entity arithmetic, which is a total divided by a population, and the chart
 * geometry. Every peer distribution and every baseline series arrives already
 * computed by the same code the server runs, and the palette and layout
 * constants arrive with them. app/test_parity.py drives both implementations
 * over the same inputs and fails on any disagreement, which is what keeps the
 * two from drifting.
 *
 * The caveats are not decoration here either. A per-resident figure whose
 * population is one estimate, a snapshot that is not a trend, a set that mixes
 * government types: all of it is carried onto the answer, because the rules are
 * what make a figure safe to read and they cannot be left on the server.
 */

(function (global) {
  'use strict';

  let DATA = null;

  function load(payload) {
    DATA = payload;
    return DATA;
  }

  // ------------------------------------------------------------ formatting
  // §4's rounding, matching agent/format.py. A figure has to read the same in a
  // sentence, an axis tick and a table cell, so these are ports rather than
  // approximations.

  function money(value) {
    if (value === null || value === undefined) return null;
    const negative = value < 0;
    const size = Math.abs(Number(value));
    let text;
    if (size >= 1e9) text = '$' + (size / 1e9).toFixed(1) + 'B';
    else if (size >= 1e6) text = '$' + Math.round(size / 1e6) + 'M';
    else if (size >= 1e5) text = '$' + Math.round(size / 1e4) * 10 + 'K';
    else if (size >= 1000) text = '$' + Math.round(size / 1e3) + 'K';
    else text = '$' + Math.round(size);
    return negative ? '-' + text : text;
  }

  // A rate is not a budget line: rounding $1,283 per resident to "$1K" destroys
  // the figure rather than tidying it.
  function rate(value) {
    if (value === null || value === undefined) return null;
    return '$' + Math.round(value).toLocaleString('en-US');
  }

  function percent(value) {
    if (value === null || value === undefined) return null;
    return Math.abs(value) >= 10 ? value.toFixed(0) + '%' : value.toFixed(1) + '%';
  }

  function count(value) {
    if (value === null || value === undefined) return null;
    return Math.round(value).toLocaleString('en-US');
  }

  function plural(number, noun, many) {
    const word = Math.abs(number) === 1 ? noun : (many || noun + 's');
    return count(number) + ' ' + word;
  }

  function index(value) {
    if (value === null || value === undefined) return null;
    return Math.round(value).toLocaleString('en-US');
  }

  // ------------------------------------------------------------ primitives

  function esc(text) {
    return String(text === null || text === undefined ? '' : text)
      .replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#x27;',
      }[c]));
  }

  function token(name) {
    const pair = DATA.tokens[name];
    return 'var(--pf-' + name + ', ' + (pair ? pair[0] : '#000') + ')';
  }

  function truncate(text, limit) {
    text = String(text);
    return text.length <= limit ? text : text.slice(0, limit - 1).replace(/\s+$/, '') + '…';
  }

  function textEl(x, y, content, options) {
    const o = options || {};
    const size = o.size === undefined ? 12 : o.size;
    const numeric = o.tabular ? ' font-variant-numeric="tabular-nums"' : '';
    return '<text x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" font-family="'
      + DATA.layout.font + '" font-size="' + size + '" font-weight="'
      + (o.weight || '400') + '" fill="' + token(o.fill || 'ink-2')
      + '" text-anchor="' + (o.anchor || 'start') + '"' + numeric + '>'
      + esc(content) + '</text>';
  }

  function barPath(x, y, width, height, radius) {
    radius = Math.max(0, Math.min(radius === undefined ? 4 : radius, width, height / 2));
    if (width <= 0.5) {
      return '<path d="M' + x.toFixed(1) + ',' + y.toFixed(1) + ' h0.5 v'
        + height.toFixed(1) + ' h-0.5 Z"';
    }
    const right = x + width;
    return '<path d="M' + x.toFixed(1) + ',' + y.toFixed(1)
      + ' H' + (right - radius).toFixed(1)
      + ' A' + radius.toFixed(1) + ',' + radius.toFixed(1) + ' 0 0 1 '
      + right.toFixed(1) + ',' + (y + radius).toFixed(1)
      + ' V' + (y + height - radius).toFixed(1)
      + ' A' + radius.toFixed(1) + ',' + radius.toFixed(1) + ' 0 0 1 '
      + (right - radius).toFixed(1) + ',' + (y + height).toFixed(1)
      + ' H' + x.toFixed(1) + ' Z"';
  }

  function frame(width, height, body, label) {
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + width + ' '
      + height + '" width="100%" style="max-width:' + width
      + 'px;height:auto;display:block" role="img" aria-label="' + esc(label)
      + '" class="pf-viz">' + body + '</svg>';
  }

  function header(title, subtitle) {
    let parts = textEl(0, 16, title, { size: 14, fill: 'ink', weight: '600' });
    if (subtitle) parts += textEl(0, 34, subtitle, { size: 11.5, fill: 'muted' });
    return { markup: parts, offset: subtitle ? 46 : 28 };
  }

  function wrap(text, columns) {
    const words = String(text).split(/\s+/);
    const lines = [];
    let line = '';
    words.forEach((word) => {
      if (!line) line = word;
      else if ((line + ' ' + word).length <= columns) line += ' ' + word;
      else { lines.push(line); line = word; }
    });
    if (line) lines.push(line);
    return lines;
  }

  // An axis labelled $905, $1,810, $2,715 is arithmetically correct and reads as
  // noise. The numbers are there to be compared against.
  function niceTicks(low, high, wanted) {
    const span = (high - low) || Math.abs(high) || 1;
    const raw = span / Math.max(wanted, 1);
    const magnitude = raw > 0 ? Math.pow(10, Math.floor(Math.log10(raw))) : 1;
    let step = magnitude;
    const multiples = [1, 2, 2.5, 5, 10];
    for (let i = 0; i < multiples.length; i += 1) {
      step = multiples[i] * magnitude;
      if (raw <= step) break;
    }
    const start = Math.floor(low / step) * step;
    const ticks = [];
    for (let value = start; value <= high + step * 0.001; value += step) {
      ticks.push(Math.round(value * 1e10) / 1e10);
    }
    return ticks.length ? ticks : [low, high];
  }

  function refusal(title, reason) {
    const width = DATA.layout.width;
    const lines = wrap(reason, 84);
    let y = 44;
    let body = textEl(0, 16, title, { size: 14, fill: 'ink', weight: '600' });
    lines.forEach((line) => {
      body += textEl(14, y, line, { size: 12, fill: 'ink-2' });
      y += 17;
    });
    body += '<rect x="0" y="30" width="3" height="' + (y - 40)
      + '" rx="1.5" fill="' + token('warning') + '"/>';
    return frame(width, y, body, title + '. ' + reason);
  }

  /* A fact about the drawing that only exists once the drawing is done, chiefly
     a government the reader picked that the chart could not draw. It belongs on
     the picture, because the picture is what gets read and screenshot, and a
     rule in a panel underneath does not travel with it. Mirrors
     viz.append_note so both engines produce the same chart. */
  function appendNote(svg, note, columns) {
    const match = /viewBox="0 0 ([\d.]+) ([\d.]+)"/.exec(svg);
    if (!match) return svg;
    const height = parseFloat(match[2]);
    const lines = wrap(note, columns || 86);
    const grown = height + 6 + lines.length * 15;
    let extra = '';
    let y = height + 14;
    lines.forEach((line) => {
      extra += textEl(0, y, line, { size: 11, fill: 'muted' });
      y += 15;
    });
    return svg
      .replace('viewBox="0 0 ' + match[1] + ' ' + match[2] + '"',
               'viewBox="0 0 ' + match[1] + ' ' + trimNumber(grown) + '"')
      .replace('</svg>', extra + '</svg>');
  }

  /* Python's %g drops a trailing ".0"; JS toString does not add one, but a
     fractional height has to render the same in both or the frames differ. */
  function trimNumber(value) {
    return String(Math.round(value * 1e6) / 1e6);
  }

  // ----------------------------------------------------------------- forms

  /* Change against a zero line. Running a change through the ordinary bar form
     draws a shrinking government as a hairline at the left edge, which loses the
     size of the fall. Growth and decline share one hue: neither direction is good
     news on its own, and this data cannot say which it was (§4). Mirrors
     viz.diverging_bars. */
  function divergingBars(title, subtitle, rows, options) {
    const o = options || {};
    const formatter = o.formatter || percent;
    const width = DATA.layout.width;
    const thickness = DATA.layout.barThickness;
    const slot = thickness + DATA.layout.barGap + 8;
    const plotLeft = DATA.layout.labelColumn;
    const plotWidth = width - DATA.layout.labelColumn - DATA.layout.valueColumn;

    rows = rows.filter((r) => r.value !== null && r.value !== undefined);
    if (!rows.length) return null;

    const peers = rows.filter((r) => r.peerMedian !== null && r.peerMedian !== undefined)
      .map((r) => r.peerMedian);
    const values = rows.map((r) => r.value).concat(peers).concat([0]);
    const low = Math.min.apply(null, values);
    const high = Math.max.apply(null, values);
    const span = (high - low) || 1;
    const px = (v) => plotLeft + ((v - low) / span) * plotWidth;
    const zeroX = px(0);

    const head = header(title, subtitle);
    let bodyParts = head.markup;
    let y = head.offset + 8;
    rows.forEach((row) => {
      const x = px(row.value);
      const left = Math.min(zeroX, x);
      const right = Math.max(zeroX, x);
      bodyParts += textEl(plotLeft - 10, y + thickness / 2 + 4,
        truncate(row.label, DATA.layout.barLabelChars), { anchor: 'end' });
      bodyParts += barPath(left, y, Math.max(right - left, 0.5), thickness, 3)
        + ' fill="' + token('s1') + '"><title>' + esc(row.label) + ': '
        + esc(formatter(row.value)) + '</title></path>';
      if (row.peerMedian !== null && row.peerMedian !== undefined) {
        const tick = px(row.peerMedian);
        bodyParts += '<line x1="' + tick.toFixed(1) + '" y1="' + (y - 2) + '" x2="'
          + tick.toFixed(1) + '" y2="' + (y + thickness + 2) + '" stroke="'
          + token('ink-2') + '" stroke-width="1.5"><title>peer median '
          + esc(formatter(row.peerMedian)) + '</title></line>';
      }
      bodyParts += textEl(width - DATA.layout.valueColumn + 8, y + thickness / 2 + 4,
        formatter(row.value), { size: 11.5, tabular: true });
      y += slot;
    });

    bodyParts += '<line x1="' + zeroX.toFixed(1) + '" y1="' + (head.offset + 2)
      + '" x2="' + zeroX.toFixed(1) + '" y2="' + (y - DATA.layout.barGap - 6)
      + '" stroke="' + token('axis') + '" stroke-width="1.5"/>';
    bodyParts += textEl(zeroX, y + 8, 'no change',
      { size: 10.5, fill: 'muted', anchor: 'middle' });
    y += 20;

    if (peers.length) {
      bodyParts += textEl(plotLeft, y + 10,
        '| tick marks the median change for that government type',
        { size: 10.5, fill: 'muted' });
      y += 16;
    }
    if (o.note) {
      wrap(o.note, 92).forEach((line) => {
        bodyParts += textEl(0, y + 12, line, { size: 11, fill: 'muted' });
        y += 14;
      });
      y += 4;
    }
    return frame(width, y + 12, bodyParts, title + '. ' + (subtitle || ''));
  }

  function horizontalBars(title, subtitle, rows, options) {
    const o = options || {};
    const formatter = o.formatter || money;
    const width = DATA.layout.width;
    const thickness = DATA.layout.barThickness;
    const slot = thickness + DATA.layout.barGap + 8;
    const plotLeft = DATA.layout.labelColumn;
    const plotWidth = width - DATA.layout.labelColumn - DATA.layout.valueColumn;

    rows = rows.filter((r) => r.value !== null && r.value !== undefined);
    if (!rows.length) return null;

    // The ticks share the bars' scale, so they set it too. Scaling to the bars
    // alone clamps a median above the longest bar to the plot edge, where it
    // reads as equal to the largest value rather than beyond it.
    const drawnPeers = rows
      .filter((r) => r.peerMedian !== null && r.peerMedian !== undefined && !r.peerDegenerate)
      .map((r) => Math.abs(r.peerMedian));
    const largest = Math.max.apply(null,
      rows.map((r) => Math.abs(r.value)).concat(drawnPeers)) || 1;

    const top = header(title, subtitle);
    let body = top.markup;
    let y = top.offset + 8;

    rows.forEach((row) => {
      const length = Math.max(0, Math.abs(row.value) / largest * plotWidth);
      body += textEl(plotLeft - 10, y + thickness / 2 + 4, truncate(row.label, DATA.layout.barLabelChars),
        { anchor: 'end' });
      body += barPath(plotLeft, y, length, thickness) + ' fill="' + token('s1')
        + '"><title>' + esc(row.label) + ': ' + esc(formatter(row.value))
        + '</title></path>';

      if (row.peerMedian !== null && row.peerMedian !== undefined && !row.peerDegenerate) {
        const tick = plotLeft + Math.min(Math.abs(row.peerMedian) / largest * plotWidth, plotWidth);
        body += '<line x1="' + tick.toFixed(1) + '" y1="' + (y - 2) + '" x2="'
          + tick.toFixed(1) + '" y2="' + (y + thickness + 2) + '" stroke="'
          + token('ink-2') + '" stroke-width="1.5"><title>peer median '
          + esc(formatter(row.peerMedian)) + '</title></line>';
      }

      body += textEl(plotLeft + length + 8, y + thickness / 2 + 4, formatter(row.value),
        { size: 11.5, tabular: true });
      y += slot;
    });

    if (rows.some((r) => r.peerMedian !== null && r.peerMedian !== undefined && !r.peerDegenerate)) {
      y += 4;
      body += '<line x1="0" y1="' + (y + 4) + '" x2="0" y2="' + (y + 14)
        + '" stroke="' + token('ink-2') + '" stroke-width="1.5"/>';
      body += textEl(8, y + 13, 'peer median for this government type',
        { size: 11, fill: 'muted' });
      y += 18;
    }

    if (o.note) {
      y += 6;
      wrap(o.note, 96).forEach((line) => {
        body += textEl(0, y + 8, line, { size: 11, fill: 'muted' });
        y += 14;
      });
      y += 2;
    }

    return frame(width, y + 8, body, (title + '. ' + (subtitle || '')).trim());
  }

  function multiSeries(title, subtitle, series, options) {
    const o = options || {};
    const formatter = o.formatter || money;
    const width = DATA.layout.width;
    const height = DATA.layout.seriesHeight;

    series = series.filter(
      (s) => s.points.filter((p) => p[1] !== null && p[1] !== undefined).length > 1);
    if (!series.length) return null;
    series = series.slice(0, DATA.layout.maxSeries);

    let reference = o.reference || null;
    if (reference
        && reference.points.filter((p) => p[1] !== null && p[1] !== undefined).length < 2) {
      reference = null;
    }

    const top = header(title, subtitle);
    const left = 68;
    const right = 150;
    const tickBand = 24;
    const legendBand = 30 + (o.note ? 18 : 0);
    const plotTop = top.offset + 26;
    const plotHeight = height - plotTop - tickBand - legendBand;
    const plotWidth = width - left - right;

    const lines = reference ? series.concat([reference]) : series;
    const years = Array.from(new Set([].concat.apply([],
      lines.map((s) => s.points.filter((p) => p[1] !== null).map((p) => p[0]))))).sort();
    const values = [].concat.apply([],
      lines.map((s) => s.points.filter((p) => p[1] !== null).map((p) => p[1])));

    // A zero baseline is the default. An index is read against its own base of
    // 100, and holding the floor at zero pins every line into the top of the
    // plot and hides the divergence the chart exists to show.
    let floor;
    let ceiling;
    if (o.baseline !== null && o.baseline !== undefined) {
      floor = Math.min.apply(null, values.concat([o.baseline])) * 0.97;
      ceiling = Math.max.apply(null, values.concat([o.baseline])) * 1.03;
    } else {
      floor = 0;
      ceiling = Math.max.apply(null, values) || 1;
    }
    const domain = (ceiling - floor) || 1;
    const yearSpan = (years[years.length - 1] - years[0]) || 1;
    const px = (year) => left + (year - years[0]) / yearSpan * plotWidth;
    const py = (v) => plotTop + plotHeight - ((v - floor) / domain) * plotHeight;

    let body = top.markup;
    niceTicks(floor, ceiling, 4).forEach((value) => {
      if (value < floor) return;
      const gy = py(value);
      const emphasised = o.baseline !== null && o.baseline !== undefined
        && Math.abs(value - o.baseline) < 1e-9;
      body += '<line x1="' + left + '" y1="' + gy.toFixed(1) + '" x2="'
        + (left + plotWidth) + '" y2="' + gy.toFixed(1) + '" stroke="'
        + token(emphasised ? 'axis' : 'grid') + '" stroke-width="1"/>';
      body += textEl(left - 10, gy + 4, formatter(value),
        { size: 10.5, fill: emphasised ? 'ink-2' : 'muted', anchor: 'end', tabular: true });
    });

    const shown = [];
    let lastX = null;
    years.forEach((year) => {
      const x = px(year);
      if (lastX === null || x - lastX >= 44 || year === years[years.length - 1]) {
        if (shown.length && year === years[years.length - 1] && x - lastX < 44) shown.pop();
        shown.push(year);
        lastX = x;
      }
    });
    shown.forEach((year) => {
      body += textEl(px(year), plotTop + plotHeight + 18, String(year),
        { size: 10.5, fill: 'muted', anchor: 'middle', tabular: true });
    });

    // Drawn first so the entities sit above it: a baseline is read through.
    if (reference) {
      const points = reference.points.filter((p) => p[1] !== null)
        .map((p) => [px(p[0]), py(p[1])]);
      const path = points.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1))
        .join(' ');
      body += '<path d="' + path + '" fill="none" stroke="' + token('muted')
        + '" stroke-width="2" stroke-dasharray="6 4" stroke-linecap="round"/>';
      reference.points.filter((p) => p[1] !== null).forEach((point, i) => {
        body += '<circle cx="' + points[i][0].toFixed(1) + '" cy="' + points[i][1].toFixed(1)
          + '" r="9" fill="transparent"><title>' + esc(reference.label) + ' ' + point[0]
          + ': ' + esc(formatter(point[1])) + '</title></circle>';
      });
    }

    const slots = ['s1', 's2', 's3', 's4'];
    const ends = [];
    series.forEach((entry, position) => {
      const colour = slots[position];
      const drawn = entry.points.filter((p) => p[1] !== null);
      const points = drawn.map((p) => [px(p[0]), py(p[1])]);
      const path = points.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1))
        .join(' ');
      body += '<path d="' + path + '" fill="none" stroke="' + token(colour)
        + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
      drawn.forEach((point, i) => {
        body += '<circle cx="' + points[i][0].toFixed(1) + '" cy="' + points[i][1].toFixed(1)
          + '" r="9" fill="transparent"><title>' + esc(entry.label) + ' ' + point[0]
          + ': ' + esc(formatter(point[1])) + '</title></circle>';
      });
      const last = points[points.length - 1];
      ends.push([last[1], last[0], colour, entry.label]);
    });

    // One forward pass over labels already sorted by height. Deliberately not a
    // loop that re-tests the gap: (a + 15) - a is not exactly 15 in binary
    // floating point, so a re-test can spin forever.
    ends.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const placed = [];
    ends.forEach((end) => {
      let y = end[0];
      if (placed.length) y = Math.max(y, placed[placed.length - 1] + DATA.layout.labelGap);
      placed.push(y);
      body += textEl(end[1] + 12, y + 4, truncate(end[3], 18), { size: 11, fill: 'ink-2' });
    });
    ends.forEach((end) => {
      body += '<circle cx="' + end[1].toFixed(1) + '" cy="' + end[0].toFixed(1)
        + '" r="6" fill="' + token('surface') + '"/>';
      body += '<circle cx="' + end[1].toFixed(1) + '" cy="' + end[0].toFixed(1)
        + '" r="4" fill="' + token(end[2]) + '"/>';
    });

    let legendY = plotTop + plotHeight + tickBand + 14;
    let x = left;
    const entries = series.map((s, i) => [slots[i], s.label, false]);
    if (reference) entries.push(['muted', reference.label, true]);
    entries.forEach((entry) => {
      const name = truncate(entry[1], 22);
      const span = 26 + name.length * 6.2;
      if (x > left && x + span > width - 20) { x = left; legendY += 18; }
      if (entry[2]) {
        body += '<line x1="' + x + '" y1="' + (legendY - 3) + '" x2="' + (x + 12)
          + '" y2="' + (legendY - 3) + '" stroke="' + token(entry[0])
          + '" stroke-width="2" stroke-dasharray="4 3"/>';
      } else {
        body += '<rect x="' + x + '" y="' + (legendY - 8) + '" width="10" height="10" rx="2" fill="'
          + token(entry[0]) + '"/>';
      }
      body += textEl(x + 16, legendY + 1, name, { size: 11, fill: 'ink-2' });
      x += span;
    });
    if (o.note) body += textEl(left, legendY + 20, o.note, { size: 11, fill: 'muted' });

    return frame(width, Math.max(height, legendY + 30), body,
      (title + '. ' + (subtitle || '')).trim());
  }

  // ------------------------------------------------------------- analytics

  function entity(pid6) {
    const row = DATA.entities[pid6];
    return row ? { pid6, name: row[0], govType: row[1], population: row[2],
                   chartable: Boolean(row[3]) } : null;
  }

  /* Three states, not two, and collapsing the middle one is what made a picked
     government vanish from its own comparison: an id this build has never heard
     of, an entity that is in the data but reports nothing any of these charts
     can draw, and one that can be drawn. §9 and §14 want the first two told
     apart — "not in the data" and "nothing to compare here" are different
     sentences, and only one of them is true of a drainage district. */
  function partition(pid6List) {
    const found = [];
    const unchartable = [];
    const missing = [];
    pid6List.forEach((pid6) => {
      const e = entity(pid6);
      if (!e) missing.push(pid6);
      else if (!e.chartable) unchartable.push(e);
      else found.push(e);
    });
    return { found, unchartable, missing };
  }

  function unchartableCaveat(unchartable) {
    return { code: 'nothing_to_compare', rule: '§9',
      guidance: unchartable.length + ' of the governments picked report neither a '
        + 'spending breakdown nor a year of totals, so there is nothing to put on '
        + 'this axis for them: ' + nameList(unchartable.map((e) => e.name))
        + '. They are in the data and were left out of the drawing, which is not the '
        + 'same as spending nothing. Name them.' };
  }

  // A school district's population is its enrollment, so dividing by it gives
  // spending per student. That cannot share an axis with spending per resident.
  function unitFor(govType) {
    const pair = DATA.denominators[govType];
    return pair ? pair[1] : 'residents';
  }

  function mixedDenominators(found) {
    const units = Array.from(new Set(found.map((e) => unitFor(e.govType)).filter(Boolean)));
    return units.length > 1 ? units.sort() : null;
  }

  // The singular noun a per-capita figure on this set is per. One unit by the
  // time this is called, because a mixed set is refused before it is drawn.
  function unitOf(found) {
    const units = Array.from(new Set(found.map((e) => unitFor(e.govType)).filter(Boolean)));
    return units.length ? units.sort()[0].replace(/s$/, '') : 'resident';
  }

  function areaRow(pid6, serviceArea) {
    const rows = DATA.areas[pid6] || [];
    for (let i = 0; i < rows.length; i += 1) {
      if (rows[i][0] === serviceArea) {
        return { total: rows[i][1], share: rows[i][2], year: rows[i][3] };
      }
    }
    return null;
  }

  function nameList(names) {
    return names.slice(0, 4).join(', ');
  }

  function perCapitaByServiceArea(pid6List, serviceArea) {
    const { found, unchartable, missing } = partition(pid6List);
    const types = Array.from(new Set(found.map((e) => e.govType))).sort();

    const mixed = mixedDenominators(found);
    if (mixed) {
      return { serviceArea, entities: [], peers: {}, excludedNoPopulation: [],
        absent: [], missing, years: [], entityTypes: types, mixedDenominators: mixed,
        unit: 'resident',
        caveats: [{ code: 'mixed_denominators', rule: '§10',
          guidance: 'This set mixes governments measured per '
            + mixed.map((u) => u.replace(/s$/, '')).join(' and per ')
            + '. Those are different measures, not one measure across different '
            + 'governments, so they cannot share an axis. Compare in total dollars, '
            + 'or split the set by type.' }] };
    }

    const rows = [];
    const noPopulation = [];
    const absent = [];
    const years = new Set();

    found.forEach((e) => {
      if (!e.population) { noPopulation.push(e.name); return; }
      const record = areaRow(e.pid6, serviceArea);
      if (!record || record.total === null) { absent.push(e.name); return; }
      years.add(record.year);
      rows.push({
        pid6: e.pid6, label: e.name, govType: e.govType,
        value: record.total / e.population, total: record.total,
        share: record.share, population: e.population, year: record.year,
      });
    });
    rows.sort((a, b) => b.value - a.value);

    const peers = {};
    Array.from(new Set(rows.map((r) => r.govType))).sort().forEach((govType) => {
      const stats = (DATA.areaStats[govType] || {})[serviceArea];
      if (stats) peers[govType] = stats;
    });
    rows.forEach((row) => {
      const stats = peers[row.govType];
      const usable = stats && !stats.degenerate && stats.median;
      row.peerMedian = usable ? stats.median : null;
      row.ratio = usable ? row.value / stats.median : null;
    });

    const caveats = [
      { code: 'inputs_not_outcomes', rule: '§4',
        guidance: 'This data measures what governments spend and who they employ. It '
          + 'contains no performance measures, service levels or results.' },
      { code: 'per_resident_basis', rule: '§10',
        guidance: 'Every figure here is spending divided by the entity’s '
          + (found.length ? unitFor(found[0].govType) : 'population')
          + '. Say the basis in the answer; a per-unit figure compared against an '
          + 'absolute one is not a comparison.' },
      { code: 'population_is_one_estimate', rule: '§8',
        guidance: 'Population is a single estimate per entity rather than a series, so '
          + 'these are levels at one moment and no rate of change can be read from them.' },
      { code: 'one_year_per_entity', rule: '§8',
        guidance: 'Service-area spending exists for one year per entity, so this is a '
          + 'snapshot. Do not describe any bar as rising or falling.' },
    ];
    const sortedYears = Array.from(years).sort();
    if (sortedYears.length > 1) {
      caveats.push({ code: 'years_differ', rule: '§8',
        guidance: 'These entities report different years (' + sortedYears.join(', ')
          + '). Say so; they are not the same moment.' });
    }
    if (types.length > 1) {
      caveats.push({ code: 'mixed_entity_types', rule: '§12',
        guidance: 'This set mixes ' + types.join(', ') + '. Per resident makes the sizes '
          + 'comparable but not the responsibilities: an Oregon county carries '
          + 'state-delegated functions a city does not.' });
      caveats.push({ code: 'oregon_county_delegated', rule: '§13',
        guidance: 'Oregon counties carry state-delegated functions cities do not, so '
          + 'they are not comparable with cities on health, corrections or judicial '
          + 'spending.' });
    }
    if (noPopulation.length) {
      caveats.push({ code: 'no_population_denominator', rule: '§10',
        guidance: noPopulation.length + ' entities have no population and are excluded '
          + 'rather than shown on another basis: ' + nameList(noPopulation) + '. Name them.' });
    }
    if (absent.length) {
      caveats.push({ code: 'absence_is_not_zero', rule: '§9',
        guidance: absent.length + ' entities report nothing in this area: '
          + nameList(absent) + '. That usually means another government holds the '
          + 'responsibility, not that the service is unfunded.' });
    }
    if (missing.length) {
      caveats.push({ code: 'entities_missing', rule: '§12',
        guidance: missing.length + ' requested entities are not in the data.' });
    }
    if (unchartable.length) caveats.push(unchartableCaveat(unchartable));

    return { serviceArea, entities: rows, peers, excludedNoPopulation: noPopulation,
      absent, missing, years: sortedYears, entityTypes: types,
      unit: unitOf(found), unchartable: unchartable.map((e) => e.name), caveats };
  }

  // Written out rather than derived from the per-resident form: it compares
  // shares, not per-resident amounts, so it carries a different median, and it
  // needs no population, so an entity excluded there belongs here.
  function serviceAreaAbsolute(pid6List, serviceArea) {
    const { found, unchartable, missing } = partition(pid6List);
    const types = Array.from(new Set(found.map((e) => e.govType))).sort();

    const rows = [];
    const absent = [];
    const years = new Set();
    found.forEach((e) => {
      const record = areaRow(e.pid6, serviceArea);
      if (!record || record.total === null) { absent.push(e.name); return; }
      years.add(record.year);
      const stats = (DATA.areaShareStats[e.govType] || {})[serviceArea];
      rows.push({
        pid6: e.pid6, label: e.name, govType: e.govType, value: record.total,
        share: record.share, year: record.year,
        peerMedianShare: stats ? stats.median : null,
        peerDegenerate: stats ? stats.degenerate : 0,
      });
    });
    rows.sort((a, b) => b.value - a.value);

    const sortedYears = Array.from(years).sort();
    const caveats = [
      { code: 'inputs_not_outcomes', rule: '§4',
        guidance: 'This data measures what governments spend and who they employ. It '
          + 'contains no performance measures, service levels or results.' },
      { code: 'one_year_per_entity', rule: '§8',
        guidance: 'Service-area spending is a single year per entity, so this is a '
          + 'snapshot and not a trend. Do not describe any of it as rising or falling.' },
    ];
    if (sortedYears.length > 1) {
      caveats.push({ code: 'years_differ', rule: '§8',
        guidance: 'These entities report different years (' + sortedYears.join(', ')
          + '). Say so; they are not the same moment.' });
    }
    if (types.length > 1) {
      caveats.push({ code: 'mixed_entity_types', rule: '§12',
        guidance: 'This set mixes ' + types.join(', ') + ', which are not comparable on '
          + 'delegated functions.' });
    }
    if (absent.length) {
      caveats.push({ code: 'absence_is_not_zero', rule: '§9',
        guidance: absent.length + ' entities report nothing in this area: '
          + nameList(absent) + '. That usually means another government holds the '
          + 'responsibility, not that the service is unfunded.' });
    }
    if (missing.length) {
      caveats.push({ code: 'entities_missing', rule: '§12',
        guidance: missing.length + ' requested entities are not in the data.' });
    }
    if (unchartable.length) caveats.push(unchartableCaveat(unchartable));

    return { serviceArea, entities: rows, absent, missing, years: sortedYears,
      entityTypes: types, unchartable: unchartable.map((e) => e.name), caveats };
  }

  function seriesFor(pid6, measure, perResident) {
    const e = entity(pid6);
    if (!e) return null;
    if (perResident && !e.population) return null;
    const column = MEASURE_COLUMN[measure];
    const points = (DATA.trends[pid6] || [])
      .filter((row) => row[column] !== null && row[column] !== undefined)
      .map((row) => [row[0], perResident ? row[column] / e.population : row[column]]);
    return points.length < 2 ? null : { pid6, label: e.name, govType: e.govType, points };
  }

  function overTime(pid6List, options) {
    const o = options || {};
    const perResident = Boolean(o.perResident);
    const measure = o.measure || 'expenditure';
    const { found, unchartable, missing } = partition(pid6List);
    const types = Array.from(new Set(found.map((e) => e.govType))).sort();

    const mixed = perResident ? mixedDenominators(found) : null;
    if (mixed) {
      return { series: [], baseline: null, indexed: Boolean(o.indexed), measure,
        excludedNoPopulation: [], excludedThin: [], missing, entityTypes: types,
        mixedDenominators: mixed, unit: 'resident',
        caveats: [{ code: 'mixed_denominators', rule: '§10',
          guidance: 'This set mixes governments measured per '
            + mixed.map((u) => u.replace(/s$/, '')).join(' and per ')
            + '. Those are different measures and cannot share an axis. Compare '
            + 'entity totals, or split the set by type.' }] };
    }

    const series = [];
    const noPopulation = [];
    const thin = [];
    found.forEach((e) => {
      if (perResident && !e.population) { noPopulation.push(e.name); return; }
      const built = seriesFor(e.pid6, measure, perResident);
      if (!built) { thin.push(e.name); return; }
      series.push(built);
    });

    // A median across mixed types is a median of two different jobs.
    let baseline = null;
    if (perResident && series.length) {
      const govTypes = Array.from(new Set(series.map((s) => s.govType)));
      if (govTypes.length === 1) {
        const stored = DATA.baselines[govTypes[0]];
        if (stored) baseline = { label: stored.label, govType: stored.gov_type || govTypes[0],
          n: stored.n, points: stored.points.map((p) => [p[0], p[1]]) };
      }
    }

    if (o.indexed) {
      const rebase = (points) => {
        const base = points.length ? points[0][1] : null;
        return points.map((p) => [p[0], base ? (p[1] / base) * 100 : null]);
      };
      series.forEach((s) => { s.points = rebase(s.points); });
      if (baseline) baseline.points = rebase(baseline.points);
    }

    const caveats = [
      { code: 'inputs_not_outcomes', rule: '§4',
        guidance: 'This data measures what governments spend and who they employ. It '
          + 'contains no performance measures, service levels or results.' },
    ];
    if (perResident) {
      caveats.push({ code: 'population_held_constant', rule: '§8',
        guidance: 'Population is a single estimate per entity, not a series, so the '
          + 'denominator is held constant across every year. What moves in these lines '
          + 'is spending. Say spending rose against a fixed population estimate.' });
    }
    caveats.push({ code: 'totals_not_categories', rule: '§8',
      guidance: 'These are entity totals. Service-area spending exists for a single year '
        + 'per entity, so no line here is a category and no category can be tracked over '
        + 'time.' });
    if (o.indexed) {
      caveats.push({ code: 'indexed_hides_level', rule: '§10',
        guidance: 'Every line starts at 100, so this shows growth and deliberately hides '
          + 'level. An entity that doubled from a low base outruns one that grew slightly '
          + 'from a high one.' });
    }
    if (baseline) {
      caveats.push({ code: 'baseline_is_peers_not_inflation', rule: '§8',
        guidance: 'The dashed line is the median across ' + baseline.n + ' Oregon '
          + baseline.govType + ' entities reporting in every year. It is a peer baseline, '
          + 'not an inflation adjustment: no price index is in this data, so none of these '
          + 'figures is in real terms.' });
    } else if (perResident) {
      caveats.push({ code: 'no_baseline_drawn', rule: '§8',
        guidance: 'No peer baseline is drawn, because this set spans more than one '
          + 'government type or too few peers report in every year.' });
    }
    if (types.length > 1) {
      caveats.push({ code: 'mixed_entity_types', rule: '§12',
        guidance: 'This set mixes ' + types.join(', ') + ', which carry different '
          + 'responsibilities.' });
    }
    if (noPopulation.length) {
      caveats.push({ code: 'no_population_denominator', rule: '§10',
        guidance: noPopulation.length + ' entities have no population and are excluded '
          + 'rather than drawn on another basis: ' + nameList(noPopulation) + '.' });
    }
    if (thin.length) {
      caveats.push({ code: 'entities_excluded', rule: '§12',
        guidance: thin.length + ' entities have fewer than two years and are not drawn: '
          + nameList(thin) + '. Name them.' });
    }
    if (missing.length) {
      caveats.push({ code: 'entities_missing', rule: '§12',
        guidance: missing.length + ' requested entities are not in the data.' });
    }
    if (unchartable.length) caveats.push(unchartableCaveat(unchartable));
    if (!perResident && series.length > DATA.layout.maxSeries) {
      caveats.push({ code: 'series_capped', rule: '§15',
        guidance: 'Past four lines the legend stops carrying identity. Only the first four '
          + 'are drawn; name the rest or split the comparison.' });
    }

    return { series, baseline, indexed: Boolean(o.indexed), measure,
      excludedNoPopulation: noPopulation, excludedThin: thin, missing,
      entityTypes: types, unit: unitOf(found),
      unchartable: unchartable.map((e) => e.name), caveats };
  }

  const MEASURE_COLUMN = { revenue: 1, expenditure: 2, debt: 3 };

  /* The trend data is two panels and only one of them is annual. Around 1,005
     governments filed 2017 and 2022 and nothing between; about 380 filed every
     year from 2019. Drawing both as lines on one axis lets a reader read a path
     through five years nobody reported, so the coverage is classified and the
     sparse ones are dashed. Mirrors explore.coverage_of. */
  const ANNUAL = 'annual';
  const ENDPOINTS = 'endpoints';
  const PARTIAL = 'partial';
  const SINGLE = 'single';

  function coverageOf(years) {
    const inside = years.slice().sort((a, b) => a - b);
    if (!inside.length) return null;
    if (inside.length === 1) return SINGLE;
    if (inside.length === (inside[inside.length - 1] - inside[0] + 1)
        && inside.length >= 3) return ANNUAL;
    if (inside.length === 2) return ENDPOINTS;
    return PARTIAL;
  }

  function trendPanel(pid6List, measure) {
    const [start, end] = DATA.windows.panel;
    const column = MEASURE_COLUMN[measure || 'expenditure'];
    const { found, unchartable, missing } = partition(pid6List);
    const series = [];
    const thin = [];
    found.forEach((e) => {
      const points = (DATA.trends[e.pid6] || [])
        .filter((row) => row[0] >= start && row[0] <= end
          && row[column] !== null && row[column] !== undefined)
        .map((row) => [row[0], row[column]]);
      const kind = coverageOf(points.map((pt) => pt[0]));
      if (kind === null || kind === SINGLE) { thin.push({ name: e.name }); return; }
      const first = points[0][1];
      const last = points[points.length - 1][1];
      series.push({
        pid6: e.pid6, label: e.name, govType: e.govType, points,
        coverage: kind, observations: points.length, first, last,
        change: first ? (last / first - 1) * 100 : null,
        interpolated: kind === ENDPOINTS || kind === PARTIAL,
      });
    });

    const kinds = new Set(series.map((s) => s.coverage));
    const caveats = [
      { code: 'inputs_not_outcomes', rule: '§4',
        guidance: 'This data measures what governments spend and who they employ. It '
          + 'contains no performance measures, service levels or results.' },
      { code: 'window_is_stated', rule: '§8',
        guidance: 'The window is ' + start + ' to ' + end + ' and was chosen, not '
          + 'inferred from whichever entity reported longest. Say the window; a '
          + 'different one produces a different and equally true picture.' },
      { code: 'totals_not_categories', rule: '§8',
        guidance: 'These are entity totals. Service-area spending exists for one year '
          + 'per entity, so no line here is a category.' },
    ];
    if (kinds.has(ENDPOINTS) || kinds.has(PARTIAL)) {
      const sparse = series.filter((s) => s.interpolated).map((s) => s.label);
      caveats.push({ code: 'series_not_annual', rule: '§8',
        guidance: sparse.length + ' of these are not annual series: ' + nameList(sparse)
          + ' report only some years in this window, and the line between their '
          + 'observations is drawn, not measured. They are dashed for that reason. Do '
          + 'not describe a year nobody filed, and do not read a rate of change off a '
          + 'dashed segment.' });
    }
    if (kinds.has(ANNUAL) && kinds.size > 1) {
      caveats.push({ code: 'mixed_density', rule: '§8',
        guidance: 'This chart mixes annual series with series measured at two ends. '
          + 'They are not equally precise and the difference is not visible in the '
          + 'shape of a line. Say which are which.' });
    }
    if (thin.length) {
      caveats.push({ code: 'entities_excluded', rule: '§12',
        guidance: thin.length + ' entities report fewer than two years inside this '
          + 'window and are not drawn: ' + nameList(thin.map((t) => t.name)) + '. Name them.' });
    }
    if (missing.length) {
      caveats.push({ code: 'entities_missing', rule: '§12',
        guidance: missing.length + ' requested entities are not in the data.' });
    }
    if (unchartable.length) caveats.push(unchartableCaveat(unchartable));

    series.sort((a, b) => (b.last || 0) - (a.last || 0));
    return { measure: measure || 'expenditure', start, end, series,
      excludedThin: thin, missing,
      annual: series.filter((s) => s.coverage === ANNUAL).length,
      interpolated: series.filter((s) => s.interpolated).length,
      count: series.length,
      unchartable: unchartable.map((e) => e.name), caveats };
  }

  function compareChange(pid6List, measure) {
    const [start, end] = DATA.windows.change;
    const key = measure || 'expenditure';
    const column = MEASURE_COLUMN[key];
    const { found, unchartable, missing } = partition(pid6List);
    const rows = [];
    const incomplete = [];
    found.forEach((e) => {
      const all = DATA.trends[e.pid6] || [];
      const at = (year) => {
        const row = all.filter((r) => r[0] === year)[0];
        return row && row[column] !== null && row[column] !== undefined
          ? row[column] : null;
      };
      const first = at(start);
      const last = at(end);
      if (!first || last === null) { incomplete.push(e.name); return; }
      const observed = all.filter((r) => r[0] >= start && r[0] <= end
        && r[column] !== null && r[column] !== undefined).length;
      rows.push({ pid6: e.pid6, label: e.name, govType: e.govType,
        first, last, value: (last / first - 1) * 100, difference: last - first,
        observations: observed, annual: observed === (end - start + 1) });
    });
    rows.sort((a, b) => b.value - a.value);

    const stats = (DATA.changeStats || {})[key] || {};
    const peers = {};
    rows.forEach((row) => {
      const s = stats[row.govType];
      row.peerMedian = s ? s.median : null;
      row.peerN = s ? s.n : null;
      if (s) peers[row.govType] = s;
    });

    const caveats = [
      { code: 'inputs_not_outcomes', rule: '§4',
        guidance: 'This data measures what governments spend and who they employ. It '
          + 'contains no performance measures, service levels or results.' },
      { code: 'change_not_trend', rule: '§8',
        guidance: 'This is the change between ' + start + ' and ' + end + ', which is '
          + 'two observations five years apart and not a trend. Nothing here says '
          + 'whether the change was steady, front-loaded or reversed in between. Do '
          + 'not describe a direction over the middle years, and do not call it a '
          + 'growth rate per year.' },
      { code: 'no_price_index', rule: '§8',
        guidance: 'No price index exists in this data, so these changes are in nominal '
          + 'dollars. A government that grew is not necessarily buying more than it '
          + 'did; say the figures are not inflation adjusted.' },
    ];
    const groups = Object.keys(peers).sort();
    if (groups.length) {
      caveats.push({ code: 'peer_group_stated', rule: '§8',
        guidance: 'The peer medians are over '
          + groups.map((t) => peers[t].n + ' Oregon ' + t).join(', ')
          + ' entities reporting both years. State the group and the count; a median '
          + 'without its pool is not a benchmark.' });
    }
    if (rows.some((r) => !r.annual)) {
      caveats.push({ code: 'endpoints_only', rule: '§8',
        guidance: 'Most of these governments report only these two years, so the '
          + 'middle of the window is not in the data at all. That is why this is drawn '
          + 'as a change and not as a line.' });
    }
    if (incomplete.length) {
      caveats.push({ code: 'entities_excluded', rule: '§12',
        guidance: incomplete.length + ' entities do not report both ' + start + ' and '
          + end + ' and are not shown: ' + nameList(incomplete) + '. That is an absence '
          + 'of a filing, not a change of zero.' });
    }
    if (missing.length) {
      caveats.push({ code: 'entities_missing', rule: '§12',
        guidance: missing.length + ' requested entities are not in the data.' });
    }
    if (unchartable.length) caveats.push(unchartableCaveat(unchartable));

    return { measure: key, start, end, span: end - start, entities: rows, peers,
      excluded: incomplete, missing, count: rows.length,
      unchartable: unchartable.map((e) => e.name), caveats };
  }

  // -------------------------------------------------------------- headline

  function headline(form, detail) {
    if (form === 'per_capita_by_service_area' || form === 'service_area_across_entities') {
      if (detail.mixedDenominators) {
        return 'This set mixes governments measured per '
          + detail.mixedDenominators.map((u) => u.replace(/s$/, '')).join(' and per ')
          + ". A school district's population is its enrollment, so those are "
          + 'different measures and cannot be ranked against each other. Compare in '
          + 'total dollars, or split the set by type.';
      }
      const rows = detail.entities;
      if (!rows.length) {
        return 'None of these governments reports spending in that area. That usually '
          + 'means another government holds the responsibility, not that the service is '
          + 'unfunded.';
      }
      const perResident = form === 'per_capita_by_service_area';
      const formatter = perResident ? rate : money;
      const basis = perResident ? 'per ' + (detail.unit || 'resident')
        : 'in total dollars, which ranks by population';
      const top = rows[0];
      const bottom = rows[rows.length - 1];
      /* One row is not a comparison. Reading rows[0] and rows[-1] off a single
         row produced "Portland is highest at $709 and Portland lowest at $709",
         which stages a race between a government and itself. */
      let line;
      if (rows.length === 1) {
        line = 'Only ' + top.label + ' can be drawn here, at ' + formatter(top.value)
          + ' on ' + detail.serviceArea + ' ' + basis + '. One government is a figure, '
          + 'not a comparison.';
      } else {
        line = 'On ' + detail.serviceArea + ' ' + basis + ', ' + top.label
          + ' is highest at ' + formatter(top.value) + ' and ' + bottom.label
          + ' lowest at ' + formatter(bottom.value) + '.';
        if (bottom.value) {
          line += ' That is a ' + (top.value / bottom.value).toFixed(1) + '× spread.';
        }
      }
      if (detail.unchartable && detail.unchartable.length) {
        const one = detail.unchartable.length === 1;
        line += ' ' + detail.unchartable.join(', ') + (one ? ' reports' : ' report')
          + ' neither a spending breakdown nor a year of totals, so there is nothing '
          + 'to draw for ' + (one ? 'it' : 'them')
          + ' here; that is not a report of zero.';
      }
      if (detail.absent && detail.absent.length) {
        line += ' ' + detail.absent.length + ' of the set reports nothing here: '
          + detail.absent.join(', ') + '.';
      }
      if (detail.excludedNoPopulation && detail.excludedNoPopulation.length) {
        line += ' ' + detail.excludedNoPopulation.length + ' has no population in the data '
          + 'and cannot be put on this basis: ' + detail.excludedNoPopulation.join(', ') + '.';
      }
      return line;
    }

    if (form === 'five_year_change') {
      const rows = detail.entities;
      if (!rows.length) {
        return 'None of these governments reports both ' + detail.start + ' and '
          + detail.end + ', so there is nothing to measure between. That is an absence '
          + 'of a filing, not a change of zero.';
      }
      const top = rows[0];
      const bottom = rows[rows.length - 1];
      const signed = (v) => (v >= 0 ? '+' : '') + v.toFixed(0) + '%';
      let line = rows.length === 1
        ? top.label + ' changed ' + signed(top.value) + ' between ' + detail.start
          + ' and ' + detail.end + '.'
        : 'Between ' + detail.start + ' and ' + detail.end + ', ' + top.label
          + ' changed most at ' + signed(top.value) + ' and ' + bottom.label
          + ' least at ' + signed(bottom.value) + '.';
      const against = rows.filter((r) => r.peerMedian !== null);
      if (against.length) {
        const ahead = against.filter((r) => r.value > r.peerMedian);
        line += ' ' + ahead.length + ' of ' + against.length + ' outgrew the median '
          + 'for their own government type over the same two years.';
      }
      line += ' This is a ' + detail.span + '-year change measured at two ends, not a '
        + 'trend: nothing here says whether it was steady, and no price index is in '
        + 'this data, so the figures are nominal.';
      return line;
    }

    if (form === 'five_year_panel') {
      const series = detail.series;
      if (series.length < 2) {
        return 'Fewer than two of these governments report more than one year between '
          + detail.start + ' and ' + detail.end + '. Most Oregon governments file two '
          + 'years five years apart rather than annually, so an annual panel covers '
          + 'about 380 of them. The change between 2017 and 2022 is the five-year '
          + 'comparison nearly all can answer.';
      }
      const ranked = series.filter((s) => s.change !== null)
        .map((s) => [s.label, s.change]).sort((a, b) => b[1] - a[1]);
      const signed = (v) => (v >= 0 ? '+' : '') + v.toFixed(0) + '%';
      let line = 'Across ' + detail.start + ' to ' + detail.end + ', ' + ranked[0][0]
        + ' grew fastest at ' + signed(ranked[0][1]) + ' and '
        + ranked[ranked.length - 1][0] + ' slowest at '
        + signed(ranked[ranked.length - 1][1]) + '.';
      if (detail.annual) {
        line += ' ' + detail.annual + ' of ' + series.length + ' are measured every '
          + 'year in this window.';
      }
      if (detail.interpolated) {
        line += ' ' + detail.interpolated + ' are not, and their lines are dashed '
          + 'between observations because the years between were not filed.';
      }
      const thin = detail.excludedThin || [];
      if (thin.length) {
        const one = thin.length === 1;
        line += ' ' + thin.slice(0, 3).map((t) => t.name).join(', ')
          + (one ? ' reports' : ' report') + ' fewer than two years between '
          + detail.start + ' and ' + detail.end + ' and ' + (one ? 'is' : 'are')
          + ' not drawn; the five-year change reaches ' + (one ? 'it' : 'them') + '.';
      }
      return line;
    }

    if (detail.mixedDenominators) {
      return 'This set mixes governments measured per '
        + detail.mixedDenominators.map((u) => u.replace(/s$/, '')).join(' and per ')
        + ', which are different measures and cannot share an axis. Compare entity '
        + 'totals, or split the set by type.';
    }
    const series = detail.series;
    if (series.length < 2) {
      const unit = detail.unit || 'resident';
      return 'Fewer than two of these governments can be drawn over time. An entity needs '
        + 'two or more years, and a per-' + unit + ' line needs a ' + unit + ' count.';
    }
    const growth = series
      .filter((s) => s.points.length && s.points[0][1])
      .map((s) => [s.label, s.points[s.points.length - 1][1] / s.points[0][1]])
      .sort((a, b) => b[1] - a[1]);
    const years = series[0].points[0][0] + ' and '
      + series[0].points[series[0].points.length - 1][0];
    let line = 'Between ' + years + ', ' + growth[0][0] + ' grew fastest at '
      + growth[0][1].toFixed(2) + '× and ' + growth[growth.length - 1][0]
      + ' slowest at ' + growth[growth.length - 1][1].toFixed(2) + '×.';
    if (detail.baseline && detail.baseline.points[0][1]) {
      const points = detail.baseline.points;
      const pace = points[points.length - 1][1] / points[0][1];
      const ahead = growth.filter((g) => g[1] > pace).length;
      line += ' The ' + detail.baseline.govType + ' median grew ' + pace.toFixed(2)
        + '× over the same years, so ' + ahead + ' of ' + growth.length
        + ' outgrew their peers.';
    }
    if (detail.indexed) {
      line += ' These lines show growth and deliberately hide level: a government that '
        + 'doubled from a low base outruns one that grew slightly from a high one.';
    }
    return line;
  }

  // ------------------------------------------------------------------ api

  const FORMS = ['per_capita_by_service_area', 'per_capita_over_time',
    'service_area_across_entities', 'entities_over_time',
    'five_year_panel', 'five_year_change'];

  function compare(request) {
    const form = request.form;
    if (FORMS.indexOf(form) < 0) {
      return { blocks: [{ kind: 'answer', text: 'No comparison form ‘' + form + '’.' }],
        trace: [] };
    }
    const serviceArea = request.service_area;
    if ((form === 'per_capita_by_service_area' || form === 'service_area_across_entities')
        && !serviceArea) {
      return { blocks: [{ kind: 'answer',
        text: 'This form compares within one service area and needs to be told which. '
          + 'Without one every entity is 100% of its own spending.' }], trace: [] };
    }

    let detail;
    let svg;
    let table;

    if (form === 'five_year_change') {
      detail = compareChange(request.pid6_list, request.measure);
      const rows = detail.entities;
      const label = DATA.measureLabels[detail.measure] || 'Total spending';
      if (!rows.length) {
        svg = refusal('Change in ' + label.toLowerCase() + ', ' + detail.start + ' to '
          + detail.end,
          'None of these governments reports both ' + detail.start + ' and '
          + detail.end + ', so there are no two ends to measure between. That is an '
          + 'absence of a filing, not a change of zero.');
      } else {
        svg = divergingBars(label + ': change over ' + detail.span + ' years',
          detail.start + ' to ' + detail.end + ', nominal dollars',
          rows.map((r) => ({ label: r.label, value: r.value, peerMedian: r.peerMedian })),
          { formatter: (v) => (v >= 0 ? '+' : '') + v.toFixed(0) + '%',
            note: 'Two observations five years apart. Nothing here says whether the '
              + 'change was steady, front-loaded or reversed in between, and no price '
              + 'index is in this data, so these are nominal.' });
        table = rows.map((r) => {
          const row = { government: r.label, type: r.govType };
          row[String(detail.start)] = money(r.first);
          row[String(detail.end)] = money(r.last);
          row.change = (r.value >= 0 ? '+' : '') + r.value.toFixed(0) + '%';
          row['type median change'] = r.peerMedian === null ? 'not comparable'
            : (r.peerMedian >= 0 ? '+' : '') + r.peerMedian.toFixed(0) + '%';
          row['years reported'] = r.observations;
          return row;
        });
      }
    } else if (form === 'five_year_panel') {
      detail = trendPanel(request.pid6_list, request.measure);
      const series = detail.series;
      const label = DATA.measureLabels[detail.measure] || 'Total spending';
      if (series.length < 2) {
        svg = refusal(label + ', ' + detail.start + ' to ' + detail.end,
          'Fewer than two of these governments report more than one year inside '
          + detail.start + ' to ' + detail.end + '. Most Oregon governments file two '
          + 'years five years apart rather than annually, so a five-year annual panel '
          + 'exists for about 380 of them. Compare the change between 2017 and 2022 '
          + 'instead, which nearly all can answer.');
      } else {
        const drawn = series.slice(0, DATA.layout.maxSeries);
        let note = detail.annual
          ? detail.annual + ' of these are measured every year. ' : '';
        if (detail.interpolated) {
          note += 'Dashed lines are drawn between observations that are years apart; '
            + 'the years between were not filed.';
        }
        if (series.length > DATA.layout.maxSeries) {
          note += ' Four lines drawn: '
            + series.slice(DATA.layout.maxSeries).map((s) => s.label).join(', ')
            + ' also requested.';
        }
        svg = multiSeries(label + ': ' + plural(drawn.length, 'government') + ' compared',
          detail.start + ' to ' + detail.end + ', entity totals in nominal dollars',
          drawn, { formatter: money, note: note.trim() || null });
        table = [];
        for (let year = detail.start; year <= detail.end; year += 1) {
          const row = { year };
          drawn.forEach((entry) => {
            const point = entry.points.filter((pt) => pt[0] === year)[0];
            row[entry.label] = point ? money(point[1]) : '\u2014';
          });
          table.push(row);
        }
      }
    } else if (form === 'per_capita_by_service_area') {
      detail = perCapitaByServiceArea(request.pid6_list, serviceArea);
      const rows = detail.entities;
      if (detail.mixedDenominators) {
        const units = detail.mixedDenominators.map((u) => u.replace(/s$/, '')).join(' and per ');
        svg = refusal('Spending on ' + serviceArea + ', per unit',
          'Not drawn. This set mixes governments measured per ' + units + '. A school '
          + "district's population is its enrollment, so dividing by it gives "
          + 'spending per student, and that cannot share an axis with spending per '
          + 'resident. Compare in total dollars, or split the set by type.');
      } else if (!rows.length) {
        svg = refusal('Spending on ' + serviceArea + ' per ' + detail.unit,
          'None of these entities can be put on a per-' + detail.unit + ' basis for this '
          + 'area: either they report nothing here, or they have no population in the '
          + 'data. Neither is a report of zero spending.');
      } else {
        const singleType = Array.from(new Set(rows.map((r) => r.govType))).length === 1;
        const span = detail.years.length === 1 ? String(detail.years[0])
          : detail.years[0] + ' to ' + detail.years[detail.years.length - 1] + ', one year each';
        svg = horizontalBars(serviceArea + ': spending per ' + detail.unit,
          plural(rows.length, 'government') + ', ' + span,
          rows.map((r) => ({ label: r.label, value: r.value,
            peerMedian: singleType ? r.peerMedian : null,
            peerDegenerate: !(singleType && r.peerMedian) })),
          { formatter: rate,
            note: singleType && rows.some((r) => r.peerMedian)
              ? 'Tick marks the median for this government type.'
              : 'Mixed government types, so no single peer median applies.' });
        table = rows.map((r) => {
          const row = { government: r.label, type: r.govType };
          row['per ' + detail.unit] = rate(r.value);
          row.total = money(r.total);
          row[detail.unit + 's'] = count(r.population);
          row['share of its own budget'] = percent(r.share);
          row['vs type median'] = r.ratio ? r.ratio.toFixed(2) + '×' : 'not comparable';
          row.year = r.year;
          return row;
        });
      }
    } else if (form === 'service_area_across_entities') {
      detail = serviceAreaAbsolute(request.pid6_list, serviceArea);
      const rows = detail.entities;
      if (!rows.length) {
        svg = refusal('Spending on ' + serviceArea,
          'None of these entities reports spending in this area. That usually means '
          + 'another government holds the responsibility, not that the service is unfunded.');
      } else {
        const span = detail.years.length === 1 ? String(detail.years[0])
          : detail.years[0] + ' to ' + detail.years[detail.years.length - 1] + ', one year each';
        svg = horizontalBars('Spending on ' + serviceArea,
          plural(rows.length, 'government') + ', ' + span,
          rows.map((r) => ({ label: r.label, value: r.value, peerMedian: null,
            peerDegenerate: true })),
          { note: 'A snapshot, not a trend. Service-area spending exists for one year per '
            + 'entity, so none of these bars can be tracked over time.' });
        table = rows.map((r) => ({ government: r.label, type: r.govType,
          spending: money(r.value), 'share of its own budget': percent(r.share),
          'peer median share': r.peerDegenerate ? 'not comparable'
            : percent(r.peerMedianShare),
          year: r.year }));
      }
    } else {
      const perResident = form === 'per_capita_over_time';
      const indexed = perResident && Boolean(request.indexed);
      detail = overTime(request.pid6_list,
        { perResident, indexed, measure: request.measure || 'expenditure' });
      const series = detail.series;
      if (detail.mixedDenominators) {
        const units = detail.mixedDenominators.map((u) => u.replace(/s$/, '')).join(' and per ');
        svg = refusal('Spending per unit over time',
          'Not drawn. This set mixes governments measured per ' + units + ', which are '
          + 'different measures and cannot share an axis. Compare entity totals, or '
          + 'split the set by type.');
      } else if (series.length < 2) {
        svg = perResident
          ? refusal('Spending per ' + detail.unit + ' over time',
              'Fewer than two entities can be drawn: an entity needs both a '
              + detail.unit + ' count in the data and two or more years of spending. '
              + 'One year is a point, not a trend.')
          : refusal('Total spending over time',
              'Fewer than two entities have two or more years of data, so there is '
              + 'nothing to compare. One year is a point, not a trend.');
      } else {
        const drawn = series.slice(0, DATA.layout.maxSeries);
        const years = Array.from(new Set([].concat.apply([],
          drawn.map((s) => s.points.map((p) => p[0]))))).sort();
        let note = perResident
          ? 'The ' + detail.unit + ' count is one estimate per entity and is held constant, '
            + 'so what moves here is spending.'
          : null;
        const folded = series.slice(DATA.layout.maxSeries).map((s) => s.label);
        if (perResident && folded.length) {
          note += ' Four lines drawn: ' + folded.join(', ') + ' also requested.';
        } else if (!perResident && folded.length) {
          note = 'Four lines drawn. Also requested: ' + folded.join(', ') + '.';
        }
        const formatter = indexed ? index : (perResident ? rate : money);
        svg = multiSeries(
          indexed ? 'Spending per ' + detail.unit + ', indexed'
            : (perResident ? 'Spending per ' + detail.unit
              : 'Total spending: ' + plural(drawn.length, 'government') + ' compared'),
          indexed ? years[0] + ' = 100, ' + drawn.length + ' governments compared'
            : years[0] + ' to ' + years[years.length - 1]
              + (perResident ? ', entity totals divided by ' + detail.unit + 's'
                : ', entity totals rather than any single category'),
          drawn,
          { formatter, note, reference: detail.baseline,
            baseline: indexed ? 100 : null });
        table = years.map((year) => {
          const row = { year };
          drawn.forEach((s) => {
            const point = s.points.filter((p) => p[0] === year)[0];
            row[s.label] = point ? formatter(point[1]) : null;
          });
          if (detail.baseline) {
            const point = detail.baseline.points.filter((p) => p[0] === year)[0];
            row[detail.baseline.label] = point ? formatter(point[1]) : null;
          }
          return row;
        });
      }
    }

    const unchartable = detail.unchartable || [];
    if (unchartable.length && svg) {
      svg = appendNote(svg, 'Not drawn: ' + unchartable.join(', ')
        + ' \u2014 neither a spending breakdown nor a year of totals is reported. '
        + 'That is an absence, not a zero.');
    }

    const blocks = [{ kind: 'answer', text: headline(form, detail) }];
    if (svg) blocks.push({ kind: 'chart', svg, table: table || null });
    if (detail.caveats.length) {
      blocks.push({ kind: 'limits',
        rules: detail.caveats.map((c) => Object.assign({ kind: 'must' }, c)) });
    }
    return { blocks, trace: ['render_comparison'], refused: !table };
  }

  global.PFCompare = { load, compare, formatters: { money, rate, percent, count, index },
    internals: { perCapitaByServiceArea, overTime, serviceAreaAbsolute, headline,
      horizontalBars, multiSeries, niceTicks } };
}(typeof globalThis !== 'undefined' ? globalThis : this));

/* How a table block is drawn.
 *
 * Every table the thread shows goes through here: the ones the server composes
 * and the ones comparison.js assembles in the browser with no server involved.
 * One implementation on purpose, so a comparison rendered from a pre-built
 * static file and the same comparison rendered from a live server cannot
 * disagree about anything but their data.
 *
 * agent/reports.py draws its own tables and deliberately does not use these
 * rules. The full report is a document with its own stylesheet and its own
 * audience; this is interface behaviour.
 *
 *     python3 app/test_tables.py
 */
(function (root) {
  'use strict';

  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /* A cell that is a figure, so it can be set as one. Money, percentages,
     counts and durations all read as quantities and want tabular numerals and a
     right edge to line up on; a government's name does not. Detected rather
     than declared, because table blocks are plain dicts built in a dozen places
     and a per-column type would have to be remembered at every one of them. */
  const FIGURE_CELL = /^[−-]?[$]?[\d][\d,.]*\s*(%|K|M|B|min|hrs?|days?|years?)?$/;

  /* A verdict is a state rather than a quantity. These are the ones this data
     produces, and each ships as a mark *and* its word: colour is the fast read,
     the word is the one that survives a colourblind reader, a printout, and a
     screenshot pasted into a document. */
  const VERDICT = {
    'yes': 'good',
    'drawn': 'good',
    'no, the margin is too wide': 'warning',
    'no margin given': 'warning',
    'refused': 'warning',
    'no estimate': 'muted',
    'not on this layer': 'muted',
    'needs a selector': 'muted',
  };

  /* Which columns are the same in every row, and which are left to draw.
     Separated from the markup because it is the part with a rule in it. */
  /* Keys a row may carry that are instructions to the renderer rather than
     cells. A hierarchy needs to say which level a row is on, and the only other
     way to carry that is a column of indentation the reader can see. */
  const CONTROL = ['_depth'];

  function layout(rows) {
    const columns = Object.keys(rows[0] || {}).filter((c) => CONTROL.indexOf(c) < 0);

    /* One row is a record, not a table, and every one of its columns is
       trivially "the same in every row". Lifting on that turned Estacada's
       single silent government into a caption reading REPORTS NOTHING HERE
       Estacada School District 108 TYPE School District above a table whose
       only column was "established by" — every value still present and none of
       them in a place a reader would look. Constancy is a fact about a set of
       rows, so it takes a set of rows. */
    if (rows.length < 2) return { lifted: [], kept: columns };

    /* A column where every row says the same thing is not a column. Nine
       governments each carrying "nothing, which usually means another
       government holds this here" is one fact rendered nine times, and it
       buries the two rows that do carry a figure. The shared value is stated
       once above the table and the column comes out.

       Measured, never declared: it lifts only when the data happens to be
       constant and stops the moment one row differs, so a value is moved and
       never dropped. The last column standing is always kept, because a table
       of no columns is not a simplification of anything. */
    const constant = columns.filter((c) => {
      const first = String(rows[0][c] === null || rows[0][c] === undefined ? '' : rows[0][c]);
      return first !== '' && rows.every(
        (r) => String(r[c] === null || r[c] === undefined ? '' : r[c]) === first);
    });
    const shown = columns.filter((c) => constant.indexOf(c) < 0);
    return shown.length
      ? { lifted: constant, kept: shown }
      : { lifted: constant.slice(0, -1), kept: columns.slice(-1) };
  }

  function card(rows, caption) {
    if (!rows || !rows.length) return '';
    const plan = layout(rows);
    const kept = plan.kept;

    const notes = plan.lifted.map(
      (c) => '<span><i>' + esc(c) + '</i> ' + esc(rows[0][c]) + '</span>');
    if (caption) notes.unshift('<span>' + esc(caption) + '</span>');

    const cell = (value) => {
      const text = String(value === null || value === undefined ? '' : value);
      const verdict = VERDICT[text.toLowerCase()];
      if (verdict) {
        return '<td class="verdict"><b class="dot ' + verdict + '"></b>' + esc(text) + '</td>';
      }
      return FIGURE_CELL.test(text.trim())
        ? '<td class="num">' + esc(text) + '</td>'
        : '<td>' + esc(text) + '</td>';
    };

    /* The first column carries the row and is set to be scanned, but only when
       there is something to scan it against. A one-column table is a list of
       sentences, and setting all of them at full strength made the four asks
       closing a brief read as four headings with no body under them. */
    const leads = kept.length > 1;
    const head = kept.map(
      (c, i) => '<th' + (leads && !i ? ' class="lead"' : '') + '>' + esc(c) + '</th>').join('');
    /* A nested row is indented and set quieter than its parent. Depth is drawn
       with padding rather than with a marker glyph, because the eye reads the
       left edge of a column faster than it reads a symbol, and the parent's own
       total is right there above it to read the child against. */
    const body = rows.map((row) => '<tr' + (row._depth
      ? ' class="child" style="--depth:' + Number(row._depth) + '"' : '')
      + '>' + kept.map(
      (c, i) => ((leads && !i)
        ? '<td class="lead">' + esc(row[c]) + '</td>'
        : cell(row[c]))).join('') + '</tr>').join('');

    return '<div class="card">'
      + (notes.length ? '<div class="tnote">' + notes.join('') + '</div>' : '')
      + '<table><thead><tr>' + head + '</tr></thead><tbody>' + body
      + '</tbody></table></div>';
  }

  root.PFTables = { card: card, layout: layout, FIGURE_CELL: FIGURE_CELL,
                    VERDICT: VERDICT };
}(typeof window === 'undefined' ? global : window));

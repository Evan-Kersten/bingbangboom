/* Resolving a typed word to a subject, in the browser.
 *
 * The second implementation of rules.resolve_topic, and it exists for the same
 * reason comparison.js does: a pre-rendered build has no server, and the page
 * still has to turn "unhoused" into the homelessness brief that was rendered
 * ahead of time. In static mode this function is the authority on which brief a
 * reader gets, so a disagreement with Python is not a cosmetic difference — it
 * sends somebody to the wrong subject, or to none.
 *
 * app/test_parity.py drives both over the same terms and fails on any
 * disagreement. That test is what makes the duplication acceptable.
 *
 * The vocabulary itself is not duplicated. The alias index arrives from
 * /api/topics (or data/topics.json), one definition in rules.py, so a word
 * added there is reachable here without touching this file.
 */
(function (root) {
  'use strict';

  function words(needle) {
    return (needle.match(/[a-z0-9]+/g) || []);
  }

  /* Best fit first, and the pass order is the whole behaviour. An exact hit on
     a concordance topic or an alias wins outright; only a term matching nothing
     exactly falls through to prefix matching. Without that, "water" drags in
     wastewater and drinking water is offered the sewer concordance first. */
  function resolve(term, index) {
    const needle = String(term || '').toLowerCase().split(/\s+/).filter(Boolean).join(' ');
    if (!needle || !index) return [];
    const topics = index.topics || [];
    const aliases = index.aliases || {};

    if (topics.indexOf(needle) >= 0) return [needle];
    if (Object.prototype.hasOwnProperty.call(aliases, needle)) {
      return aliases[needle].slice();
    }
    // Two characters is enough to be typing and not enough to mean anything.
    if (needle.length < 3) return [];

    const found = [];
    topics.forEach((topic) => {
      if (needle.indexOf(topic) >= 0 || topic.indexOf(needle) >= 0) found.push(topic);
    });

    /* Aliases match on whole words and the topics above do not. The topics are
       long enough that a bare substring is safe; the aliases include "it",
       "ems" and "911", and a bare substring made "housing situation" resolve to
       technology because "it" sits inside "situation".

       Both directions, because this runs on every keystroke: the typed word is
       the shorter one while somebody is still typing it, and the longer one
       once they have finished and pluralised it. */
    const typed = words(needle);
    Object.keys(aliases).sort().forEach((alias) => {
      const hit = alias.indexOf(needle) === 0 || alias.split(' ').every(
        (part) => typed.some(
          (w) => w.indexOf(part) === 0 || (w.length >= 3 && part.indexOf(w) === 0)));
      if (!hit) return;
      aliases[alias].forEach((topic) => {
        if (found.indexOf(topic) < 0) found.push(topic);
      });
    });
    return found;
  }

  root.PFSubjects = { resolve: resolve };
}(typeof window === 'undefined' ? global : window));

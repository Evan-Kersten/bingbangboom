# Putting the interface online

The interface can run two ways. You do not need Python for either one.

## The short version

Push to GitHub, turn Pages on once, and every push republishes the site.

1. Open the repository on GitHub
2. **Settings** → **Pages** (left sidebar)
3. Under **Build and deployment**, set **Source** to **GitHub Actions**
4. Done. Go to the **Actions** tab and watch the first run, about three minutes

The site lands at `https://<your-username>.github.io/<repository>/`.

Nothing is installed and nothing is committed. GitHub runs the build, renders
every answer, and publishes the result.

## What actually happens up there

`.github/workflows/pages.yml` runs on every push:

1. builds the store from the source files, about seven seconds
2. verifies it, 45 checks
3. adds the measured service extent from the precinct dissolve
4. runs every test suite, so a broken build never reaches the site
5. pre-renders all 17,843 answers, about forty seconds
6. uploads the result to Pages

The rendered site is roughly 150 MB across 19,000 files. It is uploaded
straight to Pages rather than committed, which is what keeps the repository
from growing by that much on every push. `site/` is gitignored for the same
reason.

## What the published site can and cannot do

**Works:** every preset for all 1,529 governments, every chart, every map,
every rules panel, search, and the follow-up chips. Answers are pre-rendered,
so the site is fast and needs no server.

**Does not work:** the free-text box. Free text needs a model, a model needs an
API key, and an API key cannot go in a static site, because anything shipped to
the browser is public. The published interface says so in the composer.

## If you want free text too

That needs a host that runs Python, not GitHub Pages. Anywhere that runs a
Python process will do. The command is:

```
python3 etl/build.py && python3 app/server.py
```

with `ANTHROPIC_API_KEY` set in the host's environment, where it stays private.
Render, Railway and Fly all do this from a repository with no configuration
beyond that command and the key.

A reasonable arrangement is both: Pages for the public, browsable interface,
and a small private instance with the key for the free-text version.

## Rebuilding after the data changes

Push. That is the whole procedure. To rebuild without pushing, open the
**Actions** tab, choose **Build and deploy the interface**, and press
**Run workflow**.

# FOUS-style Forecast

A small, phone-friendly page that presents a recurring RRFS-based forecast in the familiar FOUS layout. GitHub's hosted workflow checks at 03:15, 09:15, 15:15, and 21:15 UTC, retrieves the corresponding RRFS run, and publishes the refreshed text. It also checks the same NAM cycle against the matching official FOUS bulletin when both are available, starting with the T1/T3/T5 layer temperatures and six-hour precipitation. The current six-city list is ALB, BTV, BOS, LGA, PHL, and IPT; add later station codes and coordinates in `site/data/stations.json`.

T1, T3, and T5 are estimated pressure-weighted temperature averages over the familiar FOUS layers, using RRFS standard pressure levels and a 2 m surface-temperature anchor. These are practical layer estimates, not exact averages from the model's native layers.

The displayed product is experimental and is not an official NWS bulletin. The page preserves the last published forecast if a refresh fails. Detailed NAM comparison tables and selected source files are saved as a downloadable GitHub Actions artifact for each scheduled run; local research notes and the large raw model archive remain outside the public repository. Only `site/`, `ops/`, and this short readme are published.

## Turn on the website

The site and its scheduled weather-data checks run on GitHub. They do not require the Mac to be turned on or ask for local file or internet permission each time.

The scheduled workflow runs from the repository's default branch. GitHub says scheduled runs can be delayed under high load; the selected :15 minute offsets avoid the top of the hour, but the schedule cannot promise an exact-to-the-minute update. See [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) and [GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages).

# FOUS-style forecast webpage

This is the first, static webpage version of the familiar FOUS display. It keeps the forecast fixed-width, allows sideways scrolling on a phone, and offers copy and download buttons. Station names and coordinates live in `data/stations.json`, so the list can grow without redesigning the page.

The files under `site/data/` currently show the saved Sep 29 06Z RRFS pilot. The page is a display prototype; it does not yet fetch fresh weather data by itself. The next step is to connect a cloud scheduled job to the reusable RRFS intake and candidate builder, then publish updated `latest.txt` and `status.json` files after each successful run. That job must leave the last good forecast in place if NOAA data are late or a run fails.

The intended schedule is 03:15, 09:15, 15:15, and 21:15 UTC. Hosting and access settings must be chosen before publication. A public GitHub Pages site can be served free from a public repository; GitHub documents that Pages sites are publicly available on the internet. Private GitHub Pages access is limited to Enterprise Cloud organization setups. Sources: [GitHub Pages overview](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages), [Pages visibility](https://docs.github.com/en/enterprise-cloud%40latest/pages/getting-started-with-github-pages/changing-the-visibility-of-your-github-pages-site).

Do not include research notebooks, raw GRIB files, source archives, or credentials in the public site. Publish only the page, current text bulletin, and small public status/station files.

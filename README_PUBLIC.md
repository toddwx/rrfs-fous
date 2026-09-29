# FOUS-style Forecast

A small, phone-friendly page that presents a recurring RRFS-based forecast in the familiar FOUS layout. The scheduled job checks at 03:15, 09:15, 15:15, and 21:15 UTC, retrieves the corresponding 00Z/06Z/12Z/18Z RRFS long-range run, and publishes the refreshed text when the whole update succeeds. The current station list starts with ALB, BTV, BOS, and LGA; add later station codes and coordinates in `site/data/stations.json`.

The displayed product is experimental and is not an official NWS bulletin. The page preserves the last published version if a refresh fails. Raw research files and local notes are deliberately excluded from this public repository; only `site/`, `ops/`, and this short readme are allowed into the public repository.

## Turn on the website

1. Create a new **public** GitHub repository for this folder. A public repository is needed for free GitHub Pages hosting.
2. Publish the allow-listed files from this folder to its `main` branch.
3. In the repository settings, choose **Pages → Build and deployment → GitHub Actions**.
4. Open the **Actions** tab and enable workflows if GitHub asks.
5. Run **Refresh and publish FOUS-style forecast** once manually. When it completes, GitHub shows the public website link in the deployment details.

The scheduled workflow runs from the repository's default branch. GitHub says scheduled runs can be delayed under high load; the selected :15 minute offsets avoid the top of the hour, but the schedule cannot promise an exact-to-the-minute update. See [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) and [GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages).

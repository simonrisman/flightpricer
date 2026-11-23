# Flight Pricer

A personal flight-price tracker for round trips from New York (JFK, Newark, LaGuardia) and Boston. It runs on GitHub:

- A scheduled Action scrapes Google Flights once a day.
- Results are committed as JSON, so the history stays in the repo.
- GitHub Pages serves the dashboard.

There is no server and no database. Booking stays manual. The dashboard links to Google Flights and to [Capital One Travel](https://travel.capitalone.com/). It does not log in or book anything.

Dates and prices in this repo are public, because GitHub Pages on a free account needs a public repo.

## What the dashboard does

- One card per route, with the cheapest saved fare and a target price.
- Every date shows when a price last came back successfully. The route header shows the newest and oldest of those times.
- Fares at or below the target are called out at the top.
- Each row has the change since the previous saved price, the best price seen, and links out.
- **Look up a date** checks a specific departure and return. If it is already saved, the price and last success show immediately. **Fetch this date** runs that one search on GitHub Actions. Dates outside the window can be kept on the route so later daily runs refresh them.
- **Watched window** edits the dates, weekdays, trip lengths, origins, and target. Save writes `config.yaml` on `main`. The next scrape uses it.

Saving and fetching need a fine-grained personal access token for this repo, with Contents read/write and Actions read/write. Paste it into **GitHub access**. It stays in this browser and is only sent to `api.github.com`.

LaGuardia is included because these trips start from the New York airports. Most of these cities have no LaGuardia service, so those searches often come back empty and use part of the daily request budget. Uncheck LGA on a route if you want that budget spent on JFK, Newark, and Boston.

The daily run is capped (`max_requests_per_run` in `config.yaml`, currently 150) so it finishes inside the Action time limit. It rotates through the full list. A date that was not checked today keeps its last successful price and shows that timestamp.

## One-time GitHub setup

1. Settings → Pages → Source: **GitHub Actions**.
2. Settings → Actions → General → Workflow permissions: **Read and write**.
3. Actions → **scrape** → **Run workflow** for the first full pass.
4. Open `https://simonrisman.github.io/flightpricer/`.

The scrape is scheduled for 11:17 UTC (about 6:17am Eastern Standard Time / 7:17am Eastern Daylight Time). A keepalive step re-enables the workflow on every run so GitHub does not disable it after 60 days of inactivity.

## Routes

All of these are round trips from JFK, EWR, LGA, and BOS. Edit the windows in `config.yaml` or on the site.

| Route | Window | Days | Nights | Target |
| --- | --- | --- | --- | --- |
| Bogotá | 2027-03-01 – 2027-03-21 | Thu–Sat | 7–8 | $300 |
| Medellín | 2027-03-01 – 2027-03-21 | Thu–Sat | 7–8 | $330 |
| Lima | 2027-01-11 – 2027-02-28 | Thu–Fri | 7 | $450 |
| Buenos Aires | 2027-04-01 – 2027-05-15 | Thu–Fri | 10 | $700 |
| Paris | 2027-05-17 – 2027-06-25 | Thu–Fri | 8 | $500 |
| Madrid | 2027-04-12 – 2027-05-31 | Thu–Fri | 8 | $450 |
| Barcelona | 2027-05-17 – 2027-06-20 | Thu–Fri | 8 | $500 |
| Tokyo (Haneda and Narita) | 2027-10-20 – 2027-11-30 | Thu–Fri | 12 | $800 |
| Ho Chi Minh City | 2027-02-15 – 2027-03-31 | Thu–Fri | 12 | $850 |
| Hanoi | 2027-10-04 – 2027-11-26 | Thu–Fri | 12 | $850 |
| Bangkok | 2027-01-11 – 2027-02-28 | Thu–Fri | 12 | $800 |
| Singapore | 2027-02-15 – 2027-04-30 | Thu–Fri | 10 | $900 |
| Tbilisi | 2027-05-03 – 2027-06-20 | Thu–Fri | 9 | $700 |
| Ponta Delgada | 2027-06-01 – 2027-07-10 | Thu–Fri | 7 | $450 |
| Funchal | 2027-04-12 – 2027-06-10 | Thu–Fri | 7 | $550 |

## Local use

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
python -m scraper.main --dry-run
python -m scraper.main --limit 3
python -m scraper.export_config
python -m http.server -d site 8000
```

`--dry-run` prints every search and does not touch the network. After a hand edit to `config.yaml`, run `python -m scraper.export_config` and commit `site/data/config.json` so the dashboard matches. The on-site editor writes both files.

Google Flights access is isolated in `scraper/source_google.py` (`fast-flights` 3.1.0). `settings.source: playwright` is only a stub.

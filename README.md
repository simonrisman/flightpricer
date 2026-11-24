# Flight Pricer

A personal flight-price tracker for round trips from New York (JFK and Newark) and Boston. It runs on GitHub:

- A scheduled Action scrapes Google Flights once a day.
- Results are committed as JSON, so the history stays in the repo.
- GitHub Pages serves the dashboard.

There is no server and no database. Booking stays manual. The dashboard links to Google Flights and to [Capital One Travel](https://travel.capitalone.com/). It does not log in or book anything.

Dates and prices in this repo are public, because GitHub Pages on a free account needs a public repo.

## What the dashboard does

- One row per route, with the cheapest saved fare, the dates of that fare, and when it was fetched.
- **Edit** opens the date table, the chart, and the window editor.
- Each saved date shows when a price last came back successfully.
- Each row has the change since the previous saved price, the best price seen, and links out.
- **Look up a date** checks a specific departure and return. If it is already saved, the price and last success show immediately. **Fetch this date** runs that one search on GitHub Actions. Dates outside the window can be kept on the route so later daily runs refresh them.
- **Watched window** edits the dates, weekdays, trip lengths, and origins. Save writes `config.yaml` on `main`. The next scrape uses it.

Saving and fetching need a fine-grained personal access token for this repo, with Contents read/write and Actions read/write. Paste it into **GitHub access**. It stays in this browser and is only sent to `api.github.com`.

Searches are split by airport across the day, with a 3–7 second pause between Google requests. JFK runs at 6:17am Eastern, Newark at 12:17pm, and Boston at 6:17pm. Each run checks up to `max_requests_per_run` searches for that airport (`config.yaml`, currently 3,000), which is as many as fit in one GitHub Actions job. One airport is about 6,500 searches, so the rest of that airport is picked up the next time it runs. A date that was not checked keeps its last price.

## One-time GitHub setup

1. Settings → Pages → Source: **GitHub Actions**.
2. Settings → Actions → General → Workflow permissions: **Read and write**.
3. Actions → **scrape** → **Run workflow** for the first full pass.
4. Open `https://simonrisman.github.io/flightpricer/`.

The scrape is scheduled for 11:17 UTC (about 6:17am Eastern Standard Time / 7:17am Eastern Daylight Time). A keepalive step re-enables the workflow on every run so GitHub does not disable it after 60 days of inactivity.

## Routes

All of these are round trips from JFK, EWR, and BOS, except Hanoi, which is JFK and Newark only. Edit the windows in `config.yaml` or on the site.

| Route | Window | Days | Nights |
| --- | --- | --- | --- |
| Bogotá | 2027-03-01 – 2027-03-21 | Every day | 7–15 |
| Medellín | 2027-03-01 – 2027-03-21 | Every day | 7–15 |
| Lima | 2027-01-11 – 2027-02-28 | Every day | 7–15 |
| Buenos Aires | 2027-04-01 – 2027-05-15 | Every day | 7–15 |
| Paris | 2027-05-17 – 2027-06-25 | Every day | 7–15 |
| Madrid | 2027-04-12 – 2027-05-31 | Every day | 7–15 |
| Barcelona | 2027-05-17 – 2027-06-20 | Every day | 7–15 |
| Tokyo (Haneda and Narita) | 2027-10-20 – 2027-11-30 | Every day | 7–15 |
| Ho Chi Minh City | 2027-02-15 – 2027-03-31 | Every day | 7–15 |
| Hanoi | 2027-10-04 – 2027-11-26 | Every day | 7–15 |
| Bangkok | 2027-01-11 – 2027-02-28 | Every day | 7–15 |
| Singapore | 2027-02-15 – 2027-04-30 | Every day | 7–15 |
| Tbilisi | 2027-05-03 – 2027-06-20 | Every day | 7–15 |
| Ponta Delgada | 2027-06-01 – 2027-07-10 | Every day | 7–15 |
| Funchal | 2027-04-12 – 2027-06-10 | Every day | 7–15 |

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

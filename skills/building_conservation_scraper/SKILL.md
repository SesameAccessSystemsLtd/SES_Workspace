---
name: building_conservation_scraper
description: >
  Scrape the Building Conservation directory for company profile contact details,
  including company name, email, telephone, address, and profile URL.
  Use this skill when scraper_agent needs structured output from the Building Conservation directory.
---

# Building Conservation Scraper Skill

**Owned and executed by:** scraper_agent
**Consumed by:** scraper_agent
**Output dataset key:** `building_conservation_profiles`

---

## What this skill does

This skill crawls the Building Conservation directory listing for a given initial letter and
visits each company profile page to extract structured contact details.

It writes results to a CSV file with the following fields:

- `name`
- `email`
- `tel`
- `address`
- `url`

---

## Dependencies

- `requests`
- `beautifulsoup4`

These packages are available in the local venv at:

```
C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe
```

---

## Usage

Run the skill script from the repository root or from the script directory.

```
C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe \
  C:\Users\GilesHugo\SES_Workspace\skills\building_conservation_scraper\scripts\building_conservation_scraper.py \
  --initial A \
  --output building_conservation_detailed_A.csv
```

### Optional arguments

- `--initial` — initial letter on the directory page (default: `A`)
- `--output` — output CSV filename (default: `building_conservation_detailed_A.csv`)
- `--delay` — crawl delay between profile requests in seconds (default: `0.5`)

---

## Notes

This skill is intentionally polite to the remote site by adding a delay between profile requests.
If the directory page structure changes, update the profile extraction logic in
`scripts/building_conservation_scraper.py`.

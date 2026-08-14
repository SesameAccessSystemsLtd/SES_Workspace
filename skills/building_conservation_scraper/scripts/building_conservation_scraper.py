import argparse
import csv
import os
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.buildingconservation.com/directory/"
OUTPUT_DIR = r"C:\Users\GilesHugo\SES_Workspace\data\scraper\Inbound Data"
DEFAULT_OUTPUT_FILENAME = os.path.join(OUTPUT_DIR, "building_conservation_detailed_A.csv")


def get_profile_data(profile_url):
    """Visits a profile page and extracts contact details."""
    try:
        response = requests.get(profile_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        data = {
            "url": profile_url,
            "email": "",
            "tel": "",
            "address": "",
        }

        email_link = soup.select_one('a[href^="mailto:"]')
        if email_link:
            data["email"] = email_link.get("href", "").replace("mailto:", "").strip()

        company_info = soup.find("div", id="companyinfo")
        if company_info:
            captions = company_info.find_all("div", class_="caption")
            entries = company_info.find_all("div", class_="entry")

            for cap, ent in zip(captions, entries):
                label = cap.get_text(separator=" ", strip=True).lower()
                value = ent.get_text(separator=" ", strip=True)

                if "tel" in label:
                    data["tel"] = value
                elif "address" in label:
                    data["address"] = value.replace("\n", ", ").replace("\r", "")

        return data
    except Exception as exc:
        print(f"Error scraping {profile_url}: {exc}")
        return None


def main(initial, output_filename, delay):
    start_url = f"{BASE_URL}allco.php?initial={initial}"
    print(f"Fetching directory from {start_url}...")

    try:
        response = requests.get(start_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        menu_list = soup.find("div", id="menulist") or soup.find("div", id="dirbody")
        if not menu_list:
            print("Could not find directory list.")
            return 1

        links = menu_list.select('a[title="view profile"]')
        print(f"Found {len(links)} company profiles. Starting deep crawl...")

        results = []
        for link in links:
            company_name_elem = link.find("div", class_="titles")
            company_name = company_name_elem.get_text(strip=True) if company_name_elem else ""
            profile_url = link.get("href", "")
            if not profile_url.startswith("http"):
                profile_url = urljoin(BASE_URL, profile_url)

            print(f"Scraping: {company_name}...")
            details = get_profile_data(profile_url)
            if details:
                details["name"] = company_name
                results.append(details)
            time.sleep(delay)

        os.makedirs(os.path.dirname(output_filename), exist_ok=True)
        keys = ["name", "email", "tel", "address", "url"]
        with open(output_filename, "w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=keys)
            writer.writeheader()
            writer.writerows(results)

        print(f"\nSuccess! Saved {len(results)} records to {output_filename}")
        return 0
    except Exception as exc:
        print(f"Main Loop Error: {exc}")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape company profiles from the Building Conservation directory."
    )
    parser.add_argument("--initial", default="A", help="Directory initial letter to scrape")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_FILENAME,
        help="Output CSV filename",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay in seconds between profile requests",
    )
    args = parser.parse_args()
    raise SystemExit(main(args.initial, args.output, args.delay))

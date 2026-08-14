"""
lead_generator.py — Generate structured B2B lead lists for the OpenClaw ecosystem.

Phase 1: sample mode — deterministic synthetic contacts based on input parameters.
Phase 2: apollo mode — real contacts from Apollo.io People Search API.
Phase 3: linkedin mode — contacts from LinkedIn Sales Navigator (see lead_linkedin.py).

Owned by: scraper_agent (SAD)
Consumed by: sesame_marketing (SAM) via DATAMAP.md

Usage:
    python lead_generator.py \\
        --industry construction \\
        --count 50 \\
        --job-roles "Director,Project Manager,Site Manager" \\
        --company-size "11-200" \\
        --region UK \\
        --mode sample

    python lead_generator.py --industry saas --count 20 --mode apollo

Output:
    JSON list of lead dicts printed to stdout.
    Raw JSON also written to the configured output directory.

Exit code 0 on success, 1 on error.
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

EXPORT_DIR = os.environ.get(
    "LEAD_EXPORT_DIR",
    r"C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\updates",
)

# ---------------------------------------------------------------------------
# Sample data dictionaries (Phase 1)
# ---------------------------------------------------------------------------

_INDUSTRY_PAIN_POINTS = {
    "construction": [
        "delayed material deliveries impacting build schedules",
        "difficulty tracking subcontractor compliance",
        "manual BOM management causing procurement errors",
        "poor site-to-office communication on project status",
    ],
    "saas": [
        "high customer churn due to poor onboarding",
        "lack of visibility into product usage metrics",
        "manual reporting slowing down sales cycles",
        "difficulty scaling customer success teams",
    ],
    "healthcare": [
        "patient data fragmented across legacy systems",
        "compliance burden around data privacy regulations",
        "inefficient appointment scheduling workflows",
        "staff retention challenges in competitive market",
    ],
    "real_estate": [
        "slow deal pipeline visibility across multiple offices",
        "manual tenant communication and maintenance tracking",
        "difficulty nurturing long sales cycles",
        "lack of integration between CRM and property portals",
    ],
    "legal": [
        "time-consuming manual document review processes",
        "billing inefficiencies from poor matter tracking",
        "client communication scattered across email threads",
        "difficulty demonstrating ROI to clients",
    ],
    "manufacturing": [
        "production schedule slippage from supplier delays",
        "lack of real-time stock visibility",
        "manual quality control records causing audit risk",
        "difficulty forecasting demand for raw materials",
    ],
    "ecommerce": [
        "cart abandonment due to poor checkout experience",
        "inventory sync issues across multiple sales channels",
        "customer lifetime value declining post-pandemic",
        "returns management costing 20-30% of fulfilment spend",
    ],
    "professional_services": [
        "project scope creep eroding margins",
        "consultant utilisation below target",
        "manual timesheet and invoicing workflows",
        "difficulty demonstrating value to procurement stakeholders",
    ],
}

_COMPANY_PREFIXES = [
    "Apex", "Crest", "Summit", "Pioneer", "Keystone",
    "Benchmark", "Meridian", "Pinnacle", "Vantage", "Sterling",
    "Atlas", "Nexus", "Radius", "Zenith", "Horizon",
    "Fortis", "Sigma", "Delta", "Stratum", "Beacon",
]

_COMPANY_SUFFIXES_BY_INDUSTRY = {
    "construction": ["Construction", "Build", "Contractors", "Developments", "Projects", "Civil"],
    "saas": ["Software", "Technologies", "Digital", "Cloud", "Platforms", "Labs"],
    "healthcare": ["Health", "Medical", "Care", "Clinic", "Diagnostics", "Wellness"],
    "real_estate": ["Property", "Estates", "Developments", "Realty", "Holdings", "Land"],
    "legal": ["Law", "Legal", "Solicitors", "Chambers", "Partners", "Associates"],
    "manufacturing": ["Manufacturing", "Engineering", "Fabrication", "Industries", "Precision", "Works"],
    "ecommerce": ["Commerce", "Retail", "Shop", "Marketplace", "Direct", "Trading"],
    "professional_services": ["Consulting", "Advisory", "Partners", "Group", "Solutions", "Associates"],
}

_FIRST_NAMES = [
    "James", "Sarah", "Michael", "Emma", "David", "Claire", "Robert", "Kate",
    "Thomas", "Laura", "Simon", "Helen", "Andrew", "Rachel", "Mark", "Susan",
    "Paul", "Nicola", "Chris", "Amanda", "Daniel", "Victoria", "Peter", "Ruth",
    "Jonathan", "Charlotte", "Matthew", "Fiona", "Richard", "Diane",
]

_LAST_NAMES = [
    "Smith", "Jones", "Williams", "Taylor", "Brown", "Davies", "Evans", "Wilson",
    "Thomas", "Roberts", "Johnson", "Lewis", "Walker", "Robinson", "Wood", "Thompson",
    "White", "Watson", "Jackson", "Wright", "Green", "Harris", "Cooper", "King",
    "Lee", "Martin", "Clarke", "Scott", "Turner", "Phillips",
]

_COMPANY_SIZES_ORDERED = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001+"]

_REGION_DOMAINS = {
    "UK": ["co.uk", "uk"],
    "US": ["com"],
    "EU": ["com", "eu", "de", "fr", "nl"],
    "APAC": ["com", "com.au", "co.nz", "sg"],
    "GLOBAL": ["com"],
}

_REGION_PHONE_PREFIXES = {
    "UK": "+44",
    "US": "+1",
    "EU": "+49",
    "APAC": "+61",
    "GLOBAL": "+1",
}


def _deterministic_index(seed: str, lst: list) -> int:
    """Return a stable index into lst derived from seed string."""
    return int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(lst)


def _pick(seed: str, lst: list):
    return lst[_deterministic_index(seed, lst)]


def _generate_sample_lead(
    i: int,
    industry: str,
    job_roles: list[str],
    company_size: str,
    region: str,
) -> dict:
    """Generate one deterministic synthetic lead record."""
    industry_norm = industry.lower().replace(" ", "_").replace("-", "_")
    suffixes = _COMPANY_SUFFIXES_BY_INDUSTRY.get(
        industry_norm, _COMPANY_SUFFIXES_BY_INDUSTRY["professional_services"]
    )
    pain_points = _INDUSTRY_PAIN_POINTS.get(
        industry_norm, _INDUSTRY_PAIN_POINTS["professional_services"]
    )

    company_name = f"{_pick(f'prefix_{i}_{industry}', _COMPANY_PREFIXES)} {_pick(f'suffix_{i}_{industry}', suffixes)}"
    first_name = _pick(f"first_{i}_{industry}", _FIRST_NAMES)
    last_name = _pick(f"last_{i}_{industry}", _LAST_NAMES)
    job_role = job_roles[i % len(job_roles)]

    domain_suffixes = _REGION_DOMAINS.get(region.upper(), _REGION_DOMAINS["GLOBAL"])
    domain_suffix = _pick(f"domain_{i}_{region}", domain_suffixes)
    email_local = f"{first_name.lower()}.{last_name.lower()}"
    company_slug = company_name.lower().replace(" ", "").replace("&", "")[:12]
    email = f"{email_local}@{company_slug}.{domain_suffix}"

    phone_prefix = _REGION_PHONE_PREFIXES.get(region.upper(), "+44")
    phone_seed = int(hashlib.md5(f"phone_{i}_{industry}".encode()).hexdigest(), 16)
    phone_number = f"{phone_prefix} {7000 + (phone_seed % 3000)} {100000 + (phone_seed % 899999):06d}"[:17]

    selected_pain = _pick(f"pain_{i}_{industry}", pain_points)

    return {
        "company_name": company_name,
        "contact_name": f"{first_name} {last_name}",
        "job_title": job_role,
        "email": email,
        "phone": phone_number,
        "company_size": company_size,
        "region": region,
        "industry": industry,
        "pain_points": selected_pain,
        "source": "sample",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def generate_leads_sample(
    industry: str,
    count: int,
    job_roles: list[str],
    company_size: str,
    region: str,
) -> list[dict]:
    """Generate a list of synthetic lead records. Deterministic for same inputs."""
    return [
        _generate_sample_lead(i, industry, job_roles, company_size, region)
        for i in range(count)
    ]


def generate_leads_apollo(
    industry: str,
    count: int,
    job_roles: list[str],
    company_size: str,
    region: str,
) -> list[dict]:
    """
    Generate leads using Apollo.io People Search API.
    Requires APOLLO_API_KEY environment variable.
    Phase 2 implementation.
    """
    api_key = os.environ.get("APOLLO_API_KEY")
    if not api_key:
        raise ValueError(
            "APOLLO_API_KEY environment variable is not set. "
            "Add it to ~/.hermes/.env to use Apollo.io mode."
        )

    try:
        import requests  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("requests library is required for Apollo mode. It should be in the venv.")

    # Map company_size string to Apollo num_employees_ranges format
    size_map = {
        "1-10": ["1,10"],
        "11-50": ["11,50"],
        "51-200": ["51,200"],
        "11-200": ["11,50", "51,200"],
        "201-500": ["201,500"],
        "501-1000": ["501,1000"],
        "1001+": ["1001,10000"],
    }
    employee_ranges = size_map.get(company_size, ["11,200"])

    url = "https://api.apollo.io/api/v1/mixed_people/search"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }
    payload = {
        "page": 1,
        "per_page": min(count, 100),
        "person_titles": job_roles,
        "num_employees_ranges": employee_ranges,
        "person_locations": [region],
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()

    remaining = resp.headers.get("x-rate-limit-remaining", "unknown")
    log.info("Apollo API call complete. Rate limit remaining: %s", remaining)

    people = resp.json().get("people", [])
    leads = []
    for person in people[:count]:
        org = person.get("organization", {}) or {}
        leads.append({
            "company_name": org.get("name", ""),
            "contact_name": person.get("name", ""),
            "job_title": person.get("title", ""),
            "email": person.get("email", ""),
            "phone": person.get("phone_numbers", [{}])[0].get("raw_number", "") if person.get("phone_numbers") else "",
            "company_size": org.get("employee_count", company_size),
            "region": region,
            "industry": industry,
            "pain_points": "",
            "source": "apollo",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
    return leads


def generate_leads(
    industry: str,
    count: int,
    job_roles: list[str],
    company_size: str,
    region: str,
    mode: str = "sample",
) -> list[dict]:
    """
    Generate a list of lead dicts.

    Args:
        industry:     Target industry (e.g. 'construction', 'saas')
        count:        Number of leads to generate
        job_roles:    List of job titles to target (e.g. ['Director', 'Project Manager'])
        company_size: Company size band (e.g. '11-200', '51-500')
        region:       Geographic region (e.g. 'UK', 'US', 'EU')
        mode:         'sample' | 'apollo' | 'linkedin'

    Returns:
        List of lead dicts with keys: company_name, contact_name, job_title,
        email, phone, company_size, region, industry, pain_points, source, generated_at
    """
    if mode == "sample":
        return generate_leads_sample(industry, count, job_roles, company_size, region)
    elif mode == "apollo":
        return generate_leads_apollo(industry, count, job_roles, company_size, region)
    elif mode == "linkedin":
        raise NotImplementedError(
            "LinkedIn mode is not yet implemented. Use lead_linkedin.py (Phase 3)."
        )
    else:
        raise ValueError(f"Unknown mode '{mode}'. Choose from: sample, apollo, linkedin")


def _write_raw_output(leads: list[dict], output_dir: str) -> str:
    """Write raw JSON to the export directory. Returns the file path written."""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    archive_path = os.path.join(output_dir, f"leads_{ts}.json")
    latest_path = os.path.join(output_dir, "leads_latest.json")

    payload = json.dumps(leads, indent=2, ensure_ascii=False)
    for path in (archive_path, latest_path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)

    log.info("Raw JSON written to: %s", latest_path)
    return latest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate B2B leads for the OpenClaw ecosystem.")
    parser.add_argument("--industry", required=True, help="Target industry (e.g. construction, saas)")
    parser.add_argument("--count", type=int, default=25, help="Number of leads to generate")
    parser.add_argument("--job-roles", default="Director,Manager", help="Comma-separated job titles")
    parser.add_argument("--company-size", default="11-200", help="Company size band (e.g. 11-200)")
    parser.add_argument("--region", default="UK", help="Target region (e.g. UK, US, EU)")
    parser.add_argument(
        "--mode",
        default="sample",
        choices=["sample", "apollo", "linkedin"],
        help="Lead source mode",
    )
    parser.add_argument(
        "--output-dir",
        default=EXPORT_DIR,
        help="Directory for raw JSON output",
    )
    args = parser.parse_args()

    job_roles = [r.strip() for r in args.job_roles.split(",") if r.strip()]

    try:
        leads = generate_leads(
            industry=args.industry,
            count=args.count,
            job_roles=job_roles,
            company_size=args.company_size,
            region=args.region,
            mode=args.mode,
        )
    except (ValueError, NotImplementedError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        log.error("Unexpected error generating leads: %s", exc)
        sys.exit(1)

    _write_raw_output(leads, args.output_dir)
    log.info("Generated %d leads (mode=%s, industry=%s)", len(leads), args.mode, args.industry)

    # Print JSON to stdout for caller capture
    print(json.dumps(leads, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

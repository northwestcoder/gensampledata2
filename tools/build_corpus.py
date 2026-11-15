"""Index every original support file without changing, truncating or deduplicating it."""

import csv
import hashlib
import io
import json
from pathlib import Path

USES = {
    "birthdates": "Customer birthdates",
    "cities": "City mutations",
    "common_given_names": "Given-name mutations and explicit field pools",
    "companies": "Customer employers",
    "domains": "Customer email domains and email mutations",
    "emailcampaigns": "Email event types and clickstream campaigns",
    "firstnames_female": "Customer first names",
    "firstnames_male": "Customer first names",
    "given_name_alternatives": "Nickname transformations; complete rows also available",
    "injuries": "Safety report descriptions",
    "ip_addresses": "Mobile and Wi-Fi client addresses",
    "ipv6": "Clickstream client addresses (original file name retained)",
    "jobs": "Customer job titles",
    "last1hourdates": "Mobile and Wi-Fi activity timestamps",
    "last24hoursdates": "Clickstream and daily transaction timestamps",
    "lastfiveyeardates": "Full-history transaction and event timestamps",
    "lastnames": "Customer surnames",
    "latlongs": "Location report coordinates",
    "lmscreatedates": "Loyalty account creation dates",
    "loyaltyprograms": "Loyalty program names",
    "loyaltytiers": "Loyalty tiers",
    "macaddr": "Explicit device/address field pools",
    "phones": "Customer phone numbers",
    "postalcodes": "Customer postal codes and postal mutations",
    "prefix_female": "Customer name prefixes",
    "prefix_male": "Customer name prefixes",
    "randowords": "Customer email local parts",
    "sha256": "Mobile and Wi-Fi hashes",
    "states": "State mutations",
    "streetnames": "Customer street addresses and address mutations",
    "streetnames2": "Secondary address/unit fragments",
    "tld": "Generated email domain suffixes",
    "uris": "Clickstream and mobile page/referrer values",
    "us_cities_states_counties": "Complete structured city/state/county records",
    "user_agent": "Clickstream user agents",
    "uuid4": "Shared synthetic device identifiers",
}


def build_catalog(directory: Path) -> dict:
    datasets = {}
    for path in sorted(directory.glob("*.csv")):
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        parser = "lines"
        entries = len(text.splitlines())
        if path.stem == "common_given_names":
            parser = "csv_cells"
            entries = sum(len(row) for row in csv.reader(io.StringIO(text)))
        elif path.stem in ("given_name_alternatives", "us_cities_states_counties"):
            parser = "csv_rows"
            entries = sum(1 for _ in csv.reader(io.StringIO(text)))
        datasets[path.name] = {
            "parser": parser,
            "entries": entries,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "usage": USES.get(path.stem, "Explicit field pools"),
        }
    if not datasets:
        raise ValueError("No original support CSV files found")
    return {
        "format_version": 2,
        "description": "Complete original support files, preserved byte for byte. Duplicates and input ordering are retained.",
        "datasets": datasets,
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    catalog = build_catalog(root / "support")
    target = root / "src/sampledata/data/corpus.json"
    target.write_text(json.dumps(catalog, indent=2) + "\n")
    print(f"Indexed {len(catalog['datasets'])} complete support files in {target}")

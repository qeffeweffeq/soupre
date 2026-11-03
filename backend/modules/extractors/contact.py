import os
import csv
from bs4 import BeautifulSoup
from typing import Optional
from modules.extractors.basic_info import (
    extract_location,
    extract_email,
    extract_phone,
    extract_instagram,
    extract_linkedin,
    extract_founded,
)

SHARED_CONTACT_KEYS = [
    "Icon URL",
    "Location",
    "Email",
    "Phone",
    "Instagram",
    "LinkedIn",
    "Founded",
]


def read_job_contact_csv(job_dir: str) -> dict[str, str]:
    """Read key-value contact metadata from contact.csv in the job directory."""
    contact_csv_path = os.path.join(job_dir, "contact.csv")
    data: dict[str, str] = {}
    if os.path.exists(contact_csv_path):
        try:
            with open(contact_csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                for idx, row in enumerate(reader):
                    if idx == 0 and row == ["Key", "Value"]:
                        continue
                    if len(row) >= 2 and row[0].strip():
                        data[row[0].strip()] = row[1].strip()
        except Exception:
            pass
    return data


def update_job_contact_csv(job_dir: str, soup: BeautifulSoup, icon_url: Optional[str] = None) -> dict[str, str]:
    """Extract contact information and persist to contact.csv in the job directory."""
    os.makedirs(job_dir, exist_ok=True)
    contact_data = read_job_contact_csv(job_dir)

    candidates = {
        "Icon URL": icon_url,
        "Location": extract_location(soup),
        "Email": extract_email(soup),
        "Phone": extract_phone(soup),
        "Instagram": extract_instagram(soup),
        "LinkedIn": extract_linkedin(soup),
        "Founded": extract_founded(soup),
    }

    updated = False
    for k in SHARED_CONTACT_KEYS:
        val = candidates.get(k)
        if val is None or not str(val).strip():
            continue
        val_str = str(val).strip()
        existing_val = contact_data.get(k, "")
        # Allow upgrading Google Favicon fallback to a real site icon
        if not existing_val or (k == "Icon URL" and "google.com/s2/favicons" in existing_val and "google.com/s2/favicons" not in val_str):
            contact_data[k] = val_str
            updated = True

    contact_csv_path = os.path.join(job_dir, "contact.csv")
    if updated or not os.path.exists(contact_csv_path):
        with open(contact_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Key", "Value"])
            for k in SHARED_CONTACT_KEYS:
                if k in contact_data and contact_data[k]:
                    writer.writerow([k, contact_data[k]])

    return contact_data


# Backward-compatible aliases
_read_job_contact_csv = read_job_contact_csv
_update_job_contact_csv = update_job_contact_csv

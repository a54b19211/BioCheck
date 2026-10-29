"""Read the existing Fabric history interface; do not crawl or write its database."""
import datetime
import html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path


def source_text(record):
    if record["record_kind"] == "trial":
        return record["raw_content"], "raw_content"
    text = html.unescape(re.sub(r"<[^>]+>", " ", record["clean_text"]))
    return re.sub(r"\s+", " ", text).strip(), "clean_text_without_html"


def evidence(record, identifier):
    text, basis = source_text(record)
    return {"id": identifier, "source_id": record["source_id"],
            "record_id": record["source_record_id"], "version": record["version_id"],
            "title": record["title"], "url": record.get("source_url"),
            "text": text, "text_basis": basis, "facts": record.get("facts", {}),
            "published_at": record.get("source_published_at"),
            "received_at": record.get("received_at")}


def fetch(source_id, *, terms=(), record_ids=(), limit=20, config_path=None):
    path = Path(config_path or "/home/zk_test/.config/bioradar/runtime.env")
    config = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            config[key] = value.strip().strip("\"'")
    params = {"source_id": source_id, "is_demo": "false", "mode": "current",
              "cutoff": datetime.datetime.now(datetime.timezone.utc).isoformat(), "limit": limit,
              "terms": list(terms), "source_record_ids": list(record_ids)}
    request = urllib.request.Request(
        config["BIORADAR_DATA_FABRIC_URL"].rstrip("/") + "/api/history/records?"
        + urllib.parse.urlencode(params, doseq=True),
        headers={"Authorization": "Bearer " + config["BIORADAR_DATA_FABRIC_HISTORY_READ_TOKEN"]})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)

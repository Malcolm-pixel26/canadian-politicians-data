"""Build a public-only, static Civic Circle bundle from reviewed collector outputs."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from represent_common import read_all_master_rows  # noqa: E402

CATEGORIES = {"Federal": "federal", "Provincial_Territorial": "provincial_territorial", "Local": "local"}
PUBLIC_FIELDS = ("name", "representative_set", "elected_office", "district_name", "party_name",
                 "province_territory", "email", "phone", "url", "source_url", "last_seen", "status")
PROVINCES = {"", "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}


def public_snapshot(rows):
    """An allowlist prevents future private/internal columns reaching public hosting."""
    if not rows or len(rows) > 25000:
        raise ValueError("Expected 1–25,000 representative records.")
    people = []
    for key, source in rows.items():
        category = CATEGORIES.get(key.split("|", 1)[0])
        if not category:
            raise ValueError("Unrecognized government sheet.")
        person = {field: str(source.get(field) or "") for field in PUBLIC_FIELDS}
        person["status"] = person["status"] or "active"
        if not person["name"].strip() or person["status"] not in ("active", "removed"):
            raise ValueError("Invalid representative name or status.")
        if person["province_territory"] not in PROVINCES:
            raise ValueError("Invalid province or territory.")
        if any(len(value) > 20000 for value in person.values()):
            raise ValueError("Representative field exceeds its size limit.")
        person["id"] = hashlib.sha256(key.encode()).hexdigest()[:32]
        person["category"] = category
        people.append(person)
    people.sort(key=lambda person: person["id"])
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "people": people, "coverage": {
                "categories": dict(Counter(person["category"] for person in people)),
                "note": "Publication time is not source verification time. Source coverage and freshness vary by jurisdiction.",
                "financial_records": "not_included", "voting_history": "not_included"}}


def public_map(data):
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise ValueError("Expected a GeoJSON FeatureCollection.")
    features = data.get("features")
    if not isinstance(features, list) or not 1 <= len(features) <= 5000:
        raise ValueError("Expected 1–5,000 map features.")
    cleaned, point_count = [], 0
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            raise ValueError("Invalid map feature.")
        geometry = feature["geometry"]
        kind, coordinates = geometry.get("type"), geometry.get("coordinates")
        if kind not in ("Polygon", "MultiPolygon"):
            raise ValueError("Only district polygons are supported.")
        polygons = [coordinates] if kind == "Polygon" else coordinates
        if not isinstance(polygons, list) or not polygons:
            raise ValueError("Missing polygon coordinates.")
        for polygon in polygons:
            if not isinstance(polygon, list) or not polygon:
                raise ValueError("Invalid polygon.")
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError("Polygon rings must be closed.")
                for point in ring:
                    point_count += 1
                    if point_count > 1000000:
                        raise ValueError("Map exceeds the point limit.")
                    if not isinstance(point, list) or len(point) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) for n in point):
                        raise ValueError("Invalid map coordinate.")
                    if not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90:
                        raise ValueError("Coordinates must be longitude and latitude.")
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            raise ValueError("Invalid map properties.")
        cleaned.append({"type": "Feature", "geometry": {"type": kind, "coordinates": coordinates},
                        "properties": {key: str(properties.get(key) or "")[:500]
                                       for key in ("district", "province_territory", "representative_set")}})
    return {"type": "FeatureCollection", "features": cleaned}


def build(master, map_path, output, required_sets=()):
    master, output = Path(master), Path(output)
    if not master.is_file():
        raise ValueError("The master workbook does not exist.")
    people = public_snapshot(read_all_master_rows(master))
    available_sets = {person["representative_set"] for person in people["people"] if person["status"] == "active"}
    missing_sets = set(required_sets) - available_sets
    if missing_sets:
        raise ValueError("Missing required source groups: " + ", ".join(sorted(missing_sets)))
    boundaries = public_map(json.loads(Path(map_path).read_text(encoding="utf-8"))) if map_path else None
    output.mkdir(parents=True, exist_ok=True)
    (output / "data").mkdir(exist_ok=True)
    for name in ("index.html", "app.js", "style.css"):
        shutil.copyfile(Path(__file__).parent / name, output / name)
    (output / ".nojekyll").write_text("", encoding="utf-8")
    (output / "data" / "people.json").write_text(json.dumps(people, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    map_output = output / "data" / "map.json"
    if boundaries:
        map_output.write_text(json.dumps(boundaries, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    elif map_output.exists():
        map_output.unlink()
    print("Built public site with", len(people["people"]), "profiles and", len(boundaries["features"]) if boundaries else 0, "districts.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", required=True)
    parser.add_argument("--map")
    parser.add_argument("--output", required=True)
    parser.add_argument("--require-set", action="append", default=[])
    args = parser.parse_args()
    build(args.master, args.map, args.output, args.require_set)


if __name__ == "__main__":
    main()


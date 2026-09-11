"""Export a sanitized snapshot of demo users and devices for GitHub."""

import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import app


def export_fixtures(output_dir=PROJECT_ROOT / "fixtures", flask_app=app):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    with flask_app.app_context():
        db = flask_app.get_db()
        users = db.execute("""SELECT user_id,name,role,retired
            FROM users ORDER BY user_id""").fetchall()
        devices = db.execute("""SELECT d.device_number,d.device_type,p.name contract_plan,d.active
            FROM devices d JOIN contract_plans p ON p.id=d.plan_id
            ORDER BY d.device_number""").fetchall()

    files = (
        (output_path / "demo_users.csv", ("user_id", "name", "role", "retired"), users),
        (output_path / "demo_devices.csv", ("device_number", "device_type", "contract_plan", "active"), devices),
    )
    for file_path, headers, rows in files:
        with file_path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(headers)
            writer.writerows(tuple(row) for row in rows)
    return {file_path.name: len(rows) for file_path, _headers, rows in files}


if __name__ == "__main__":
    counts = export_fixtures()
    for name, count in counts.items():
        print(f"{name}: {count} rows")

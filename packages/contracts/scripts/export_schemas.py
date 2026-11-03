"""Write JSON Schema files for every contract model (used to generate dashboard TypeScript types).

Usage: uv run python packages/contracts/scripts/export_schemas.py <output_dir>
"""

import json
import sys
from pathlib import Path

from spotcool_contracts.calendar import ScheduleInformation
from spotcool_contracts.cameras import CameraCatalog
from spotcool_contracts.events import WORLD_PAYLOADS, EventEnvelope
from spotcool_contracts.occupants import ComplaintText
from spotcool_contracts.points import DevicePointMap
from spotcool_contracts.vrf import VrfProfile

MODELS = {
    "EventEnvelope": EventEnvelope,
    "DevicePointMap": DevicePointMap,
    "VrfProfile": VrfProfile,
    "CameraCatalog": CameraCatalog,
    "ScheduleInformation": ScheduleInformation,
    "ComplaintText": ComplaintText,
    **{model.__name__: model for model in WORLD_PAYLOADS.values()},
}


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "schemas")
    out.mkdir(parents=True, exist_ok=True)
    for name, model in sorted(MODELS.items()):
        path = out / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
    print(f"wrote {len(MODELS)} schemas to {out}")


if __name__ == "__main__":
    main()

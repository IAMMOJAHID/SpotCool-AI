# spotcool-contracts

Everything that crosses the line between **Person A's agent** and **Person B's simulated world** is defined here. Neither side imports the other's code, only this package. Swap the world for a real building, or the agent for another agent: as long as both honour these contracts, nothing else changes.

| Contract | File | In plain words |
|---|---|---|
| **BMS point map** | `points/office_bms.yaml` | The 51 BACnet objects of the office's building-management system: zone temperatures, cooling setpoints, CO₂, airflow, occupancy, plant power, weather, the entrance door and the agent heartbeat. |
| **VRF gateway point map** | `points/vrf_gateway.yaml` | The meeting-room air conditioner's BACnet gateway: on/off, setpoint, mode, fan and louver positions. |
| **VRF brand profiles** | `points/vrf_profiles.yaml` | Which louver controls each brand exposes. Mitsubishi has vertical and horizontal, Daikin vertical only, LG swing only. |
| **Priorities** | `priorities.py` | The agent writes at **priority 10** and releases with NULL. Operators at **8** always win. If the agent's heartbeat stops for 120 s, the BMS releases everything the agent wrote. |
| **Camera catalog** | `cameras.py` | `GET /cameras` lists cameras; `GET /cameras/{id}/snapshot.jpg` returns one JPEG (like ONVIF GetSnapshotUri). |
| **Room calendar** | `calendar.py` | Room bookings shaped like Microsoft Graph `getSchedule`. |
| **Occupant complaints** | `occupants.py` | The world's scripted occupants `POST /chat` (text) or `POST /voice` (16 kHz mono WAV) to the agent. |
| **Dashboard events** | `events.py` | One envelope `{v, type, source, t_ms, sim_time_s, data}` for everything the dashboard shows. |
| **BOPTEST catalog snapshot** | `data/boptest_multizone_office_simple_air.json` | Every input and output of the BOPTEST office test case (from BOPTEST master `a29024f`). Tests check the point map against it. |

## Rules

- **Units on the wire are BMS units:** °C, kW, kWh, L/s, %, ppm. The world converts to and from BOPTEST's K, W, m³/s and fractions.
- **The agent writes zone cooling setpoints and occupancy-sensor points.** It never writes plant points.
- **Releasing a setpoint hands control back to the building's own schedule.** The relinquish default is read live from the baseline run.
- **Ground-truth fields** (`ZoneScene.people`, `CameraSceneData`) are for scoring and the dashboard. The agent must not read them.

## Changing a contract

Contracts are owned jointly. To change one:
1. Open a PR that bumps `version` in `pyproject.toml` and `CONTRACTS_VERSION`.
2. Explain the change in the PR description.
3. Get approval from both Person A and Person B.

Additive changes bump the minor version; breaking changes bump the major version.

## Commands

```bash
uv run pytest packages/contracts                                   # validate the contracts
uv run python packages/contracts/scripts/export_schemas.py schemas # JSON Schema for TypeScript types
```

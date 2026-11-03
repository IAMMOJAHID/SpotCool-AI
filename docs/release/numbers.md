# Numbers the video may use

Every figure the narration, the captions or a slide may show, with its source. A number not on this page is not shown. The rule behind our own numbers: quote only a run whose report says `Quotable`, or a stub run labelled as the policy's upper bound with perfect perception (`packages/world/AGENTS.md`, Telemetry: the report's verdict). The sources are `docs/status/HANDOFF.md` sections 3 and 7.

## Published sources

| Figure | What it says | Source |
|---|---|---|
| Up to 17.8 % against 5.9 % | HVAC savings from counting occupants, against detecting presence only | [PNNL-22072](https://www.pnnl.gov/main/publications/external/technical_reports/pnnl-22072.pdf) |
| -27 % | HVAC energy when the cooling setpoint goes from 22.2 to 25 degC | [CBE 2015](https://escholarship.org/uc/item/13s1q2xc) |
| About 6 % per 1 degC | AC electricity saved per degree of setpoint | [BEE](https://www.cseindia.org/a-step-in-the-right-direction-says-cse-of-power-ministry-s-move-to-fix-starting-temperature-of-room-air-conditioners-at-24oc-and-not-lower-to-save-energy-8814) |
| About 55 % | HVAC's share of energy in Indian commercial buildings | [BEE](https://rehva.eu/fileadmin/user_upload/REHVA-ISHRAE_EC_BEE_seminar_S_Kumar_Deepa_.pdf) |
| 56.1 %, 66.1 % on Tuesdays | Office attendance | [Kastle](https://www.kastle.com/safety-wellness/getting-america-back-to-work/) |

Supporting, for the narration's reasoning rather than on screen: PNNL 2023 (why occupancy sensors are rare), BU 2024 (camera counting errs by about 7 %), and ASHRAE 90.1-2019 section 6.5.3.8 (occupied-standby shuts zone airflow off; the clause is verified).

## Our simulation: an upper bound with perfect perception

Each figure below comes from the stub agent, which reads the scenario's script instead of the cameras. On screen it always carries the label "upper bound with perfect perception (stub agent, in simulation)".

| Figure | Run | Source |
|---|---|---|
| **21.14 % HVAC energy saved**: 227.0 against 287.8 kWh | BOPTEST, `hybrid_tuesday`, stub agent, speed 60, 2 Nov 12:11 | `runs/checkpoint-boptest-stub-s60-3`. BOPTEST's own `ener_tot` and the world's integration agree (0.000 %, no fallbacks); the verdict is blocked by the stub agent alone. A speed-0 run with two recovered steps (`runs/wp12-live-drop`) gave the same totals. |
| Comfort where people were: 1.37 against 0.33 K h | the same run | the same report |
| Cost and emissions about -21 %; peak demand unchanged | the same run | BOPTEST's `cost_tot`, `emis_tot` and `pele_tot` in the report |
| -29 % (cooling setpoint 28 degC alone), -81 % (with standby's airflow shut-off) | BOPTEST, every zone empty all day | HANDOFF section 3, "The levers on an all-empty day"; say "in simulation, with the building empty" |

The 21.14 % runs predate WP10's stub, which writes at each step's close, so its writes take effect a step later than some of the earlier stub's did. On the RC plant that lowered the saving from 16.87 to 15.45 %. The live eval (`docs/runbooks/boptest-codespace.md`, step 7) measures the current stub on BOPTEST; once the lead has run it, quote that run's figure in place of 21.14 %.

BOPTEST's own discomfort KPIs (`tdis_tot` 10.30 against 0.15 K h per zone, `idis_tot` 30.5 against 0 ppm h per zone) are never shown bare. BOPTEST's schedule counts every zone as occupied all day, so they include the zones the scenario leaves empty. If they appear at all, they appear with that sentence.

## SpotCool's own figure

**SpotCool's own figure needs Person A's agent**: a paced BOPTEST run (speed 60) of `hybrid_tuesday` with A's agent, whose report says `Quotable`. Until that run exists, the video shows no saving as SpotCool's own.

## Never quote

| Figure | Why |
|---|---|
| 18.2 % | WP11's first stub run, which wrote only the occupancy sensor |
| 27.1 % | A measurement error: power sampled at step ends missed the spikes when standby dampers reopen (fixed in WP6.1) |
| 32.8 % | WP6's example, with a stand-in agent and the same sampling error |
| "Up to 60 %" (the shared root README) | No run of ours supports it. The user raises it with Person A before release. |
| 16.87 %, 15.45 %, or any RC figure | The RC plant is our own reduced model; its cross-check is circular. RC runs test the world, they do not measure SpotCool. |
| `checkpoint-boptest-stub-s60` and `-2` | Both died when the laptop changed Wi-Fi networks mid-run; kept as evidence only |
| VRF power (`IDU-PWR`) | Its constants are illustrative; it never enters a saving |

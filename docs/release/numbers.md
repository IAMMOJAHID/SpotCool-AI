# Numbers the video may use

Every figure the narration, the captions or a slide may show, with its source. A number not on this page is not shown. The rule behind our own numbers: quote only a run whose report says `Quotable`, or a stub run labelled as the policy's upper bound with perfect perception (`packages/world/AGENTS.md`, Telemetry: the report's verdict). The sources are `docs/status/HANDOFF.md` sections 3 and 7.

## Published sources

| Figure | What it says | Source |
|---|---|---|
| 17.8 % against 5.9 % | Whole-building energy saved in a simulated US large office by occupancy-based control of the lights and VAV boxes: with sensors that count the occupants, against sensors that detect presence only. A national average weighted by construction volume, not a maximum, and not a share of HVAC energy; most of the saving is heating | [PNNL-22072](https://www.pnnl.gov/main/publications/external/technical_reports/pnnl-22072.pdf), executive summary and section 3.3 |
| -27 % | HVAC energy when the cooling setpoint goes from 22.2 to 25 degC | [CBE 2015](https://escholarship.org/uc/item/13s1q2xc) |
| About 6 % per 1 degC | AC electricity saved per degree of setpoint | [BEE](https://www.cseindia.org/a-step-in-the-right-direction-says-cse-of-power-ministry-s-move-to-fix-starting-temperature-of-room-air-conditioners-at-24oc-and-not-lower-to-save-energy-8814) |
| About 55 % | HVAC's share of energy in a typical commercial building in India | [BEE](https://rehva.eu/fileadmin/user_upload/REHVA-ISHRAE_EC_BEE_seminar_S_Kumar_Deepa_.pdf), "Building Code and Rating in India", slide "Typical Commercial Building Energy Use" |
| 56.1 %; 66.1 % on the peak day | US office attendance by badge swipes, against February 2020 (= 100 %, which Kastle calls normal peak occupancy, not capacity): the 10-city weekly average, and the peak day, Tuesday 20 October 2026. Not a share of desks. The page updates weekly (read 3 November 2026) | [Kastle](https://www.kastle.com/safety-wellness/getting-america-back-to-work/) |

The PNNL, BEE 55 % and Kastle rows were checked against their sources on 3 November 2026. Check any other row against its source before it goes on screen.

Supporting, for the narration's reasoning rather than on screen: PNNL 2023 (why occupancy sensors are rare), BU 2024 (camera counting errs by about 7 %), and ASHRAE 90.1-2019 section 6.5.3.8 (occupied-standby shuts zone airflow off; the clause is verified).

## Our simulation: an upper bound with perfect perception

Each figure below comes from the stub agent, which reads the scenario's script instead of the cameras. On screen it always carries the label "upper bound with perfect perception (stub agent, in simulation)".

| Figure | Run | Source |
|---|---|---|
| **19.94 % HVAC energy saved**: 230.4 against 287.8 kWh | BOPTEST, `hybrid_tuesday`, the final stub agent, speed 60, 3 Nov 12:29 (the live eval) | `runs/eval-live-hybrid-20261103-122941`. BOPTEST's own `ener_tot` and the world's integration agree (0.000 %, no fallbacks); every stub write was taken a step after its decision; the verdict is blocked by the stub agent alone. |
| Comfort where people were: 2.13 against 0.33 K h | the same run | the same report |
| Cost about -20 %, emissions about -20 %; peak demand unchanged | the same run | BOPTEST's `cost_tot` (0.0130 against 0.0163), `emis_tot` (0.0473 against 0.0590) and `pele_tot` (0.0183 against 0.0184) |
| -29 % (cooling setpoint 28 degC alone), -81 % (with standby's airflow shut-off) | BOPTEST, every zone empty all day | HANDOFF section 3, "The levers on an all-empty day"; say "in simulation, with the building empty" |

**Superseded, never quote:** 21.14 % (the 2 Nov checkpoint and `wp12-live-drop`). It was measured with the earlier stub, whose writes raced the step. The final stub writes at each step's close, and its run above is the figure.

BOPTEST's own discomfort KPIs (`tdis_tot` 9.85 against 0.15 K h per zone, `idis_tot` 33.3 against 0 ppm h per zone) are never shown bare. BOPTEST's schedule counts every zone as occupied all day, so they include the zones the scenario leaves empty. If they appear at all, they appear with that sentence.

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

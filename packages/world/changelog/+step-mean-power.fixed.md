Energy and energy saved are measured over the whole of each step. The seven ElectricPower meters report their mean over the step that just ended, and every other value stays the one at the end of the step:

- `BoptestPlant` averages BOPTEST's stored 30-second results over the step: `BoptestClient.results` (`PUT /results`) and the time-weighted trapezoid (`step_means`). If that call fails, the step keeps its meters' values at the end of the step, with a warning and a count (`power_fallbacks`); the run goes on.
- `RcPlant` divides each meter's energy over its 60-second sub-steps by the step.
- `EnergyMeter` adds each step's mean power times the step, from the first step; an initial state's power is never counted.

Telemetry no longer misses power that spikes inside a step, such as a VAV box opening wide when occupied-standby reopens its damper; sampled only at the ends of the steps, it overstated SpotCool's saving. At the end of a run, `KpiProcessor` cross-checks each run's energy against its plant's own (`ener_tot` x 1662.66 m2) and warns when they differ by more than 2 %. The report shows both, their difference, and whether its energy saved passed the cross-check and may be quoted. Its power chart draws each step's mean over that step.

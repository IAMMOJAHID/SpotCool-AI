---
paths:
  - "src/digital_twin/**"
  - "frontend/**"
---

# AC Digital Twin Rules

- **Mechanical Slew Rate:** Never jump actuator positions instantaneously. Stepper motors must advance toward target angles at $15^\circ/\text{sec}$ ($\pm 0.75^\circ$ per 50ms tick).
- **Audio Feedback:** Fire a 120ms $2400\text{ Hz}$ sine wave tone via Web Audio API whenever an incoming remote command changes temperature or mode, emulating a real split-AC piezo buzzer.
- **Dynamic Power Computation:**
  $$P_{\text{AC}} = P_{\text{fan}}(\text{RPM}) + \mathbb{I}_{\text{cool}} \cdot P_{\text{base}} \cdot \max\left(0.15, \frac{T_{\text{ambient}} - T_{\text{setpoint}}}{10}\right) \cdot \eta_{\text{spot}}$$
  Where $\eta_{\text{spot}} = 0.65$ in spot cooling mode vs. $\eta_{\text{uniform}} = 1.0$ for baseline.
- **Airflow Visualization:** Render animated streamlines whose velocity binds to `current_fan_rpm` and color transitions from deep cyan (18°C) to soft blue (23°C) to amber (Eco mode).


---
paths:
  - "src/perception/**"
  - "src/vision/**"
---

# Perception Pipeline Rules

- **SAM 3 Mask-Only Depth:** Always isolate depth pixels using the binary mask of the segmented person. Take the `np.median()` of masked pixels to determine distance $Z$. Never take the average of the 2D bounding box, as walls skew the calculation.
- **Pinhole Back-Projection:** Use camera intrinsics $(f_x, f_y, c_x, c_y)$ to map pixel $(u, v)$ and depth $Z$ to real-world meters:
  $$X = \frac{(u - c_x) \cdot Z}{f_x}, \quad Y = \frac{(v - c_y) \cdot Z}{f_y}$$
- **Louver Angle Geometry:**
  $$\theta_{\text{pan}} = \arctan2(X, Z) \cdot \frac{180}{\pi}, \quad \theta_{\text{tilt}} = \arctan2(Y, Z) \cdot \frac{180}{\pi}$$
- **Loop Separation:** Never block the 10 Hz video frame loop on network API calls. Dispatch Llama Vision (clothing Clo analysis) and Whisper/audio tasks to background workers at 0.2 Hz.


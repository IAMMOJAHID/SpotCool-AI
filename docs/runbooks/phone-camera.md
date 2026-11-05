# The live phone camera

An Android phone running IP Webcam films the real meeting room for the VRF segment. The world serves it as the camera `cam-meeting-live`, and SpotCool's agent aims the VRF's louvers at the person it sees. Details: [AGENTS.md, Windows notes, The live phone camera](../../packages/world/AGENTS.md#windows-notes).

1. **Install** "IP Webcam" (by Pavel Khlebovich) from Google Play. In its settings, set Video preferences > Video resolution to 1280x720.
2. **One network.** Turn on the phone's hotspot and join it from this PC, or join both to the same Wi-Fi. The agent's PC joins that network too. Keep the PC on it for the whole take ([boptest-codespace.md](boptest-codespace.md), step 1).
3. **Start the server.** Plug the phone into a charger, then tap "Start server" at the bottom of the app's settings. Read the address on the phone's screen, such as `http://192.168.43.1:8080`. It changes with each hotspot, so read it every time.
4. **Check it** on this PC: open `http://<phone address>/shot.jpg` in a browser. It shows one photo.
5. **Check the catalog:**

   ```bash
   uv run python packages/world/examples/foundational/07_camera_catalog.py --live-url http://<phone address>/shot.jpg
   ```

   The catalog lists `cam-meeting-live`.
6. **Give the world the URL.** Set it before each run that films the phone:

   ```bash
   export SPOTCOOL_WORLD_LIVE_CAMERA_URL=http://<phone address>/shot.jpg
   ```

7. **The firewall** for the agent's PC and the dashboard: [windows-firewall.md](windows-firewall.md). The phone itself needs no rule.

If the phone drops out mid-take, the snapshot keeps the last frame, is marked stale (`X-Camera-Stale: 1`) after 5 s, and the console warns once. Nothing is stored: frames stay in memory.

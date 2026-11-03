# YABE: the BMS, the VRF, and an operator override

[YABE](https://sourceforge.net/projects/yetanotherbacnetexplorer/) is a free BACnet explorer for Windows. It shows the world's two BACnet devices as a building engineer would see them. Full walk-throughs: [AGENTS.md, Checking the BMS with YABE](../../packages/world/AGENTS.md#checking-the-bms-with-yabe-manual) and [Checking the VRF with YABE](../../packages/world/AGENTS.md#checking-the-vrf-with-yabe-manual).

## Connect

1. **Start the world on loopback, with the stub agent.** YABE keeps a shared socket on UDP 47808 that can swallow messages for a device on the same PC, so the BMS moves to 47809. The VRF stays on 47810:

   ```bash
   SPOTCOOL_WORLD_BMS_ADDRESS=127.0.0.1 SPOTCOOL_WORLD_BMS_PORT=47809 SPOTCOOL_WORLD_HTTP_HOST=127.0.0.1 \
     uv run spotcool-world run --plant rc --speed 60 --stub-agent
   ```

   It prints `BMS 127.0.0.1:47809 (BACnet/IP)` and `VRF 127.0.0.1:47810 (BACnet/IP)`. At speed 60 a 15-minute step takes 15 s.
2. **Add a channel.** In YABE: Functions > Add BACnet Channel. Choose "BACnet/IP V4 & V6 over Udp", Port 47808, Local endpoint `127.0.0.1` (type it in), then Start.
3. **Add both devices.** Functions > IP Services > Add Remote Bacnet/IP node, once with `1001 - 127.0.0.1:47809` (the BMS) and once with `2001 - 127.0.0.1:47810` (the VRF). The Devices tree lists both.
4. **Check them.** Click the BMS: its Objects tree lists 52 entries, the 51 points and the device. Click the VRF: 11 entries. From another PC, use this PC's address instead of `127.0.0.1`, the default ports, and [the firewall rules](windows-firewall.md).

## The priority-array demo

Follow [AGENTS.md, Checking the BMS with YABE, step 4](../../packages/world/AGENTS.md#checking-the-bms-with-yabe-manual) on `ZN-COR-CLG-SP`. In short: a write at 8 beats one at 10, a NULL releases only its own slot, and priorities 1-7 are refused. For the VRF's louvers, follow steps 4 and 5 of [Checking the VRF](../../packages/world/AGENTS.md#checking-the-vrf-with-yabe-manual).

## The operator override for the video

The stub agent reads each point's priority array before it writes. When an operator holds the point at 8, the stub backs off and says so on the dashboard.

1. Start the world as in step 1, and open the dashboard at `http://127.0.0.1:8090/?mode=video`.
2. Before 08:30 sim time (the first 6 steps, about 90 s at speed 60), press Ctrl+Alt+8 in YABE. It beeps, and its Log says "WritePriority change to level 8". On the BMS's `ANALOG_VALUE:201` (`ZN-COR-CLG-SP`), write Present Value 23. Priority Array holds 23 at 8 over the stub's 28 at 10.
3. At 08:30 people arrive in Core, and the stub wants to release its setpoint. Instead, the agent panel shows:

   > Core: ZN-COR-CLG-SP is held at 23.0 by an operator at priority 8 -> backed off from releasing it; trying again each step

   The stub says it once, and it never touches slot 8.
4. Release the override: still at priority 8, clear the Present Value field and press Enter (YABE writes NULL). At the close of the next step, the stub releases its own slot 10, and Core is back on the building's schedule.
5. Stop the world with Ctrl+C.

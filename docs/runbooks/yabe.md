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

SpotCool's agent (`packages/agent`) reads a point's priority array before it writes a value. When an operator holds the point at 8, it backs off and says so on the dashboard. It does release its own slot 10 under a hold: a NULL at 10 can never override an operator's 8. So the back-off shows when the agent wants to **write**, which for Core is 18:00, when Core empties. This flow was rehearsed on Nov 4 with a scripted operator.

1. **The agent first, on loopback** (its own window):

   ```bash
   uv run spotcool-agent run --world-host 127.0.0.1 --bms-port 47809 --vision meta \
     --env-file C:/Users/hussa/dev/secrets/spotcool-agent.env
   ```

2. **Then the world, on loopback and without the stub** (another window), and open the dashboard at `http://127.0.0.1:8090/?mode=video`:

   ```bash
   SPOTCOOL_WORLD_BMS_ADDRESS=127.0.0.1 SPOTCOOL_WORLD_BMS_PORT=47809 SPOTCOOL_WORLD_HTTP_HOST=127.0.0.1 \
     uv run spotcool-world run --plant rc --speed 60
   ```

   Connect YABE as in steps 2 and 3 of "Connect".
3. **Hold Core between 08:30 and 17:45 sim time.** From 08:30 people are in Core, and the agent has released its slot 10.
   - Press Ctrl+Alt+8 in YABE. It beeps, and its Log says "WritePriority change to level 8".
   - On the BMS's `ANALOG_VALUE:201` (`ZN-COR-CLG-SP`), write Present Value 23.

   The Priority Array holds 23 at 8 and nothing at 10.
4. **At 18:00 Core empties.** At the 18:15 step, about 15 s later at speed 60, the agent wants 28 degC. The agent panel shows:

   > Core: ZN-COR-CLG-SP is held at 23.0 by an operator at priority 8 -> backed off from writing 28.0; trying again each step

   It says this once, and it never touches slot 8. **Start the video take now** (`take.py yabe_override`): the message stays on the panel.
5. **Release the override** (the take's last cue). Still at priority 8, clear the Present Value field and press Enter; YABE writes NULL. At the next step, the agent writes 28 at priority 10.
6. **Stop:** Ctrl+C in the agent's window first, so it releases its slots while the BMS is up, then in the world's.

The stub (`--stub-agent` on the world, no agent process) behaves the same way, except that it backs off from releases too. With the stub, the hold must be written before 08:30.

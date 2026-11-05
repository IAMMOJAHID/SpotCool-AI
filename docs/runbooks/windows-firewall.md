# Windows firewall

Windows blocks other PCs from reaching the world. SpotCool's agent on another PC needs the BMS and the VRF over BACnet/IP (UDP 47808 and 47810), and the cameras, calendar and dashboard over HTTP (TCP 8081 and 8090). Loopback traffic is never filtered, and the phone needs no rule, because the world polls it. Details: [AGENTS.md, Windows notes](../../packages/world/AGENTS.md#windows-notes).

1. Open PowerShell as administrator (Start, type `PowerShell`, Run as administrator).
2. Allow BACnet/IP, UDP 47808 to 47810 (the BMS on 47808, the VRF gateway on 47810):

   ```powershell
   New-NetFirewallRule -DisplayName "SpotCool BACnet/IP" -Direction Inbound -Protocol UDP -LocalPort 47808-47810 -Action Allow -Profile Domain,Private,Public
   ```

3. Allow HTTP, TCP 8081 (cameras and calendar) and 8090 (the dashboard):

   ```powershell
   New-NetFirewallRule -DisplayName "SpotCool HTTP" -Direction Inbound -Protocol TCP -LocalPort 8081,8090 -Action Allow -Profile Domain,Private,Public
   ```

   `Public` is in both rules because Windows usually calls a phone's hotspot a public network. Drop it if the demo network is set to Private.

4. Check that both rules are there and enabled:

   ```powershell
   Get-NetFirewallRule -DisplayName "SpotCool*" | Format-Table DisplayName, Enabled, Profile
   ```

5. After the takes, remove them:

   ```powershell
   Remove-NetFirewallRule -DisplayName "SpotCool BACnet/IP"
   Remove-NetFirewallRule -DisplayName "SpotCool HTTP"
   ```

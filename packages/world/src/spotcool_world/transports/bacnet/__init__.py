"""BACnet/IP transports: the virtual BMS (WP2) and the VRF gateway (WP3).

Import what you need from its module:

- ``bms``: ``BacnetBmsTransport``, the office BMS device (needs the ``bacnet`` extra).
- ``vrf``: ``BacnetVrfGateway``, the meeting-room VRF gateway (needs the ``bacnet`` extra).
- ``server``: the BACnet/IP server code both devices share (needs the ``bacnet`` extra).
- ``priority``: the priority array and the network command policy (pure, no extra needed).
- ``bms_logic``: occupied-standby and the heartbeat watchdog (pure, no extra needed).
"""

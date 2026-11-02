"""The stub agent: a stand-in for Person A's agent, so the world runs without it.

It is a stub, never Person A's agent: its perception is the scenario's ground truth. Import what
you need from its module:

- ``policy``: ``StubPolicy``, what each zone's occupancy sensor holds (pure, no extra needed).
- ``worker``: ``StubAgentWorker``, which writes it to the BMS over BACnet/IP and publishes its
  events (needs the ``bacnet`` extra).
"""

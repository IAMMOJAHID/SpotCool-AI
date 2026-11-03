"""BOPTEST plant providers: the hosted service and a local (Docker or Codespaces) stack.

Import the provider you need from its module; the HTTP modules need the ``boptest`` extra:

- ``client``: ``BoptestClient``, the HTTP API with BOPTEST's quirks handled.
- ``plant``: ``BoptestPlant``, the ``PlantService`` on top of it.
- ``hosted`` and ``local``: the same plant pointed at the public service or a local stack.
- ``catalog``: the test case's input and output catalog (no extra needed).
"""

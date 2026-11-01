"""HTTP transports.

``cameras.py`` has the camera catalog, snapshot and MJPEG routes, and ``calendar.py`` the room
calendar's. ``server.py``'s ``create_http_app`` puts them in one app, which
``workers/http_server.py``'s ``HttpServerWorker`` serves.
"""

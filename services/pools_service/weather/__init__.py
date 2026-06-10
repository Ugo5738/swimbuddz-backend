"""Weather module (lives inside pools_service).

Cached multi-day hourly forecasts for pool locations. Hosted here — rather than
as a standalone service — because pools_service owns pool coordinates, so the
pre-fetch reads the Pool table directly with no cross-service hop. See
docs/design/WEATHER_SERVICE_DESIGN.md (§ "Why it lives in pools_service").
"""

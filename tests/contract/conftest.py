"""
Contract test fixtures.

Contract tests call internal endpoints with service-role auth.
They reuse the per-service clients from integration/conftest.py
but override auth to use service_role.
"""

# Contract tests use the same service client fixtures from integration/conftest.py.
# The service_role_override is already applied in those fixtures.
# No additional fixtures needed here, but this file exists for future needs.

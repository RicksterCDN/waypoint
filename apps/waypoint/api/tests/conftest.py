"""Shared pytest configuration for the Waypoint API test suite.

Tests must be hermetic. ``Settings`` loads a local ``api/.env`` (via ``env_file``) so developers can
configure OneLake and other options for local runs, but that file must never influence tests.
Disable dotenv loading for the whole session so ``Settings()`` reflects only explicit constructor
arguments plus the process environment.
"""

from app.common.settings import Settings

Settings.model_config["env_file"] = None

"""Smartcar V3 adapter. The only package that talks to Smartcar."""

from app.integrations.smartcar.client import DEFAULT_SCOPES, SmartcarClient, get_smartcar_client

__all__ = ["DEFAULT_SCOPES", "SmartcarClient", "get_smartcar_client"]

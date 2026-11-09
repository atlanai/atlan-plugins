# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Official connector and SDK authentication, scoped to the selected workspace."""
import os
import re
import sys

HOST = "DATABRICKS_SERVER_HOSTNAME"
PATH = "DATABRICKS_HTTP_PATH"
TOKEN = "DATABRICKS_TOKEN"
DSN = "METRICS_AGENT_WAREHOUSE_DSN"
_ALLOWED = {"server_hostname", "http_path", "access_token", "auth_type", "catalog", "schema"}
SIGN_INS = ("browser", "azure-browser", "token", "pat", "databricks-oauth", "azure-oauth",
            "oauth-m2m", "unified", "sdk")


class AuthConfigurationError(ValueError):
    """Only messages authored by this module are safe to display."""


def sdk_provider(host, settings, method):
    """Return the connector's factory of refreshing header factories, not a bearer token.

    The strategy checks the SDK's resolved profile host before any credential provider
    can send a credential. Never replace a profile's host with the selected target host.
    """
    def provider():
        from databricks.sdk.core import Config
        from databricks.sdk.credentials_provider import DefaultCredentials

        class ScopedCredentials:
            def __init__(self):
                self.delegate = DefaultCredentials()

            def auth_type(self):
                return self.delegate.auth_type()

            def __call__(self, cfg):
                if str(cfg.host).rstrip('/').lower() != ('https://' + host).lower():
                    raise AuthConfigurationError("Select a Databricks SDK profile or DATABRICKS_HOST matching the selected workspace")
                if cfg.username or cfg.password or cfg.auth_type == 'basic':
                    raise AuthConfigurationError("Databricks SQL does not support username/password authentication")
                if cfg.auth_type == 'external-browser' and not at_terminal():
                    raise AuthConfigurationError("Databricks browser sign-in needs a real terminal")
                return self.delegate(cfg)

        kwargs = {'credentials_strategy': ScopedCredentials()}
        if method == 'oauth-m2m':
            # Explicit environment route, without inheriting a saved profile's secrets.
            kwargs.update(host='https://' + host, config_file=os.devnull,
                          auth_type='oauth-m2m', client_id=os.environ.get('DATABRICKS_CLIENT_ID'),
                          client_secret=os.environ.get('DATABRICKS_CLIENT_SECRET'))
        else:
            if settings.get('profile'):
                kwargs['profile'] = settings['profile']
            if settings.get('sdk_auth_type'):
                kwargs['auth_type'] = settings['sdk_auth_type']
        try:
            config = Config(**kwargs)
        except Exception:
            # Config may include host/profile and credential details in its exceptions.
            raise AuthConfigurationError("Databricks SDK sign-in failed; check the selected workspace, profile and authentication environment") from None

        def headers():
            try:
                return config.authenticate()
            except Exception:
                raise AuthConfigurationError("Databricks SDK credential refresh failed; repeat the selected sign-in") from None
        return headers
    return provider


def at_terminal():
    return sys.stdin.isatty() and sys.stderr.isatty()


def connection_options(settings):
    from metrics_agent.lib.engine import parse_kv

    options = parse_kv(os.environ[DSN]) if os.environ.get(DSN) else {}
    if set(options) - _ALLOWED:
        raise AuthConfigurationError("Databricks DSN contains unsupported settings; use only Databricks connection fields")
    selected_host = settings.get("server_hostname")
    existing_host = options.get("server_hostname") or os.environ.get(HOST) or os.environ.get('DATABRICKS_HOST', '').removeprefix('https://').rstrip('/')
    if (selected_host and existing_host and selected_host != existing_host
            and (options.get("access_token") or os.environ.get(TOKEN))):
        raise AuthConfigurationError("Select matching Databricks connection metadata and credentials for this workspace")
    for key, variable in (("server_hostname", HOST), ("http_path", PATH)):
        # The selected non-secret connection metadata wins over another run's defaults.
        value = settings.get(key) or options.get(key) or os.environ.get(variable)
        if value:
            options[key] = value
    host = options.get("server_hostname", "")
    path = options.get("http_path", "")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*\.(?:cloud\.databricks\.com|azuredatabricks\.net|gcp\.databricks\.com)", host):
        raise AuthConfigurationError("Set a Databricks server hostname without a scheme, path or credentials")
    if not isinstance(path, str) or not re.fullmatch(r"/sql/1\.0/warehouses/[A-Za-z0-9_-]+", path):
        raise AuthConfigurationError("Set the selected SQL warehouse HTTP path: /sql/1.0/warehouses/<id>")
    token = options.get("access_token") or os.environ.get(TOKEN)
    default = 'token' if token else ('oauth-m2m' if os.environ.get('DATABRICKS_CLIENT_ID') and os.environ.get('DATABRICKS_CLIENT_SECRET') else 'browser')
    if settings.get('profile') or os.environ.get('DATABRICKS_CONFIG_PROFILE') or os.environ.get('DATABRICKS_AUTH_TYPE'):
        default = 'unified'
    method = settings.get("sign_in") or options.get("auth_type") or default
    if method in ("token", "pat"):
        if not token:
            raise AuthConfigurationError("Databricks token sign-in needs DATABRICKS_TOKEN in the environment")
        options["access_token"] = token
        options.pop("auth_type", None)
    elif method in ("browser", "databricks-oauth", "azure-browser", "azure-oauth"):
        if not at_terminal():
            raise AuthConfigurationError("Databricks browser sign-in needs a real terminal; use the host TTY or an environment token for unattended runs")
        options.pop("access_token", None)
        options["auth_type"] = "azure-oauth" if method in ('azure-browser', 'azure-oauth') else "databricks-oauth"
    elif method in ('oauth-m2m', 'unified', 'sdk'):
        if method == 'oauth-m2m' and not (os.environ.get('DATABRICKS_CLIENT_ID') and os.environ.get('DATABRICKS_CLIENT_SECRET')):
            raise AuthConfigurationError("Databricks OAuth M2M needs DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET in the environment")
        options.pop('access_token', None)
        options.pop('auth_type', None)
        options['credentials_provider'] = sdk_provider(host, settings, method)
    else:
        raise AuthConfigurationError("Databricks sign_in must be browser, azure-browser, token, oauth-m2m or unified")
    if settings.get('identity_federation_client_id'):
        options['identity_federation_client_id'] = settings['identity_federation_client_id']
    for key in ("catalog", "schema"):
        if settings.get(key):
            options[key] = settings[key]
    return options

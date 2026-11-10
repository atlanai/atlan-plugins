# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Refreshable Power BI credentials; secret values never enter diagnostics."""
import os
import threading
import time


class TokenProvider:
    def __init__(self, args, extractor):
        self.args, self.x = args, extractor
        self.token = None
        self.expires_at = 0
        self.refresh_token = None
        self.credential = None
        self.lock = threading.Lock()

    def _remember(self, payload):
        self.token = payload['access_token']
        self.expires_at = time.time() + int(payload.get('expires_in', 3600))
        self.refresh_token = payload.get('refresh_token') or self.refresh_token

    def _azure(self):
        from azure.identity import CertificateCredential, ManagedIdentityCredential
        if self.credential is None:
            if self.args.auth == 'certificate':
                names = ('PBI_TENANT_ID', 'PBI_CLIENT_ID', 'PBI_CLIENT_CERTIFICATE_PATH')
                if any(not os.environ.get(n) for n in names):
                    raise self.x.AuthError('Certificate sign-in needs ' + ', '.join(names))
                self.credential = CertificateCredential(
                    tenant_id=os.environ[names[0]], client_id=os.environ[names[1]],
                    certificate_path=os.environ[names[2]],
                    password=os.environ.get('PBI_CLIENT_CERTIFICATE_PASSWORD'))
            else:
                self.credential = ManagedIdentityCredential(client_id=os.environ.get('PBI_CLIENT_ID') or None)
        token = self.credential.get_token(self.args.scope)
        self.token, self.expires_at = token.token, token.expires_on

    def __call__(self, *, force=False):
        with self.lock:
            if self.token and not force and time.time() < self.expires_at - 60:
                return self.token
            mode = self.args.auth
            try:
                if mode in ('certificate', 'managed-identity'):
                    self._azure()
                elif mode == 'spn':
                    self.x.token_client_credentials(self.args.scope, on_token=self._remember)
                elif mode == 'token':
                    if force:
                        raise self.x.AuthError('The supplied Power BI token expired; replace PBI_ACCESS_TOKEN')
                    self.token = self.x.acquire_token(self.args)
                    self.expires_at = float(self.x.token_claims(self.token).get('exp') or time.time() + 300)
                elif self.refresh_token:
                    status, payload = self.x._form_post(
                        f'{self.x.LOGIN_HOST}/{self.args.tenant}/oauth2/v2.0/token',
                        {'grant_type': 'refresh_token', 'client_id': self.args.client_id,
                         'refresh_token': self.refresh_token, 'scope': self.args.scope})
                    if status != 200 or 'access_token' not in payload:
                        raise self.x.AuthError('Power BI token renewal was refused; repeat sign-in')
                    self._remember(payload)
                elif force:
                    raise self.x.AuthError('The cached Power BI sign-in cannot renew; repeat sign-in')
                else:
                    self.token = self.x.acquire_token(self.args, on_token=self._remember)
                    if not self.expires_at:
                        self.expires_at = float(self.x.token_claims(self.token).get('exp') or time.time() + 300)
                return self.token
            except self.x.AuthError:
                raise
            except Exception:
                raise self.x.AuthError('Power BI credential acquisition or renewal failed; check the selected identity and credential configuration') from None

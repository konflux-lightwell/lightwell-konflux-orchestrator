"""Thin HTTP transport for Kubernetes API calls using requests."""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests
import yaml


@dataclass(frozen=True)
class KubeAuth:
    """Resolved cluster credentials."""

    server: str
    token: str
    ca_cert: str | None


def resolve_auth(cluster_api: str) -> KubeAuth:
    """Resolve auth credentials from env vars or kubeconfig.

    Priority:
      1. KONFLUX_TOKEN env var + cluster_api arg  (CI mode)
      2. KUBECONFIG / ~/.kube/config              (local dev, OAuth token only)
    """
    if token := os.getenv("KONFLUX_TOKEN"):
        return KubeAuth(server=cluster_api, token=token, ca_cert=None)

    kubeconfig_path = os.getenv("KUBECONFIG", os.path.expanduser("~/.kube/config"))
    with open(kubeconfig_path) as f:
        config = yaml.safe_load(f)

    ctx_name = config["current-context"]
    ctx = next(c["context"] for c in config["contexts"] if c["name"] == ctx_name)

    cluster = next(c["cluster"] for c in config["clusters"] if c["name"] == ctx["cluster"])
    user = next(u["user"] for u in config["users"] if u["name"] == ctx["user"])

    token = user.get("token", "")
    if not token:
        raise RuntimeError(
            f"Kubeconfig user '{ctx['user']}' has no 'token' field. "
            "Only OAuth token auth is supported (run 'oc login' first)."
        )

    return KubeAuth(
        server=cluster["server"],
        token=token,
        ca_cert=cluster.get("certificate-authority"),
    )


class KubeAPI:
    """Low-level HTTP client for Kubernetes API calls."""

    _DEFAULT_TIMEOUT = 30

    def __init__(self, auth: KubeAuth, timeout: int = _DEFAULT_TIMEOUT):
        self._auth = auth
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {auth.token}"
        self._session.headers["Accept"] = "application/json"
        if auth.ca_cert:
            self._session.verify = auth.ca_cert

    def _url(self, api_path: str) -> str:
        return f"{self._auth.server}{api_path}"

    def get(self, api_path: str) -> dict:
        resp = self._session.get(self._url(api_path), timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def list(self, api_path: str, **params) -> dict:
        resp = self._session.get(self._url(api_path), params=params, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def create(self, api_path: str, body: dict) -> dict:
        resp = self._session.post(self._url(api_path), json=body, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

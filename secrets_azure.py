"""Optional Azure Key Vault backend for ${...} secret references.

Keeps API keys OUT of git entirely: instead of storing the key in config.json,
store it as a Key Vault secret and reference it as e.g. ${cta_key}. At runtime
the value is fetched from the vault.

Auth uses azure-identity's DefaultAzureCredential, which "just works" with:
  * `az login` on your Mac (local dev),
  * a service principal via AZURE_CLIENT_ID / AZURE_TENANT_ID /
    AZURE_CLIENT_SECRET env vars (on the Pi, set in the systemd unit),
  * a managed identity (if ever run on an Azure VM).

Everything is best-effort: if the Azure libraries aren't installed, no vault is
configured, or a lookup fails, we return None and the caller falls back to its
other secret sources. Install with:  pip install -r requirements-azure.txt
"""

from __future__ import annotations

_SECRET_CACHE: dict[tuple[str, str], str | None] = {}
_CLIENT_CACHE: dict[str, object] = {}


def _client(vault_url: str):
    if vault_url in _CLIENT_CACHE:
        return _CLIENT_CACHE[vault_url]
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    _CLIENT_CACHE[vault_url] = client
    return client


def get_secret(name: str, vault_url: str) -> str | None:
    """Fetch a secret by ${name} from the vault, cached. None on any failure.

    Key Vault secret names can't contain underscores, so ``cta_key`` maps to
    the vault secret ``cta-key``.
    """
    if not vault_url:
        return None
    cache_key = (vault_url, name)
    if cache_key in _SECRET_CACHE:
        return _SECRET_CACHE[cache_key]

    value: str | None = None
    try:
        client = _client(vault_url)
        value = client.get_secret(name.replace("_", "-")).value
    except Exception as exc:  # noqa: BLE001 - optional dependency / network / auth
        print(f"[keyvault] could not fetch '{name}': {exc}")
    _SECRET_CACHE[cache_key] = value
    return value

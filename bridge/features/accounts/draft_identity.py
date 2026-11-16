"""Opaque, stable namespaces for drafts whose account owner is known."""

import hashlib
import json
from pathlib import Path


def draft_identity(*parts):
    return hashlib.sha256(json.dumps(['draft-v1', *parts], ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _codex_configuration(home, toml_loads):
    if toml_loads is None:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        toml_loads = tomllib.loads
    path = home / 'config.toml'
    config = toml_loads(path.read_text(encoding='utf-8') if path.exists() else '')
    profile = (config.get('profiles') or {}).get(config.get('profile')) or {}
    return {**config, **profile}


def codex_account_identity(home, provider, environment=None, *, shared_auth=False, toml_loads=None):
    """Read the selected home without contacting or activating a Codex runtime."""
    home = Path(home)
    explicit_environment = environment is not None
    environment = environment or {}
    try:
        auth = json.loads((home/'auth.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        auth = {}
    if provider == 'openai':
        endpoint = environment.get('OPENAI_BASE_URL') or 'https://api.openai.com/v1'
        route_known = not shared_auth or explicit_environment
        if shared_auth:
            try:
                effective = _codex_configuration(home, toml_loads)
                definition = (effective.get('model_providers') or {}).get(provider) or {}
                configured_endpoint = definition.get('base_url') or effective.get('openai_base_url')
                endpoint = configured_endpoint or endpoint
                route_known = bool(configured_endpoint) or explicit_environment
                if 'env_key' in definition or 'experimental_bearer_token' in definition:
                    key = (environment.get(definition['env_key']) if definition.get('env_key')
                           else definition.get('experimental_bearer_token'))
                    return (draft_identity('api', provider, endpoint, key)
                            if route_known and isinstance(key, str) and key else '')
            except (OSError, ValueError, TypeError, KeyError, ImportError, AttributeError):
                return ''
        account_id = (auth.get('tokens') or {}).get('account_id')
        if auth.get('auth_mode') != 'apikey' and isinstance(account_id, str) and account_id:
            return (draft_identity('chatgpt', account_id, endpoint) if shared_auth else
                    draft_identity('chatgpt', account_id))
        if auth.get('auth_mode') == 'chatgpt':
            return ''
        key = auth.get('OPENAI_API_KEY') or environment.get('OPENAI_API_KEY')
        return (draft_identity('api', provider, endpoint, key)
                if route_known and isinstance(key, str) and key else '')
    try:
        effective = _codex_configuration(home, toml_loads)
        definition = (effective.get('model_providers') or {}).get(provider) or {}
        key = (environment.get(definition['env_key']) if definition.get('env_key')
               else definition.get('experimental_bearer_token'))
        if not key and ('env_key' in definition or 'experimental_bearer_token' in definition):
            return ''
        if shared_auth and not key and definition.get('requires_openai_auth') is True:
            key = auth.get('OPENAI_API_KEY')
            if not key and not definition.get('base_url'):
                # Native compatibility aliases can use the official ChatGPT
                # login after switching back from an API provider.
                owner = codex_account_identity(home, 'openai', environment)
                return draft_identity('shared-auth', provider, owner) if owner else ''
        endpoint = definition.get('base_url')
        if isinstance(key, str) and key and isinstance(endpoint, str) and endpoint:
            return draft_identity('api', provider, endpoint, key)
    except (OSError, ValueError, TypeError, KeyError, ImportError, AttributeError):
        pass
    return ''

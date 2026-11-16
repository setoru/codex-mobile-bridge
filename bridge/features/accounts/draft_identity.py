"""Opaque, stable namespaces for drafts whose account owner is known."""

import base64
import hashlib
import json
from pathlib import Path


def draft_identity(*parts):
    return hashlib.sha256(json.dumps(['draft-v1', *parts], ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def token_owner(auth):
    tokens = auth.get('tokens') or {}
    try:
        encoded = tokens['id_token'].split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        # These are local native-login records, not independent token validation.
        return [tokens.get('account_id'), claims.get('sub'), claims.get('email')]
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def _codex_toml(text, toml_loads=None):
    if toml_loads is None:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        toml_loads = tomllib.loads
    return toml_loads(text)


def codex_configuration_identity(home, cwd, provider):
    """Bind recovery to every user or project configuration that may apply."""
    try:
        home = Path(home).resolve(strict=True)
        workspace = Path(cwd).resolve(strict=True)
        if not home.is_dir() or not workspace.is_dir() or not isinstance(provider, str) or not provider:
            raise ValueError('无法核验原接入配置，请先核对源会话')
        paths = [home / 'config.toml', *(parent / '.codex' / 'config.toml'
                  for parent in (workspace, *workspace.parents))]
        layers, configurations = [], []
        for path in dict.fromkeys(paths):
            try:
                content = path.read_bytes()
            except FileNotFoundError:
                content = None
            configurations.append(_codex_toml(content.decode('utf-8')) if content is not None else {})
            digest = hashlib.sha256(content).hexdigest() if content is not None else None
            layers.append([str(path), digest])
        profile_name = configurations[0].get('profile')
        for config in reversed(configurations[1:]):
            if 'profile' in config:
                profile_name = config['profile']
        for config in configurations:
            profile = (config.get('profiles') or {}).get(profile_name) or {}
            effective = {**config, **profile}
            definition = (effective.get('model_providers') or {}).get(provider) or {}
            if (any(definition.get(key) for key in
                    ('env_key', 'env_http_headers', 'auth', 'gateway_oauth', 'aws')) or
                    effective.get('api_key_helper') or effective.get('credential_store') or
                    effective.get('cli_auth_credentials_store', 'file') != 'file'):
                raise ValueError('无法核验原接入配置，请先核对源会话')
    except (OSError, TypeError, ValueError, AttributeError, ImportError) as exc:
        raise ValueError('无法核验原接入配置，请先核对源会话') from exc
    return draft_identity('codex-config-v1', str(home), str(workspace), provider, layers)


def _codex_configuration(home, toml_loads):
    path = home / 'config.toml'
    config = _codex_toml(path.read_text(encoding='utf-8') if path.exists() else '', toml_loads)
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
            if not shared_auth:
                return draft_identity('chatgpt', account_id)
            try:
                owner = token_owner(auth)
            except AttributeError:
                return ''
            if not owner or not all(isinstance(part, str) and part for part in owner):
                return ''
            return draft_identity('chatgpt', owner, endpoint)
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
                owner = codex_account_identity(home, 'openai', environment if explicit_environment else None,
                                               shared_auth=True, toml_loads=toml_loads)
                return draft_identity('shared-auth', provider, owner) if owner else ''
        endpoint = definition.get('base_url')
        if isinstance(key, str) and key and isinstance(endpoint, str) and endpoint:
            return draft_identity('api', provider, endpoint, key)
    except (OSError, ValueError, TypeError, KeyError, ImportError, AttributeError):
        pass
    return ''

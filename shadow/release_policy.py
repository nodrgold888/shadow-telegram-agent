"""Trusted automatic-release configuration; no secrets are returned to the model/UI."""
import os

from .persist import load_local_settings

AUTO_LABEL = 'shadow-auto-deploy'
PROTECTED_PATHS = ('.github/', 'scripts/auto-deploy.cjs', 'scripts/auto-deploy.test.cjs', 'shadow/release_policy.py')


def github_token():
    return os.getenv('SHADOW_DEV_GITHUB_TOKEN', '').strip() or load_local_settings().get('SHADOW_DEV_GITHUB_TOKEN', '').strip()


def auto_deploy_enabled():
    return os.getenv('SHADOW_AGENT_AUTO_DEPLOY', 'true').strip().lower() in {'true', '1', 'yes'}


def automatic_paths_allowed(files):
    return not any(f['path'].startswith(PROTECTED_PATHS) for f in files)


def release_readiness():
    enabled, connected = auto_deploy_enabled(), bool(github_token())
    return {'enabled': enabled, 'ready': enabled and connected,
            'note': 'Build → test → merge → Render → health check' if enabled and connected else
                    'Development Studio da GitHub kalitini ulang. Contents va Pull requests: Read and write; Actions va Checks: Read.' if enabled else
                    'SHADOW_AGENT_AUTO_DEPLOY serverda ochirilgan.'}

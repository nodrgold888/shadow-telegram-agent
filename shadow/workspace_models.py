"""A small strong-model shortlist, separate from the full model library."""
from __future__ import annotations

import re

from .free_catalog import catalog_snapshot, free_chat_model_ids, gateway_provider, gateway_models, model_status
from .model_routing import ordered_backup_providers, free_gateway_base_url
from .provider_catalog import XKIRO_MODELS

SHORTLIST_SIZE = 12
_PRIVATE_TOP = {model: name.replace('xKiro ', '') for model, name in XKIRO_MODELS}
_PRIVATE_TOP = {model: ('Claude ' + name if name.startswith(('Sonnet', 'Opus')) else name)
                for model, name in _PRIVATE_TOP.items()}


def _family(row):
    name = re.sub(r'\s*\([^)]*\)', '', row['name']).casefold().strip()
    return re.sub(r'\s+\d{4}$', '', name)


def shortlist(settings, live=None, checked=False):
    """Twelve distinct recommendations, not a promise of twelve connected services."""
    eligible = {p.slot for p in ordered_backup_providers(settings)}
    result, seen = [], set()
    # Keep saved flagship routes while hiding the lower-tier reserve chain.
    for p in settings.backup_providers:
        name = _PRIVATE_TOP.get(p.model)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        enabled = p.slot in eligible
        result.append({'id': f'slot:{p.slot}', 'name': p.name, 'label': name, 'model': p.model,
                       'enabled': enabled, 'reason': '' if enabled else 'Pullik model. Bepul rejimda yopiq.',
                       'status': 'configured' if enabled else 'restricted', 'rank': None, 'context': None})
    direct = next((name for ident, name in _PRIVATE_TOP.items()
                   if ident.split('/')[-1] == settings.openai_model), None)
    if settings.openai_api_key and direct and direct.casefold() not in seen:
        seen.add(direct.casefold())
        enabled = settings.ai_work_mode != 'free'
        result.append({'id': 'openai', 'name': 'OpenAI', 'label': direct, 'model': settings.openai_model,
                       'enabled': enabled, 'reason': '' if enabled else 'Pullik OpenAI API. Bepul rejimda yopiq.',
                       'status': 'configured' if enabled else 'restricted', 'rank': None, 'context': None})
    live = live or {}
    gateway = gateway_provider(settings)
    candidates = sorted((r for r in catalog_snapshot()['models'] if r['kind'] == 'chat' and r['enabled']),
                        key=lambda r: (r['rank'] or 10000,
                                       model_status(live.get(r['id'])) not in {'ready', 'connected'}, r['name'].casefold()))
    for row in candidates:
        family = _family(row)
        if family in seen:
            continue
        seen.add(family)
        status = model_status(live.get(row['id'])) if checked else 'unknown'
        saved = next((p for p in settings.backup_providers if p.model == row['id'] and
                      (not gateway or p.base_url != gateway.base_url)), None)
        if saved:
            enabled = saved.slot in eligible
            ident, name = f'slot:{saved.slot}', saved.name
            status = 'configured' if enabled else 'restricted'
            reason = '' if enabled else 'Bepul rejimda yopiq: pullik yoki tasdiqlanmagan model.'
        else:
            enabled = bool(gateway and status in {'ready', 'connected'} and gateway.slot in eligible)
            ident, name = 'catalog:' + row['id'], row['provider']
            reason = ('' if enabled else 'Avval shu akkauntga FreeLLMAPI unified kalitini ulang.' if not gateway else
                      'Model kvotasi tugagan. Boshqa top modelni tanlang.' if status == 'exhausted' else
                      'FreeLLMAPI da shu model provayderining kalitini ulang.' if status in {'needsKey', 'not_listed'} else
                      'Model gatewayda ochirilgan.' if status == 'disabled' else
                      'Gateway holati tekshirilmadi. Yangilash yoki AI tekshirishni bosing.')
        result.append({'id': ident, 'name': name, 'label': re.sub(r'\s*\([^)]*\)', '', row['name']).strip(),
                       'model': row['id'], 'enabled': enabled, 'reason': reason, 'status': status,
                       'rank': row['rank'], 'context': row['context'], 'gateway_model': row['id'] if not saved else None})
        if len(result) >= SHORTLIST_SIZE:
            break
    for i, item in enumerate(result, 1):
        item['position'] = i
    return result[:SHORTLIST_SIZE]


async def account_shortlist(settings):
    saved = gateway_provider(settings)
    live, checked, notice = {}, False, ''
    if saved:
        try:
            live = {row['id']: row for row in await gateway_models(saved.base_url, saved.api_key)}
            checked = True
        except ValueError as exc:
            notice = str(exc)
    return {'models': shortlist(settings, live, checked), 'gateway_checked': checked,
            'gateway_notice': notice, 'gateway_base_url': free_gateway_base_url()}


def catalog_choice(settings, model):
    if not model.startswith('catalog:'):
        return None
    target = model[len('catalog:'):]
    if target not in free_chat_model_ids():
        raise ValueError('Faqat tasdiqlangan chat modelini tanlang.')
    saved = gateway_provider(settings)
    if saved is None or saved.slot not in {p.slot for p in ordered_backup_providers(settings)}:
        raise ValueError('Avval shu akkauntga FreeLLMAPI unified API kalitini ulang.')
    return saved, target


async def check_catalog_choice(settings, model):
    choice = catalog_choice(settings, model)
    if choice is None:
        return
    provider, target = choice
    rows = await gateway_models(provider.base_url, provider.api_key)
    if model_status(next((row for row in rows if row['id'] == target), None)) not in {'ready', 'connected'}:
        raise ValueError('Bu model hozir ulangan emas yoki kvotasi tugagan. Boshqa top modelni tanlang.')

"""Public anime metadata/playback and an account-isolated personal watch library."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

ORIGIN = 'https://anilibria.top'
API = ORIGIN + '/api/v1/'
SORTS = {'fresh': 'FRESH_AT_DESC', 'rating': 'RATING_DESC', 'year': 'YEAR_DESC'}


class AnimeUnavailable(ValueError):
    pass


def public_url(value, *, stream=False):
    if not isinstance(value, str) or not value or len(value) > 3000:
        return None
    url = urljoin(ORIGIN, value)
    try:
        parts = urlsplit(url)
        port = parts.port
        host = (parts.hostname or '').lower()
    except ValueError:
        return None
    allowed = host == 'anilibria.top' or host.endswith('.anilibria.top')
    if stream:
        allowed = allowed or any(host == h or host.endswith('.'+h) for h in ('libria.fun', 'anilibria.tv', 'anilibria.online'))
    if parts.scheme != 'https' or parts.username or parts.password or port not in (None, 443) or not allowed:
        return None
    return url


def release_card(row):
    if not isinstance(row, dict) or not isinstance(row.get('id'), int) or row['id'] < 1:
        return None
    name = row.get('name') or {}
    poster = row.get('poster') or {}
    if (row.get('age_rating') or {}).get('is_adult'):
        return None
    return {'id': row['id'], 'title': str(name.get('main') or name.get('english') or 'Anime')[:240],
            'english': str(name.get('english') or '')[:240], 'poster': public_url((poster.get('optimized') or {}).get('src') or poster.get('src')),
            'poster_thumbnail': public_url((poster.get('optimized') or {}).get('thumbnail') or poster.get('thumbnail')),
            'year': row.get('year'), 'type': (row.get('type') or {}).get('description', ''),
            'rating': (row.get('mal') or {}).get('rating'), 'ongoing': bool(row.get('is_ongoing')),
            'episodes_total': row.get('episodes_total'), 'description': str(row.get('description') or '')[:8000],
            'genres': [{'id': g['id'], 'name': str(g.get('name') or '')[:70]} for g in row.get('genres', []) if isinstance(g, dict) and isinstance(g.get('id'), int)],
            'blocked': bool(row.get('is_blocked_by_geo') or row.get('is_blocked_by_copyrights')),
            'source': 'AniLibria', 'source_url': ORIGIN+'/anime/releases/release/'+str(row.get('alias') or row['id'])}


class AnimeCatalog:
    def __init__(self):
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.lock = asyncio.Lock()

    async def fetch(self, path, params=None, ttl=120):
        key = (path, tuple(sorted((params or {}).items())))
        cached = self.cache.get(key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        async with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() < cached[0]:
                return cached[1]
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                    response = await client.get(API+path, params=params, headers={'Accept': 'application/json', 'User-Agent': 'Shadow-Anime/1.0'})
                if not response.is_success or len(response.content) > 8_000_000:
                    raise AnimeUnavailable('Anime manbasi hozir javob bermayapti. Keyinroq qayta urinib koring.')
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                if isinstance(exc, AnimeUnavailable):
                    raise
                raise AnimeUnavailable('Anime manbasi bilan aloqa uzildi. Qayta urinib koring.') from None
            if key in self.cache:
                self.cache_bytes -= self.cache[key][2]
            size = len(response.content)
            self.cache[key] = (time.monotonic()+ttl, data, size)
            self.cache_bytes += size
            self.cache.move_to_end(key)
            while len(self.cache) > 80 or self.cache_bytes > 32_000_000:
                _, old = self.cache.popitem(last=False)
                self.cache_bytes -= old[2]
            return data

    async def catalog(self, query='', page=1, sort='fresh', genre=None):
        params = {'limit': 24, 'page': page, 'f[sorting]': SORTS[sort]}
        if query:
            params['f[search]'] = query
        if genre:
            params['f[genres][]'] = genre
        data = await self.fetch('anime/catalog/releases', params)
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):
            raise AnimeUnavailable('Katalog javobi notogri. Keyinroq urinib koring.')
        cards = [card for row in data['data'] if (card := release_card(row))]
        meta = (data.get('meta') or {}).get('pagination') or {}
        return {'items': cards, 'page': page, 'pages': max(1, int(meta.get('total_pages') or 1)),
                'total': int(meta.get('total') or len(cards)), 'source': 'AniLibria', 'preferred_quality': '1080'}

    async def detail(self, release_id):
        row = await self.fetch('anime/releases/'+str(release_id), ttl=60)
        card = release_card(row)
        if not card or card['id'] != release_id:
            raise AnimeUnavailable('Bu anime mavjud emas.')
        episodes = []
        if not card['blocked']:
            for episode in row.get('episodes') or []:
                if not isinstance(episode, dict) or not isinstance(episode.get('ordinal'), (int, float)):
                    continue
                streams = {quality: url for quality in ('1080', '720', '480') if (url := public_url(episode.get('hls_'+quality), stream=True))}
                if not streams:
                    continue
                episodes.append({'id': str(episode.get('id') or ''), 'number': episode['ordinal'],
                    'name': str(episode.get('name') or '')[:240], 'duration': episode.get('duration') or 0,
                    'preview': public_url((episode.get('preview') or {}).get('src')), 'streams': streams,
                    'opening': episode.get('opening') or {}, 'ending': episode.get('ending') or {}})
        episodes.sort(key=lambda ep: ep['number'])
        return {**card, 'episodes': episodes, 'preferred_quality': '1080',
                'playback_notice': 'Manba bu anime uchun tomoshani cheklagan. Boshqa rasmiy manbani tanlang.' if card['blocked'] else
                                   '' if episodes else 'Bu manbada hozir tomosha qilinadigan seriyalar yoq.'}

    async def genres(self):
        data = await self.fetch('anime/genres', ttl=3600)
        return [{'id': row['id'], 'name': str(row.get('name') or '')[:70]} for row in data if isinstance(row, dict) and isinstance(row.get('id'), int)]


class AnimeProgress(BaseModel):
    release_id: int = Field(ge=1, le=10_000_000)
    episode: float = Field(ge=0, le=10000, allow_inf_nan=False)
    position: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)
    duration: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)
    favorite: bool = False
    status: Literal['watching', 'planned', 'watched'] = 'watching'
    quality: Literal['1080', '720', '480'] = '1080'


class AnimeStore:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv('SHADOW_ANIME_STORE') or Path(os.getenv('SHADOW_CHAT_STORE', '.shadow-state/workspace.sqlite3')).with_name('anime.sqlite3'))

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        os.chmod(self.path, 0o600)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS anime_library (account TEXT NOT NULL, release_id INTEGER NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(account,release_id))')
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self, account):
        with self.connect() as db:
            return [json.loads(row['payload']) for row in db.execute('SELECT payload FROM anime_library WHERE account=? ORDER BY updated DESC LIMIT 500', (str(account),))]

    def save(self, account, progress, card):
        payload = {**progress.model_dump(), 'title': card['title'], 'poster': card['poster'], 'poster_thumbnail': card.get('poster_thumbnail'), 'year': card['year'], 'updated': time.time()}
        if payload['duration'] and payload['position'] > payload['duration']:
            payload['position'] = payload['duration']
        with self.connect() as db:
            existing = db.execute('SELECT 1 FROM anime_library WHERE account=? AND release_id=?', (str(account),progress.release_id)).fetchone()
            if not existing and db.execute('SELECT COUNT(*) FROM anime_library WHERE account=?', (str(account),)).fetchone()[0] >= 500:
                raise ValueError('Kutubxona toldi. Eski yozuvlarni olib tashlang.')
            db.execute('INSERT OR REPLACE INTO anime_library VALUES (?,?,?,?)', (str(account),progress.release_id,json.dumps(payload, ensure_ascii=False),payload['updated']))
        return payload

    def delete(self, account, release_id):
        with self.connect() as db:
            db.execute('DELETE FROM anime_library WHERE account=? AND release_id=?', (str(account),release_id))


catalog = AnimeCatalog()
library = AnimeStore()


def router(auth, account):
    routes = APIRouter()

    @routes.get('/anime/api/catalog')
    async def get_catalog(q: str = Query(default='', max_length=120), page: int = Query(default=1, ge=1, le=1000),
                          sort: Literal['fresh','rating','year'] = 'fresh', genre: int | None = Query(default=None, ge=1, le=1000)):
        try:
            return await catalog.catalog(q.strip(), page, sort, genre)
        except AnimeUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @routes.get('/anime/api/genres')
    async def get_genres():
        try:
            return await catalog.genres()
        except AnimeUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @routes.get('/anime/api/releases/{release_id}')
    async def get_release(release_id: int):
        if not 1 <= release_id <= 10_000_000:
            raise HTTPException(404, 'Anime topilmadi.')
        try:
            return JSONResponse(await catalog.detail(release_id), headers={'Cache-Control':'no-store'})
        except AnimeUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @routes.get('/anime/api/library', dependencies=[Depends(auth)])
    async def get_library(request: Request):
        owner = account(request)
        return JSONResponse({'account_id': str(owner), 'items': library.list(owner)}, headers={'Cache-Control':'no-store'})

    @routes.post('/anime/api/library', dependencies=[Depends(auth)])
    async def save_library(request: Request, progress: AnimeProgress):
        owner = account(request)
        try:
            card = await catalog.detail(progress.release_id)
            if progress.episode and progress.episode not in {ep['number'] for ep in card['episodes']}:
                raise HTTPException(400, 'Bu seriya manbada topilmadi.')
            return JSONResponse(library.save(owner, progress, card), headers={'Cache-Control':'no-store'})
        except AnimeUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @routes.delete('/anime/api/library/{release_id}', dependencies=[Depends(auth)])
    async def delete_library(request: Request, release_id: int):
        library.delete(account(request), release_id)
        return {'ok': True}

    return routes

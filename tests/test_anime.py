import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from shadow import anime


def release(**changes):
    row={'id':12,'name':{'main':'Test anime','english':'Test'},'year':2026,
         'poster':{'src':'/storage/poster.webp'},'type':{'description':'TV'},'mal':{'rating':8.6},
         'age_rating':{'is_adult':False},'genres':[{'id':1,'name':'Adventure'}],
         'episodes':[{'id':'episode-one','ordinal':1,'duration':240,'opening':{'start':5,'stop':30},
                      'hls_1080':'https://cache.libria.fun/videos/1080.m3u8?token=public-media-token',
                      'hls_720':'https://cache.libria.fun/videos/720.m3u8'},
                     {'id':'episode-two','ordinal':2,'duration':260,'hls_720':'https://cache.libria.fun/videos/two-720.m3u8'}]}
    row.update(changes)
    return row


class AnimeCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_1080_is_real_available_stream_and_missing_quality_is_not_invented(self):
        catalog=anime.AnimeCatalog();catalog.fetch=AsyncMock(return_value=release())
        detail=await catalog.detail(12)
        self.assertIn('1080',detail['episodes'][0]['streams'])
        self.assertNotIn('1080',detail['episodes'][1]['streams'])
        self.assertIn('token=public-media-token',detail['episodes'][0]['streams']['1080'])
        self.assertEqual(detail['preferred_quality'],'1080')
        self.assertEqual(detail['episodes'][0]['opening']['stop'],30)

    async def test_provider_rights_and_geo_blocks_are_respected(self):
        for key in ['is_blocked_by_geo','is_blocked_by_copyrights']:
            catalog=anime.AnimeCatalog();catalog.fetch=AsyncMock(return_value=release(**{key:True}))
            detail=await catalog.detail(12)
            self.assertEqual(detail['episodes'],[])
            self.assertTrue(detail['playback_notice'])
            self.assertTrue(detail['blocked'])

    async def test_catalog_search_pagination_and_genres_reach_provider(self):
        catalog=anime.AnimeCatalog();catalog.fetch=AsyncMock(return_value={'data':[release()], 'meta':{'pagination':{'total_pages':3,'total':50}}})
        result=await catalog.catalog('Naruto',2,'rating',1)
        self.assertEqual(result['pages'],3);self.assertEqual(result['total'],50)
        catalog.fetch.assert_awaited_once_with('anime/catalog/releases',{'limit':24,'page':2,'f[sorting]':'RATING_DESC','f[search]':'Naruto','f[genres][]':1})

    async def test_cache_reuses_metadata_and_errors_do_not_echo_upstream_body(self):
        calls=[];real_client=httpx.AsyncClient
        def handle(request):
            calls.append(request)
            return httpx.Response(200,json=release())
        catalog=anime.AnimeCatalog()
        with patch('shadow.anime.httpx.AsyncClient',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw)):
            await catalog.detail(12);await catalog.detail(12)
        self.assertEqual(len(calls),1);self.assertGreater(catalog.cache_bytes,0)
        def fail(request):return httpx.Response(403,text='provider-private-secret')
        with patch('shadow.anime.httpx.AsyncClient',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(fail),**kw)):
            with self.assertRaises(anime.AnimeUnavailable) as error:await catalog.detail(13)
        self.assertNotIn('provider-private-secret',str(error.exception))

    def test_media_urls_cannot_escape_trusted_https_hosts(self):
        for url in ['http://cache.libria.fun/movie.m3u8','https://127.0.0.1/stream','https://evil.test/video','https://cache.libria.fun.evil.test/video','https://user:pass@cache.libria.fun/a','https://cache.libria.fun:9999/a','javascript:alert(1)','https://cache.libria.fun:bad/a']:
            self.assertIsNone(anime.public_url(url,stream=True),url)
        self.assertTrue(anime.public_url('https://cache.libria.fun/videos/one.m3u8',stream=True))
        self.assertTrue(anime.public_url('/storage/poster.webp'))


class AnimeLibraryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=anime.AnimeStore(Path(self.tmp.name)/'private.sqlite3')

    def test_every_account_has_its_own_watch_history_and_favorites(self):
        card=anime.release_card(release())
        self.store.save(11,anime.AnimeProgress(release_id=12,episode=1,position=50,duration=240,favorite=True),card)
        self.assertEqual(self.store.list(22),[])
        self.store.save(22,anime.AnimeProgress(release_id=12,episode=2,position=40,duration=260),card)
        self.store.delete(22,12)
        first=self.store.list(11)
        self.assertEqual(first[0]['episode'],1);self.assertTrue(first[0]['favorite'])
        self.assertEqual(self.store.path.stat().st_mode&0o777,0o600)

    async def test_private_routes_require_auth_and_reject_stale_account_writes(self):
        module=importlib.import_module('shadow.app')
        catalog=anime.AnimeCatalog();catalog.fetch=AsyncMock(return_value=release())
        with patch.object(anime,'library',self.store),patch.object(anime,'catalog',catalog),patch.object(module,'agent',SimpleNamespace(account_id=11)),patch.object(module,'settings',SimpleNamespace(setup_token='test',admin_token='')):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://test') as client:
                body={'release_id':12,'episode':1,'position':50,'duration':240,'favorite':True,'quality':'1080'}
                for method,path in [('GET','/anime/api/library'),('POST','/anime/api/library'),('DELETE','/anime/api/library/12')]:
                    self.assertEqual((await client.request(method,path,json=body)).status_code,401)
                headers={'Authorization':'Bearer test'}
                response=await client.post('/anime/api/library?account=22',headers=headers,json=body)
                self.assertEqual(response.status_code,409);self.assertEqual(self.store.list(11),[])
                response=await client.post('/anime/api/library?account=11',headers=headers,json=body)
                self.assertEqual(response.status_code,200)
                history=await client.get('/anime/api/library',headers=headers)
                self.assertEqual(history.json()['account_id'],'11');self.assertEqual(history.json()['items'][0]['position'],50)
                self.assertEqual(history.headers['cache-control'],'no-store')
                body['episode']=999
                self.assertEqual((await client.post('/anime/api/library',headers=headers,json=body)).status_code,400)
                body['episode']=1;body['position']=-1
                self.assertEqual((await client.post('/anime/api/library',headers=headers,json=body)).status_code,422)

    async def test_home_and_watch_are_public_but_never_contain_private_settings(self):
        module=importlib.import_module('shadow.app')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://test') as client:
            for path,page in [('/','home'),('/home','home'),('/anime','anime')]:
                response=await client.get(path)
                self.assertEqual(response.status_code,200)
                self.assertIn('data-page="'+page+'"',response.text)
                self.assertIn('Shadow AI',response.text);self.assertIn('Anime Watch',response.text)
                self.assertNotIn('TELEGRAM_SESSION',response.text)
            self.assertEqual((await client.get('/hub/vendor/hls.min.js')).status_code,200)
            player=await client.get('/hub/player.js')
            self.assertEqual(player.status_code,200)
            self.assertIn('text/javascript',player.headers['content-type'])
            self.assertEqual((await client.get('/hub/../../.env')).status_code,404)
            self.assertEqual((await client.get('/hub/app.py')).status_code,404)
            self.assertIn('loginForm',(await client.get('/dashboard')).text)

if __name__=='__main__':unittest.main()

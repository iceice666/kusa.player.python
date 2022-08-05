import asyncio
import json
import platform
import time
from typing import Optional

import streamlink
import urllib3
import youtube_dl
from prompt_toolkit.application import in_terminal
from rich.console import Console

from CONFIG import *

if platform.system() == "Windows":
    import os

    os.environ["PYTHON_VLC_MODULE_PATH"] = "./bin/vlc-windows"

import vlc

http = urllib3.PoolManager(headers={
    "user-agent": "Mozilla/5.0 (Windows NT 10.0  Win64  x64) AppleWebKit/537.36 (KHTML, like Gecko)"
                  "Chrome/103.0.5060.114 Safari/537.36 Edg/103.0.1264.62"})


def send_get_request(url) -> dict:
    return json.loads((http.request('GET', url)).data.decode('utf-8'))


class Player:
    console = Console()

    flag_repeat: bool = False
    flag_loop: bool = False
    flag_skip: bool = False
    _finish_signal_passed: bool = False

    playlist: list[dict[str:any]] = []
    nowplaying: dict = {}

    def __init__(self, args: tuple = ("--no-ts-trust-pcr", "--ts-seek-percent", "--no-video", "-q")):
        self._rl = asyncio.get_running_loop()

        self.player: vlc.MediaPlayer = vlc.Instance(args).media_player_new()
        self.player.audio_set_volume(50)

        self.player.event_manager().event_attach(vlc.EventType.MediaPlayerStopped,
                                                 lambda *_: asyncio.run_coroutine_threadsafe(self._playing_end(),
                                                                                             self._rl))

    def event_attach(self, event, callback: callable, *args, **kwargs):
        self.player.event_manager().event_attach(event, callback, args, kwargs)

    async def execute(self, cmd_args):
        for c in cmd_args:
            exec(c.replace("$", "self."))

    async def help_cmd(self):
        self.console.print("help!")

    async def queue(self):
        return self.playlist

    async def volume(self, vol: Optional[int] = None):
        if vol is None:
            pass
        else:
            self.player.audio_set_volume(vol)
        return self.player.audio_get_volume()

    async def position(self, pos: int | float):
        if 1 > pos >= 0:
            self.player.set_position(pos)
        elif 1 <= pos < self.player.get_length():
            self.player.set_position(pos / self.player.get_length())

    async def repeat(self):
        self.flag_repeat = not self.flag_repeat

    async def loop(self):
        self.flag_loop = not self.flag_loop

    async def skip(self):
        self.flag_skip = True
        self.player.stop()

    async def pause(self):
        self.player.set_pause(1)

    async def resume(self):
        self.player.set_pause(0)

    async def clear(self):
        self.playlist = []

    async def add_track(self, uri=None, vid_id=None, website=None, url=None, title=None, **kwargs):

        if url is not None and uri is not None:
            pass
        elif website is None:
            url = await Search.fetch_url(uri=uri)
        elif website.upper() == 'YOUTUBE' and vid_id is not None:
            uri = f'https://youtu.be/{vid_id}'
            url = await Search.fetch_youtube_url_info(url=uri)
        elif website.upper() == 'BILIBILI' and vid_id is not None:
            uri = f'https://www.bilibili.com/video/{vid_id}'
            url = await Search.fetch_bilibili_url_info(url=uri)
        else:
            return

        if title is None:
            title = uri

        info = self._make_info(
            title=title,
            url=url,
            source=uri,
            expired_time=int(time.time()) + 3600,
            **kwargs
        )

        self.playlist.append(info)

        if not self.player.is_playing():
            await self.play()

    @staticmethod
    def _make_info(**kwargs):
        return kwargs

    async def play(self):
        if not self.playlist:
            return
        self.nowplaying = self.playlist.pop(0)
        if time.time() > self.nowplaying['expired_time']:
            self.console.print(
                '[Player] [yellow]This link expired, re-fetching...[/yellow]')
            self.nowplaying['url'] = await Search.fetch_url(self.nowplaying['source'])
            self.nowplaying['expired_time'] = time.time()+3600

        self.player.set_media(
            self.player.get_instance().media_new(self.nowplaying['url']))
        async with in_terminal():
            self.console.print(
                '[Player] Nowplaying: ', self.nowplaying['source'])

        self.player.play()

    async def _playing_end(self):
        if self.flag_repeat and not self.flag_skip:
            self.playlist.insert(0, self.nowplaying)
        elif self.flag_loop:
            self.playlist.append(self.nowplaying)

        self.nowplaying = {}

        await self.play()


class Search:
    def __init__(self):
        pass

    @classmethod
    async def fetch_url(cls, uri):
        parse = urllib3.util.parse_url(uri)
        if parse.host is None:
            return
        elif parse.host == "www.bilibili.com":
            url = await cls.fetch_bilibili_url_info(url=uri)
        else:
            try:
                url = await cls.fetch_url_info(uri)
            except (streamlink.PluginError, streamlink.NoPluginError, AttributeError):
                url = uri
                if parse.host in ['www.youtube.com', 'youtu.be']:
                    url = await cls.fetch_youtube_url_info(url=uri)

        return url

    @staticmethod
    async def fetch_url_info(url):
        return (streamlink.streams(url))['best'].url

    @staticmethod
    async def fetch_youtube_url_info(url):

        with youtube_dl.YoutubeDL({"quiet": True}) as ydl:
            song_info = ydl.extract_info(
                url, download=False)["formats"][0]

        try:
            url = song_info["fragment_base_url"]
        except KeyError:
            url = song_info["url"]
        return url

    @staticmethod
    async def fetch_bilibili_url_info(url):

        bvid = (urllib3.util.parse_url(url)).path.split('/')[-1]

        cid = send_get_request(
            f'https://api.bilibili.com/x/player/pagelist?bvid={bvid}')['data'][0]['cid']

        urls = send_get_request(
            f'https://api.bilibili.com/x/player/playurl?cid={cid}&bvid={bvid}&platform=html5')['data']['durl']

        return urls[0]['url']

    @staticmethod
    async def youtube(searching):
        searched_list = send_get_request(
            f"https://www.googleapis.com/youtube/v3/search?part=snippet&"
            f"q={searching.replace(' ', '+')}&key={YOUTUBE_API}&maxResults=20&"
            "type=video"
        )['items']

        searched_result = {}
        for i in searched_list:
            title = i['snippet']['title']
            searched_result[title] = \
                ({
                    'platform': 'YOUTUBE',
                    'author': i['snippet']['channelTitle'],
                    'title': title,
                    'vidId': i['id']['videoId']})

        return searched_result

    @staticmethod
    async def bilibili(searching):
        searched_list = send_get_request(
            f'https://api.bilibili.com/x/web-interface/search/all/v2?keyword={searching}')['data']['result'][-1]['data']
        searched_result = {}
        for i in searched_list:
            title = str(i['title']).replace(
                '<em class="keyword">', '').replace('</em>', '')
            searched_result[title] = \
                ({
                    'platform': 'BILIBILI',
                    'author': i['author'],
                    'title': title,
                    'vidId': i['bvid']
                })
        return searched_result

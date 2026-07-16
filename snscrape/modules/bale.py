__all__ = ['LinkPreview', 'BalePost', 'BaleChannel', 'BaleChannelScraper']


import dataclasses
import datetime
import json
import logging
import re
import snscrape.base
import snscrape.utils
import typing


_logger = logging.getLogger(__name__)
_MARKDOWN_LINK_RE = re.compile(r'\[([^\]]*)\]\((https?://[^\s)]+)\)')
_BARE_URL_RE = re.compile(r'(?<!\()(https?://[^\s)]+)')
_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_MENTION_RE = re.compile(r'@([A-Za-z0-9_]+)')
_NEXT_F_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


@dataclasses.dataclass
class LinkPreview:
	href: str
	siteName: typing.Optional[str] = None
	title: typing.Optional[str] = None
	description: typing.Optional[str] = None
	image: typing.Optional[str] = None


@dataclasses.dataclass
class BalePost(snscrape.base.Item):
	url: str
	date: datetime.datetime
	viewCount: int
	content: typing.Optional[str]
	outlinks: list
	linkPreview: typing.Optional[LinkPreview] = None
	repliedPostUrl: typing.Optional[str] = None
	reactions: typing.Optional[typing.List[typing.Tuple[str, int]]] = None

	outlinksss = snscrape.base._DeprecatedProperty('outlinksss', lambda self: ' '.join(self.outlinks), 'outlinks')

	def __str__(self):
		return self.url


@dataclasses.dataclass
class BaleChannel(snscrape.base.Item):
	username: str
	title: str
	verified: bool
	photo: str
	description: typing.Optional[str] = None
	members: typing.Optional[int] = None

	def __str__(self):
		return f'https://ble.ir/s/{self.username}'


class BaleChannelScraper(snscrape.base.Scraper):
	name = 'bale-channel'

	def __init__(self, name, **kwargs):
		super().__init__(**kwargs)
		self._name = name
		self._headers = {
			'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36',
			'Accept': 'application/json, text/plain, */*',
		}
		self._apiHeaders = {
			**self._headers,
			'Content-Type': 'application/json',
			'Origin': 'https://ble.ir',
		}
		self._initialPage = None
		self._initialJson = None

	def _initial_page(self):
		if self._initialPage is None:
			r = self._get(f'https://ble.ir/s/{self._name}', headers=self._headers)
			if r.status_code != 200:
				raise snscrape.base.ScraperException(f'Got status code {r.status_code}')
			self._initialPage = r
			self._initialJson = self._parse_embedded_json(r.text)
		return self._initialPage, self._initialJson

	@staticmethod
	def _parse_embedded_json(html):
		payloads = _NEXT_F_PUSH_RE.findall(html)
		for payload in payloads:
			try:
				decoded = json.loads('"' + payload + '"')
			except json.JSONDecodeError:
				continue
			if ':' not in decoded:
				continue
			_, valueStr = decoded.split(':', 1)
			try:
				parsed = json.loads(valueStr)
			except json.JSONDecodeError:
				continue
			result = BaleChannelScraper._find_data_dict(parsed)
			if result:
				return result
		raise snscrape.base.ScraperException('Could not parse channel data from page; the page structure may have changed')

	@staticmethod
	def _find_data_dict(obj):
		if isinstance(obj, dict):
			if 'peer' in obj and 'group' in obj and 'messages' in obj:
				return obj
			for v in obj.values():
				result = BaleChannelScraper._find_data_dict(v)
				if result:
					return result
		elif isinstance(obj, (list, tuple)):
			for item in obj:
				result = BaleChannelScraper._find_data_dict(item)
				if result:
					return result
		return None

	def _extract_channel_info(self, data):
		group = data['group']
		kwargs = {}
		kwargs['username'] = group['nick']
		kwargs['title'] = group['title']
		kwargs['verified'] = group['isVerified']
		kwargs['photo'] = group['avatarUrl']
		if group.get('description'):
			kwargs['description'] = group['description']
		if group.get('members'):
			kwargs['members'] = group['members']
		return BaleChannel(**kwargs)

	def _message_to_item(self, msg, nick):
		rid = msg['rid']
		date = datetime.datetime.fromtimestamp(msg['date'] / 1000, tz=datetime.timezone.utc)
		viewCount = msg['viewCount']
		url = f'https://ble.ir/s/{nick}/{rid}'

		message = msg.get('message', {})
		content = None
		if 'textMessage' in message:
			content = message['textMessage'].get('text')
		elif 'documentMessage' in message:
			doc = message['documentMessage']
			if 'caption' in doc:
				content = doc['caption'].get('text')

		outlinks = []
		if content:
			for match in _MARKDOWN_LINK_RE.finditer(content):
				href = match.group(2)
				if href not in outlinks and href != url:
					outlinks.append(href)
			for match in _BARE_URL_RE.finditer(content):
				href = match.group(1).rstrip(')')
				if href not in outlinks and href != url:
					outlinks.append(href)
			for match in _EMAIL_RE.finditer(content):
				href = f'mailto:{match.group(0)}'
				if href not in outlinks:
					outlinks.append(href)
			for match in _MENTION_RE.finditer(content):
				href = f'https://ble.ir/{match.group(1)}'
				if href not in outlinks and href != url:
					outlinks.append(href)

		reactions = None
		if msg.get('reactions'):
			reactions = [(r['code'], r['cardinality']) for r in msg['reactions']]

		return BalePost(url=url, date=date, viewCount=viewCount, content=content, outlinks=outlinks,
		                linkPreview=None, repliedPostUrl=None, reactions=reactions)

	def get_items(self):
		r, data = self._initial_page()
		nick = data['group'].get('nick', self._name)
		messages = data.get('messages', [])
		if not messages:
			_logger.warning('No messages found on initial page')
			return

		peer = data['group']['peer']
		cursor = min(m['date'] for m in messages)
		seen = {m['rid'] for m in messages}

		yield from (self._message_to_item(m, nick) for m in messages)

		while True:
			body = json.dumps({'peer': peer, 'date': cursor})
			r = self._post('https://api.ble.ir/api/v1/LoadHistory', data=body, headers=self._apiHeaders)
			if r.status_code != 200:
				raise snscrape.base.ScraperException(f'LoadHistory got status code {r.status_code}')
			batch = r.json().get('history', [])
			if not batch:
				break

			newMessages = [m for m in batch if m['rid'] not in seen]
			if not newMessages:
				break

			seen.update(m['rid'] for m in newMessages)
			yield from (self._message_to_item(m, nick) for m in reversed(newMessages))
			cursor = min(m['date'] for m in newMessages)

	def _get_entity(self):
		_, data = self._initial_page()
		return self._extract_channel_info(data)

	@classmethod
	def _cli_setup_parser(cls, subparser):
		subparser.add_argument('channel', type=snscrape.utils.nonempty_string_arg('channel'), help='A channel name')

	@classmethod
	def _cli_from_args(cls, args):
		return cls._cli_construct(args, args.channel)

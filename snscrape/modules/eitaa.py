__all__ = ['LinkPreview', 'EitaaPost', 'EitaaChannel', 'EitaaChannelScraper']


import bs4
import dataclasses
import datetime
import logging
import re
import snscrape.base
import snscrape.utils
import typing
import urllib.parse


_logger = logging.getLogger(__name__)
_SINGLE_MEDIA_LINK_PATTERN = re.compile(r'^https://eitaa\.com/[^/]+/\d+\?single$')


@dataclasses.dataclass
class LinkPreview:
	href: str
	siteName: typing.Optional[str] = None
	title: typing.Optional[str] = None
	description: typing.Optional[str] = None
	image: typing.Optional[str] = None


@dataclasses.dataclass
class EitaaPost(snscrape.base.Item):
	url: str
	date: datetime.datetime
	viewCount: int
	content: str
	outlinks: list
	linkPreview: typing.Optional[LinkPreview] = None
	repliedPostUrl: typing.Optional[str] = None

	outlinksss = snscrape.base._DeprecatedProperty('outlinksss', lambda self: ' '.join(self.outlinks), 'outlinks')

	def __str__(self):
		return self.url


@dataclasses.dataclass
class EitaaChannel(snscrape.base.Item):
	username: str
	title: str
	verified: bool
	photo: str
	description: typing.Optional[str] = None
	members: typing.Optional[int] = None
	photos: typing.Optional[snscrape.base.IntWithGranularity] = None
	videos: typing.Optional[snscrape.base.IntWithGranularity] = None
	files: typing.Optional[snscrape.base.IntWithGranularity] = None

	photosGranularity = snscrape.base._DeprecatedProperty('photosGranularity', lambda self: self.photos.granularity if self.photos else None, 'photos.granularity')
	videosGranularity = snscrape.base._DeprecatedProperty('videosGranularity', lambda self: self.videos.granularity if self.videos else None, 'videos.granularity')
	filesGranularity = snscrape.base._DeprecatedProperty('filesGranularity', lambda self: self.files.granularity if self.files else None, 'files.granularity')

	def __str__(self):
		return f'https://eitaa.com/{self.username}'


class EitaaChannelScraper(snscrape.base.Scraper):
	name = 'eitaa-channel'

	def __init__(self, name, **kwargs):
		super().__init__(**kwargs)
		self._name = name
		self._headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36'}
		self._initialPage = None
		self._initialPageSoup = None

	def _initial_page(self):
		if self._initialPage is None:
			r = self._get(f'https://eitaa.com/{self._name}', headers = self._headers)
			if r.status_code != 200:
				raise snscrape.base.ScraperException(f'Got status code {r.status_code}')
			self._initialPage, self._initialPageSoup = r, bs4.BeautifulSoup(r.text, 'lxml')
		return self._initialPage, self._initialPageSoup

	def _soup_to_items(self, soup, pageUrl, onlyUsername = False):
		posts = soup.find_all('div', attrs = {'class': 'etme_widget_message', 'data-post': True})
		for post in reversed(posts):
			if onlyUsername:
				yield post['data-post'].split('/')[0]
				return
			
			dataPost = post['data-post']
			# data-post format: "channel_name/post_id"
			postParts = dataPost.split('/')
			if len(postParts) != 2:
				_logger.warning(f'Possibly incorrect data-post: {dataPost!r}')
				continue
			
			channelName, postId = postParts
			rawUrl = f'https://eitaa.com/s/{channelName}/{postId}'
			url = rawUrl
			
			# Extract date and view count
			date = None
			viewCount = None
			footerDiv = post.find('div', class_ = 'etme_widget_message_footer')
			if footerDiv:
				# Find date in message_info div
				messageInfoDiv = footerDiv.find('div', class_ = 'etme_widget_message_info')
				if messageInfoDiv:
					# Extract view count
					viewSpan = messageInfoDiv.find('span', class_ = 'etme_widget_message_views')
					if viewSpan and 'data-count' in viewSpan.attrs:
						try:
							viewCount = int(viewSpan['data-count'])
						except (ValueError, TypeError):
							_logger.warning(f'Could not parse view count from {viewSpan.get("data-count", "")}')
					
					# Extract date
					dateLink = messageInfoDiv.find('a', class_ = 'etme_widget_message_date')
					if dateLink:
						timeTag = dateLink.find('time')
						if timeTag and 'datetime' in timeTag.attrs:
							try:
								# Parse ISO format datetime: 2025-11-20T15:27:59+00:00
								dateStr = timeTag['datetime']
								date = datetime.datetime.fromisoformat(dateStr.replace('Z', '+00:00'))
							except (ValueError, AttributeError) as e:
								_logger.warning(f'Could not parse date from {timeTag.get("datetime", "")}: {e}')
			
			# Extract content
			content = None
			outlinks = []
			messageDiv = post.find('div', class_ = 'etme_widget_message_text')
			if messageDiv:
				for br in messageDiv.find_all('br'):
					br.replace_with('\n')
				content = messageDiv.get_text(strip = False)
				
				# Extract outlinks
				for link in post.find_all('a', href = True):
					# Skip author links
					if any(x in link.parent.attrs.get('class', []) for x in ('etme_widget_message_user', 'etme_widget_message_author', 'etme_widget_message_owner_name')):
						continue
					# Skip links to the post itself
					if link['href'] == rawUrl or link['href'] == url or link['href'] == f'/s/{channelName}/{postId}':
						continue
					# Skip single media links
					if _SINGLE_MEDIA_LINK_PATTERN.match(link['href']):
						continue
					href = urllib.parse.urljoin(pageUrl, link['href'])
					if href not in outlinks:
						outlinks.append(href)
			else:
				content = None
				outlinks = []
			
			# Link preview (not commonly seen in eitaa, but keeping structure)
			linkPreview = None
			linkPreviewA = post.find('a', class_ = 'etme_widget_message_link_preview')
			if linkPreviewA:
				kwargs = {}
				kwargs['href'] = urllib.parse.urljoin(pageUrl, linkPreviewA['href'])
				if (siteNameDiv := linkPreviewA.find('div', class_ = 'link_preview_site_name')):
					kwargs['siteName'] = siteNameDiv.text
				if (titleDiv := linkPreviewA.find('div', class_ = 'link_preview_title')):
					kwargs['title'] = titleDiv.text
				if (descriptionDiv := linkPreviewA.find('div', class_ = 'link_preview_description')):
					kwargs['description'] = descriptionDiv.text
				if (imageI := linkPreviewA.find('i', class_ = 'link_preview_image')):
					if imageI.get('style', '').startswith("background-image:url('"):
						kwargs['image'] = imageI['style'][22 : imageI['style'].index("'", 22)]
					else:
						_logger.warning(f'Could not process link preview image on {url}')
				linkPreview = LinkPreview(**kwargs)
			
			# Replied post URL (not commonly seen in eitaa, but keeping structure)
			repliedPostUrl = None
			repliedPost = post.find('a', class_ = 'etme_widget_message_reply')
			if repliedPost:
				repliedPostUrl = repliedPost['href']
			
			yield EitaaPost(url = url, date = date, viewCount = viewCount, content = content, outlinks = outlinks,
			               linkPreview = linkPreview, repliedPostUrl = repliedPostUrl)

	def get_items(self):
		r, soup = self._initial_page()
		while True:
			yield from self._soup_to_items(soup, r.url)
			# Look for pagination link
			pageLink = soup.find('a', attrs = {'class': 'eme_messages_more', 'data-before': True})
			if not pageLink:
				break
			nextPageUrl = urllib.parse.urljoin(r.url, pageLink['href'])
			r = self._get(nextPageUrl, headers = self._headers)
			if r.status_code != 200:
				raise snscrape.base.ScraperException(f'Got status code {r.status_code}')
			soup = bs4.BeautifulSoup(r.text, 'lxml')

	def _get_entity(self):
		kwargs = {}
		r, soup = self._initial_page()
		
		channelInfoDiv = soup.find('div', class_ = 'etme_channel_info')
		if not channelInfoDiv:
			_logger.warning('Channel info div not found')
			return None
		
		# Extract title and verified status
		titleWrapDiv = channelInfoDiv.find('div', class_ = 'etme_channel_info_header_title_wrap')
		if titleWrapDiv:
			titleDiv = titleWrapDiv.find('div', class_ = 'etme_channel_info_header_title')
			if titleDiv:
				titleSpan = titleDiv.find('span')
				if titleSpan:
					kwargs['title'] = titleSpan.text
			kwargs['verified'] = bool(titleWrapDiv.find('i', class_ = 'verified-icon'))
		
		# Extract username
		usernameDiv = channelInfoDiv.find('div', class_ = 'etme_channel_info_header_username')
		if usernameDiv:
			usernameA = usernameDiv.find('a')
			if usernameA:
				usernameText = usernameA.text
				# Remove @ if present
				if usernameText.startswith('@'):
					usernameText = usernameText[1:]
				kwargs['username'] = usernameText
		
		# Extract photo
		photoContainer = channelInfoDiv.find('i', class_ = 'etme_page_photo_image')
		if photoContainer:
			photoImg = photoContainer.find('img')
			if photoImg and 'src' in photoImg.attrs:
				kwargs['photo'] = urllib.parse.urljoin('https://eitaa.com', photoImg['src'])
		
		# Extract description
		descriptionDiv = channelInfoDiv.find('div', class_ = 'etme_channel_info_description')
		if descriptionDiv:
			kwargs['description'] = descriptionDiv.text
		
		# Extract counters (members, photos, videos, files)
		def parse_counter_value(text):
			# Parse Persian numbers like "110.5هزار" or "5.3هزار"
			text = text.strip()
			if 'هزار' in text:
				# Extract number part
				numPart = text.replace('هزار', '').strip()
				try:
					value = float(numPart) * 1000
					# Determine granularity based on decimal places
					if '.' in numPart:
						decimalPlaces = len(numPart.split('.')[1])
						granularity = 10 ** (3 - decimalPlaces)
					else:
						granularity = 1000
					return int(value), granularity
				except ValueError:
					_logger.warning(f'Could not parse counter value: {text}')
					return None, None
			else:
				# Try to parse as integer
				try:
					return int(text.replace(' ', '')), 1
				except ValueError:
					_logger.warning(f'Could not parse counter value: {text}')
					return None, None
		
		for counterDiv in channelInfoDiv.find_all('div', class_ = 'etme_channel_info_counter'):
			valueSpan = counterDiv.find('span', class_ = 'counter_value')
			typeSpan = counterDiv.find('span', class_ = 'counter_type')
			
			if valueSpan and typeSpan:
				# Try to get from data-count attribute first (more accurate)
				if 'data-count' in valueSpan.attrs:
					try:
						value = int(valueSpan['data-count'])
						granularity = 1
					except (ValueError, TypeError):
						value, granularity = parse_counter_value(valueSpan.text)
				else:
					value, granularity = parse_counter_value(valueSpan.text)
				
				if value is None:
					continue
				
				typeText = typeSpan.text.strip()
				if typeText == 'دنبال‌کننده':
					kwargs['members'] = value
				elif typeText == 'عکس':
					kwargs['photos'] = snscrape.base.IntWithGranularity(value, granularity)
				elif typeText == 'ویدیو':
					kwargs['videos'] = snscrape.base.IntWithGranularity(value, granularity)
				elif typeText == 'فایل':
					kwargs['files'] = snscrape.base.IntWithGranularity(value, granularity)
		
		return EitaaChannel(**kwargs)

	@classmethod
	def _cli_setup_parser(cls, subparser):
		subparser.add_argument('channel', type = snscrape.utils.nonempty_string_arg('channel'), help = 'A channel name')

	@classmethod
	def _cli_from_args(cls, args):
		return cls._cli_construct(args, args.channel)


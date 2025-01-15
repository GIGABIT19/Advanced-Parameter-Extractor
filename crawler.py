import asyncio
import random
import aiohttp
import warnings
from bs4 import BeautifulSoup
import time
import re
from urllib.parse import urlparse, urlunparse, urljoin, unquote

warnings.filterwarnings("ignore")

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
    'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:54.0) Gecko/20100101 Firefox/54.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/61.0.3163.100 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_6) AppleWebKit/604.5.6 (KHTML, like Gecko) Version/11.0.3 Safari/604.5.6',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64.0.3282.140 Safari/537.36 Edge/17.17134'
]

MAX_THREADS = 10


async def get_page(session, url):
    try:
        async with session.get(url, headers={'User-Agent': random.choice(USER_AGENTS)}) as response:
            if response.status == 200:
                return await response.text()
            else:
                return None
    except Exception as e:
        #print(f"Error fetching {url}: {e}")
        return None


async def fetch_sitemap_urls(sitemap_url):
    headers = {
        'User-Agent': 'Mozilla/5.0'
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(sitemap_url, headers=headers) as response:
                response.raise_for_status()
                return await response.text()
    except aiohttp.ClientError as e:
        # print(f"An error occurred while fetching the sitemap: {e}")
        return None

cached_sitemaps = {}
async def fetch_and_parse_sitemap(sitemap_url):
    if sitemap_url not in cached_sitemaps:
        sitemap_content = await fetch_sitemap_urls(sitemap_url)
        if sitemap_content:
            cached_sitemaps[sitemap_url] = await parse_sitemap_urls(sitemap_content)
        else:
            cached_sitemaps[sitemap_url] = []

    return cached_sitemaps[sitemap_url]

async def parse_sitemap_urls(sitemap_content):
    if sitemap_content is None:
        return []

    soup = BeautifulSoup(sitemap_content, 'lxml')
    url_tags = soup.find_all('loc')
    sitemap_urls = [url_tag.text.strip() for url_tag in url_tags]
    urls = []

    tasks = []
    for sitemap_url in sitemap_urls:
        if sitemap_url.endswith('.xml'):
            tasks.append(fetch_and_parse_sitemap(sitemap_url))
        else:
            urls.append(sitemap_url)

    parsed_urls = await asyncio.gather(*tasks)
    for parsed_url_list in parsed_urls:
        urls.extend(parsed_url_list)

    return urls


async def extract_parameters(session, url):
    try:
        page_content = await get_page(session, url)
        if not page_content:
            return set()
        soup = BeautifulSoup(page_content, 'lxml')
        results = set()
        # Extract parameters from href attributes
        links = soup.find_all('a', href=True)
        for link in links:
            href = link['href']
            params = re.findall(r'\?([^=&]+)=([^&]+)', href)
            if params:
                params_str = '&'.join([f"{key}={value}" for key, value in params])
                results.add(f"{url}?{params_str}")
        # Extract parameters from form elements
        forms = soup.find_all('form')
        for form in forms:
            params_dict = {input_element.get('name', ''): input_element.get('value', '') for input_element in
                           form.find_all(['input', 'textarea', 'select', 'button']) if input_element.get('name')}
            if params_dict:
                params_str = '&'.join([f"{key}={value}" for key, value in params_dict.items()])
                results.add(f"{url}?{params_str}")
        return results

    except Exception as e:
        # print(f"An error occurred: {e}")
        return set()


async def extract_links(session, url):
    try:
        page_content = await get_page(session, url)
        if not page_content:
            return []

        base_url = url
        soup = BeautifulSoup(page_content, 'lxml')

        # Extract links from various tags
        tags_to_extract = ['a', 'link', 'script', 'img', 'iframe', 'form', 'area', 'object']
        links = []
        for tag in tags_to_extract:
            links.extend([urljoin(base_url, link.get('src', link.get('href', link.get('action', link.get('data')))))
                          for link in soup.find_all(tag, src=True) + soup.find_all(tag, href=True) +
                          soup.find_all(tag, action=True) + soup.find_all(tag, data=True)])

        # Extract links from meta refresh tags
        meta_refresh = soup.find('meta', attrs={'http-equiv': 'refresh', 'content': re.compile('URL=')})
        if meta_refresh:
            content = meta_refresh['content'].lower()
            url_match = re.search('url=([^\s]+)', content)
            if url_match:
                redirect_url = url_match.group(1)
                links.append(urljoin(base_url, redirect_url))

        # Extract links from srcset attribute in img tags
        links.extend([urljoin(base_url, src) for src in re.findall(r'\S+\s+\d+w',
                                                                    ' '.join([img.get('srcset', '') for img in
                                                                              soup.find_all('img')]))])

        # Filter out links with non-HTTP schemes
        links = [link for link in links if urlparse(link).scheme in ['http', 'https']]
        return links

    except Exception as e:
        print(f"Error extracting links for {url}: {e}")
        return []


async def normalize_url(url, base_url=None):
    if not base_url:
        base_url = url

    parsed_url = urlparse(url)
    base_parsed_url = urlparse(base_url)

    scheme = parsed_url.scheme or base_parsed_url.scheme
    netloc = parsed_url.netloc or base_parsed_url.netloc

    path = base_parsed_url.path if parsed_url.path == '' else urljoin(base_parsed_url.path, parsed_url.path)
    path = unquote(path)

    query_params = parsed_url.query
    fragment = parsed_url.fragment

    normalized_url = urlunparse((scheme, netloc, path, '', query_params, fragment))

    return normalized_url

async def crawl_and_extract_params(seed_url, max_depth=3, max_urls=None):
    sitemap_url = urljoin(seed_url, 'sitemap.xml')
    sitemap_content = await fetch_sitemap_urls(sitemap_url)
    if sitemap_content:
        sitemap_urls = await parse_sitemap_urls(sitemap_content)
    else:
        sitemap_urls = []
    visited_urls = set()
    params_list = set()
    async with aiohttp.ClientSession() as session:
        urls_to_visit = [seed_url] + sitemap_urls
        while urls_to_visit and (max_urls is None or len(params_list) < max_urls):
            url = urls_to_visit.pop(0)
            if url in visited_urls:
                continue
            visited_urls.add(url)
            try:
                parameters = await extract_parameters(session, url)
                if parameters:
                    params_list.update(parameters)
                    if max_depth is not None and len(urlparse(url).path.split('/')) - 1 >= max_depth:
                        continue
                    links = await extract_links(session, url)
                    normalized_links = await asyncio.gather(*(normalize_url(link, url) for link in links))
                    for normalized_link in normalized_links:
                        if normalized_link not in visited_urls:
                            link_params = await extract_parameters(session, normalized_link)
                            if link_params:
                                urls_to_visit.append(normalized_link)
                                params_list.update(link_params)

            except Exception as e:
                pass
                # print(f"Error processing {url}: {e}")

    return params_list


async def main():
    start_time = time.time()
    seed_url = input("The url: ")
    params_list = await crawl_and_extract_params(seed_url, max_depth=3, max_urls=100)
    end_time = time.time()
    print(f"Crawled {len(params_list)} URLs in {end_time - start_time} seconds")
    for item in params_list:
        print(item)


asyncio.run(main())

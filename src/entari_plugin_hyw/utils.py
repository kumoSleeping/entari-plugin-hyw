#!/usr/bin/env python3
"""
工具函数模块
"""
import asyncio
import httpx
import re
from typing import List, Dict, Optional
from enum import Enum


class SearchEngine(Enum):
    """搜索引擎枚举"""
    BING_CN = "bing_cn"  # 必应中国
    DUCKDUCKGO = "duckduckgo"  # DuckDuckGo
    AUTO = "auto"  # 自动选择（优先必应中国）


async def universal_search(
    keywords: List[str], 
    max_results: int = 5, 
    exact_search: bool = False,
    engine: SearchEngine = SearchEngine.AUTO
) -> List[Dict[str, str]]:
    """
    通用搜索函数，支持多个搜索引擎
    
    Args:
        keywords: 关键词列表
        max_results: 每个关键词的最大结果数
        exact_search: 是否进行精确搜索（用引号包裹）
        engine: 搜索引擎选择
    
    Returns:
        list: 搜索结果列表，每个元素包含title, url, snippet, domain, type, keyword, engine字段
    """
    if not keywords:
        return []
    
    # 自动选择引擎时，优先使用必应中国
    if engine == SearchEngine.AUTO:
        # 先尝试必应中国
        results = await _bing_search(keywords, max_results, exact_search)
        if results:
            return results
        # 必应失败时回退到DuckDuckGo
        return await _duckduckgo_search(keywords, max_results, exact_search)
    elif engine == SearchEngine.BING_CN:
        return await _bing_search(keywords, max_results, exact_search)
    elif engine == SearchEngine.DUCKDUCKGO:
        return await _duckduckgo_search(keywords, max_results, exact_search)
    else:
        return []


async def _bing_search(keywords: List[str], max_results: int, exact_search: bool) -> List[Dict[str, str]]:
    """
    必应中国搜索实现
    """
    all_results = []
    url = "https://cn.bing.com/search"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
    }
    
    async def search_single_keyword(client, keyword):
        if exact_search:
            query = f'"{keyword.strip()}"'
        else:
            query = f"{keyword.strip()} -csdn -gitcode"
        
        params = {"q": query}
        
        try:
            response = await client.get(url, params=params)
            if response.status_code != 200:
                return []
            
            content = response.text
            keyword_results = []
            
            # 必应搜索结果解析
            # 必应的li标签格式: <li class="b_algo" data-id iid=SERP.5330>
            result_blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', content, re.DOTALL)
            
            for block in result_blocks[:max_results]:
                # 必应的标题格式：<h2><a target="_blank" href="...">title</a></h2>
                title_match = re.search(r'<h2[^>]*><a[^>]*href="(http[^"]*?)"[^>]*>(.*?)</a></h2>', block, re.DOTALL)
                
                if title_match:
                    href, title_html = title_match.groups()
                    
                    # 清理标题中的HTML标签（如<strong>等）
                    title = re.sub(r'<[^>]+>', '', title_html).strip()
                    # HTML实体解码
                    title = title.replace('&#183;', '·').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
                    
                    # 提取描述
                    snippet = ""
                    snippet_match = re.search(r'<p[^>]*>([^<]+)</p>', block)
                    if snippet_match:
                        snippet = snippet_match.group(1).strip()
                    
                    # 提取域名
                    domain_match = re.search(r'https?://([^/]+)', href)
                    domain = domain_match.group(1) if domain_match else ""
                    
                    keyword_results.append({
                        'title': title.strip(),
                        'url': href,
                        'snippet': snippet,
                        'domain': domain,
                        'type': 'search_result',
                        'keyword': keyword,
                        'engine': 'bing_cn'
                    })
            
            return keyword_results
            
        except Exception:
            return []
    
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
            tasks = [search_single_keyword(client, keyword) for keyword in keywords]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)
            
            for results in results_list:
                if isinstance(results, list):
                    all_results.extend(results)
            
            return all_results
            
    except Exception:
        return []


async def _duckduckgo_search(keywords: List[str], max_results: int, exact_search: bool) -> List[Dict[str, str]]:
    """
    DuckDuckGo搜索实现
    """
    all_results = []
    url = "https://html.duckduckgo.com/html/"
    
    async def search_single_keyword(client, keyword):
        if exact_search:
            query = f'"{keyword.strip()}"'
        else:
            query = f"{keyword.strip()} -csdn -gitcode"
        
        payload = {
            "q": query,
            "b": "",
            "l": "us-en"
        }
        
        try:
            response = await client.post(url, data=payload)
            
            if response.status_code != 200 or 'no-results' in response.text.lower():
                return []
            
            keyword_results = []
            
            # DuckDuckGo搜索结果解析
            result_blocks = re.findall(
                r'<div class="result results_links[^"]*"[^>]*>(.*?)</div>\s*(?=<div class="result|<div id=|$)', 
                response.text, 
                re.DOTALL
            )
            
            for block in result_blocks[:max_results]:
                title_match = re.search(
                    r'<h2 class="result__title">\s*<a[^>]*href="([^"]*)"[^>]*>([^<]+)</a>\s*</h2>', 
                    block
                )
                
                if title_match:
                    href, title = title_match.groups()
                    
                    # 提取描述
                    snippet = ""
                    snippet_match = re.search(r'class="result__snippet"[^>]*>([^<]+)<', block)
                    if snippet_match:
                        snippet = snippet_match.group(1).strip()
                    
                    # 提取域名
                    domain_match = re.search(r'https?://([^/]+)', href)
                    domain = domain_match.group(1) if domain_match else ""
                    
                    if href.startswith('http'):
                        keyword_results.append({
                            'title': title.strip(),
                            'url': href,
                            'snippet': snippet,
                            'domain': domain,
                            'type': 'search_result',
                            'keyword': keyword,
                            'engine': 'duckduckgo'
                        })
            
            return keyword_results
            
        except Exception:
            return []
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            tasks = [search_single_keyword(client, keyword) for keyword in keywords]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)
            
            for results in results_list:
                if isinstance(results, list):
                    all_results.extend(results)
            
            return all_results
            
    except Exception:
        return []


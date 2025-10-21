#!/usr/bin/env python3
"""
工具函数模块
"""
import asyncio
import httpx
import re
from typing import List, Dict


async def duck_search_real(keywords: List[str], max_results: int = 5, region: str = "us-en", exact_search: bool = False) -> List[Dict[str, str]]:
    """
    使用DuckDuckGo HTML端点进行搜索
    
    Args:
        keywords: 关键词列表
        max_results: 每个关键词的最大结果数
        region: 地区设置
        exact_search: 是否进行精确搜索（用引号包裹）
    
    Returns:
        list: 搜索结果列表，每个元素包含title, url, snippet, domain, type, keyword字段
    """
    if not keywords:
        return []
    
    all_results = []
    url = "https://html.duckduckgo.com/html/"
    
    async def search_single_keyword(client, keyword):
        """搜索单个关键词"""
        if exact_search:
            # 精确搜索：用引号包裹关键词
            query = f'"{keyword.strip()}"'
        else:
            # 普通搜索：添加屏蔽词
            query = f"{keyword.strip()} -csdn -gitcode"
        
        payload = {
            "q": query,
            "b": "",
            "l": region
        }
        
        try:
            response = await client.post(url, data=payload)
            
            if response.status_code != 200 or 'no-results' in response.text.lower():
                return []
            
            keyword_results = []
            
            # 查找搜索结果块
            result_blocks = re.findall(
                r'<div class="result results_links[^"]*"[^>]*>(.*?)</div>\s*(?=<div class="result|<div id=|$)', 
                response.text, 
                re.DOTALL
            )
            
            for block in result_blocks[:max_results]:
                # 提取标题和链接
                title_match = re.search(
                    r'<h2 class="result__title">\s*<a[^>]*href="([^"]*)"[^>]*>([^<]+)</a>\s*</h2>', 
                    block
                )
                
                if title_match:
                    href, title = title_match.groups()
                    
                    # 提取描述片段
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
                            'keyword': keyword
                        })
            
            return keyword_results
            
        except Exception:
            return []
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # 并发搜索所有关键词
            tasks = [search_single_keyword(client, keyword) for keyword in keywords]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 合并所有结果
            for results in results_list:
                if isinstance(results, list):
                    all_results.extend(results)
            
            return all_results
            
    except Exception:
        return []

#!/usr/bin/env python3
"""
工具函数模块
"""
import asyncio
import httpx
import re
from typing import List, Dict


async def duck_search_real(keywords: List[str], max_results: int = 5, region: str = "us-en") -> List[Dict[str, str]]:
    """
    使用DDGS实际使用的DuckDuckGo HTML端点进行搜索，对每个关键词单独进行严格搜索
    
    Args:
        keywords: 关键词列表，每个关键词会单独搜索
        max_results: 每个关键词的最大结果数
        region: 地区设置 (us-en, cn-zh, de-de等)
    
    Returns:
        list: 所有搜索结果的合并列表，每个元素包含title, url, snippet, domain, type, keyword字段
    """
    if not keywords:
        return []
    
    all_results = []
    
    async def search_single_keyword(client, keyword):
        """搜索单个关键词"""
        keyword = keyword.strip()
        
        # 添加屏蔽参数到查询中
        query_with_blocks = f"{keyword} -csdn -gitcode"
        
        # 使用DDGS实际使用的端点
        url = "https://html.duckduckgo.com/html/"
        
        # 构建POST数据
        payload = {
            "q": query_with_blocks,
            "b": "",
            "l": region
        }
        
        try:
            # 发送POST请求
            response = await client.post(url, data=payload)
            
            if response.status_code != 200:
                return []
            
            html_content = response.text
            keyword_results = []
            
            # 查找所有搜索结果块
            result_blocks = re.findall(
                r'<div class="result results_links[^"]*"[^>]*>(.*?)</div>\s*(?=<div class="result|<div id=|$)', 
                html_content, 
                re.DOTALL
            )
            
            for i, block in enumerate(result_blocks[:max_results]):
                # 提取标题和链接
                title_match = re.search(
                    r'<h2 class="result__title">\s*<a[^>]*href="([^"]*)"[^>]*>([^<]+)</a>\s*</h2>', 
                    block
                )
                
                if title_match:
                    href, title = title_match.groups()
                    
                    # 提取描述片段
                    snippet = ""
                    snippet_patterns = [
                        r'<a class="result__snippet"[^>]*>([^<]+)</a>',
                        r'<div class="result__snippet"[^>]*>([^<]+)</div>',
                        r'class="result__snippet"[^>]*>([^<]+)<',
                    ]
                    
                    for pattern in snippet_patterns:
                        snippet_match = re.search(pattern, block, re.DOTALL)
                        if snippet_match:
                            snippet = snippet_match.group(1).strip()
                            break
                    
                    # 如果没找到snippet，尝试其他方法
                    if not snippet:
                        extras_match = re.search(r'<div class="result__extras"[^>]*>(.*?)</div>', block, re.DOTALL)
                        if extras_match:
                            extras_content = extras_match.group(1)
                            text_content = re.sub(r'<[^>]+>', '', extras_content)
                            text_content = re.sub(r'https?://[^\s]+', '', text_content)
                            snippet = ' '.join(text_content.split())[:200]
                    
                    # 提取域名
                    domain = ""
                    domain_match = re.search(r'https?://([^/]+)', href)
                    if domain_match:
                        domain = domain_match.group(1)
                    
                    if href.startswith('http'):
                        result_data = {
                            'title': title.strip(),
                            'url': href,
                            'snippet': snippet,
                            'domain': domain,
                            'type': 'search_result',
                            'keyword': keyword  # 添加搜索的关键词信息
                        }
                        keyword_results.append(result_data)
            
            # 备用解析方法
            if not keyword_results:
                simple_pattern = r'<a[^>]*href="(https?://[^"]*)"[^>]*>([^<]+)</a>'
                all_matches = re.findall(simple_pattern, html_content)
                
                seen_urls = set()
                for href, title in all_matches:
                    if (href not in seen_urls and 
                        len(keyword_results) < max_results and 
                        'duckduckgo.com' not in href and
                        href.startswith('http')):
                        
                        domain_match = re.search(r'https?://([^/]+)', href)
                        domain = domain_match.group(1) if domain_match else ""
                        
                        keyword_results.append({
                            'title': title.strip(),
                            'url': href,
                            'snippet': "",
                            'domain': domain,
                            'type': 'search_result',
                            'keyword': keyword
                        })
                        seen_urls.add(href)
            
            return keyword_results
            
        except Exception as e:
            return []
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # 并发搜索所有关键词
            tasks = [search_single_keyword(client, keyword) for keyword in keywords]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 合并所有结果
            for results in results_list:
                if isinstance(results, list):  # 忽略异常结果
                    all_results.extend(results)
            
            return all_results
            
    except Exception as e:
        return []


# def format_search_results(results: List[Dict[str, str]], max_length: int = 2000) -> str:
#     """
#     格式化搜索结果为文本
    
#     Args:
#         results: 搜索结果列表
#         max_length: 最大返回长度
    
#     Returns:
#         str: 格式化后的搜索结果文本
#     """
#     if not results:
#         return "没有找到相关搜索结果。"
    
#     formatted_lines = []
#     for i, result in enumerate(results, 1):
#         lines = [f"{i}. {result['title']}"]
        
#         if result.get('snippet'):
#             lines.append(f"   {result['snippet']}")
        
#         lines.append(f"   链接: {result['url']}")
        
#         if result.get('domain'):
#             lines.append(f"   来源: {result['domain']}")
        
#         formatted_lines.extend(lines)
#         formatted_lines.append("")  # 空行分隔
    
#     formatted_text = "\n".join(formatted_lines)
    
#     # 如果超长则截断
#     if len(formatted_text) > max_length:
#         formatted_text = formatted_text[:max_length] + "...\n\n(结果已截断，如需完整信息请缩小搜索范围)"
    
#     return formatted_text

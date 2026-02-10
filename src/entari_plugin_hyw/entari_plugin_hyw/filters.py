"""
Filter syntax parsing utilities.
"""

import re
from typing import List, Tuple, Optional


def parse_filter_syntax(query: str, max_count: int = 3):
    """
    Parse enhanced filter syntax supporting:
    - Chinese/English colons (: ：) and commas (, ，)
    - Multiple filters: "mcmod=2, github=1 : xxx" 
    - Index lists: "1, 2, 3 : xxx"
    - Max total selections
    
    Returns:
        (filters, search_query, error_msg)
        filters: list of (filter_type, filter_value, count) tuples
                 filter_type: 'index' or 'link'
                 count: how many to get (default 1)
        search_query: the actual search query
        error_msg: error message if exceeded max
    """
    if not query:
        return [], query, None
    
    # Skip filter parsing if query contains URL (has :// pattern)
    if re.search(r'https?://', query):
        return [], query.strip(), None
    
    # Normalize Chinese punctuation to English
    normalized = query.replace('：', ':').replace('，', ',').replace('、', ',')
    
    # Handle escaped colons: \: or /: -> placeholder
    normalized = re.sub(r'[/\\]:', '\x00COLON\x00', normalized)
    
    # Split by colon - last part is the search query
    parts = normalized.split(':')
    if len(parts) < 2:
        # No colon found, restore escaped colons and return as-is
        return [], query.replace('\\:', ':').replace('/:', ':'), None
    
    # Everything after the last colon is the search query
    search_query = parts[-1].strip().replace('\x00COLON\x00', ':')
    
    # Everything before is the filter specification
    filter_spec = ':'.join(parts[:-1]).strip().replace('\x00COLON\x00', ':')
    
    if not filter_spec or not search_query:
        return [], query.replace('\\:', ':').replace('/:', ':'), None
    
    # Parse filter specifications (comma-separated)
    filter_items = [f.strip() for f in filter_spec.split(',') if f.strip()]
    
    filters = []
    for item in filter_items:
        # Check for "filter=count" pattern (e.g., "mcmod=2")
        eq_match = re.match(r'^(\w+)\s*=\s*(\d+)$', item)
        if eq_match:
            filter_name = eq_match.group(1).lower()
            count = int(eq_match.group(2))
            filters.append(('link', filter_name, count))
        elif item.isdigit():
            # Pure index
            filters.append(('index', int(item), 1))
        else:
            # Filter name without count (default count=1)
            filters.append(('link', item.lower(), 1))
    
    # Calculate total count
    total = sum(f[2] for f in filters)
    if total > max_count:
        return [], search_query, f"最多选择{max_count}个结果 (当前选择了{total}个)"
    
    # Append filter names to search query
    # Extract filter names (only 'link' type, skip 'index' type)
    filter_names = [f[1] for f in filters if f[0] == 'link']
    if filter_names:
        # Append filter names to search query: "search_query filter1 filter2"
        search_query = f"{search_query} {' '.join(filter_names)}"
    
    return filters, search_query, None

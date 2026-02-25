from .._public.search.service import DuckDuckGoSearchService

async def web_fetch(url: str, headless: bool = True) -> str:
    """
    访问网页并获取内容和截图。
    """
    print(f"  [WebFetchTool] Fetching: {url}")
    service = DuckDuckGoSearchService(headless=headless)

    # 复刻 old 逻辑: fetch_page 获取内容 + 截图
    result = await service.fetch_page(url, include_screenshot=True)

    content = result.get("content", "")
    title = result.get("title", "")
    img_b64 = result.get("raw_screenshot_b64") or result.get("screenshot_b64")

    if not content and not img_b64:
        return f"Failed to fetch {url}"

    output = f"Title: {title}\nURL: {url}\n\n"

    if img_b64:
        output += f"[IMAGE_BASE64: {img_b64}]\n\n"

    output += f"Content:\n{content}"

    return output

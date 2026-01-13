import asyncio  
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig  
  
async def main():  
    # 配置截图  
    config = CrawlerRunConfig(  
        screenshot=True,
    )  
      
    async with AsyncWebCrawler() as crawler:  
        result = await crawler.arun("https://minecraft.fandom.com/zh/wiki/%E9%9B%AA%E7%90%83", config=config)  
          
        if result.success and result.screenshot:  
            # 保存截图  
            import base64  
            with open("screenshot.png", "wb") as f:  
                f.write(base64.b64decode(result.screenshot))  
            print("截图已保存")


if __name__ == "__main__":
    asyncio.run(main())
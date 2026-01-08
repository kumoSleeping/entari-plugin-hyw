import sys
import asyncio
from pathlib import Path

# Directly add the core directory to sys.path to avoid importing the parent package (and triggering entari init)
core_dir = Path(__file__).parent.parent / "core"
sys.path.append(str(core_dir))

# Import directly as a module
from render_vue import ContentRenderer

def verify_ui():
    renderer = ContentRenderer()
    
    # Test with new format: # title + <summary> + normal h2 sections + trailing table section
    markdown_content = """# 机动战士高达 00

<summary>《机动战士高达 00》是日升动画制作的原创电视动画，以西元 2307 年为背景，讲述私立武装组织"天人"通过高达武力介入，试图根除战争并推动人类相互理解的故事。</summary>

## 作品概览
《机动战士高达 00》（日语：機動戦士ガンダム 00）是"高达"系列的第 11 部原创电视动画作品 [1]。该作由 **SUNRISE** 制作，首次采用高清（16:9）格式播出，并分为两季进行放映 [2][3]：
*   **第一季 (Season 1)：** 2007年10月6日 — 2008年3月29日（全25话）[2]。
*   **第二季 (Season 2)：** 2008年10月5日 — 2009年3月29日（全25话）[2]。
*   **剧场版：** 《机动战士高达00 -先驱者的觉醒-》于 2010 年 9 月 18 日上映，标志着故事的完结 [2][4]。

## 世界观与背景
故事设定在 **西元 (Anno Domini, A.D.) 2307 年** 的地球 [5]。当时化石燃料枯竭，人类依赖由三条巨大的**轨道电梯**组成的太阳能发电系统 [5]。世界被三大超级大国群割据：
1.  **Union**（太阳能与自由领地联合）：以美国为首 [5]。
2.  **人革联**（人类革新联盟）：以中国、俄罗斯、印度为首 [5]。
3.  **AEU**（新欧盟）：以欧洲国家为首 [5]。

![机动战士高达00 视觉图](https://tse1.mm.bing.net/th/id/OIP.od2s9dPt50Nzap6sLexinQHaKY?pid=Api)

## 核心主题
《高达 00》不仅展示了宏大的机甲战斗，更深入探讨了**"对话"**与**"相互理解"**的重要性 [4]。
*   **GN 粒子：** 这种半永久性能源不仅是动力源，在后期更成为人类意识沟通的媒介 [4]。
*   **变革者 (Innovator)：** 随着故事发展，人类开始向进化的新阶段迈进 [4]。

## 播放时间表
| 季度 | 开始日期 | 结束日期 | 集数 | 备注 |
| :--- | :--- | :--- | :---: | :--- |
| **第一季** | 2007-10-06 | 2008-03-29 | 25 | 高清首播 |
| **第二季** | 2008-10-05 | 2009-03-29 | 25 | 完结篇 |
| **剧场版** | 2010-09-18 | - | 1 | 先驱者的觉醒 |

## 代码示例
```python
def gundam_exia():
    print("Gundam Exia, Setsuna F. Seiei, eliminating targets.")
    return "Mission Complete"
```"""
    
    stages = [
        {"name": "search", "model": "duckduckgo", "time": 1.2, "references": [
            {"title": "Gundam 00 Wiki", "url": "https://gundam.fandom.com/wiki/Mobile_Suit_Gundam_00"},
            {"title": "豆瓣电影", "url": "https://movie.douban.com/subject/2286663/"},
            {"title": "Bangumi", "url": "https://bgm.tv/subject/2585"}
        ]},
        {"name": "crawler", "model": "crawl4ai", "time": 2.5, "crawled_pages": [
            {"title": "机动战士高达00 官方网站", "url": "http://www.gundam00.net/"},
             {"title": "Wikipedia Entry", "url": "https://en.wikipedia.org/wiki/Mobile_Suit_Gundam_00"}
        ]},
        {"name": "agent", "model": "gpt-4o", "time": 3.8, "cost": 0.002, "references": [{"title": "Agent Source", "url": "internal"}]}
    ]
    
    output_path = Path(__file__).parent / "ui_test_output.jpg"
    print(f"🎨 Rendering to {output_path}...")
    
    try:
        # Since render is async, we need to run it in an event loop
        async def run_render():
            await renderer.render(
                markdown_content=markdown_content,
                output_path=str(output_path),
                stats={"total_time": 8.5},
                stages_used=stages,
                references=[{"title": f"Ref {i}", "url": "http://example.com"} for i in range(5)],
                page_references=[{"title": f"Page {i}", "url": "http://example.com"} for i in range(2)],
                image_references=[]
            )
            
        asyncio.run(run_render())
        print(f"✨ Success! Image saved to: {output_path}")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_ui()

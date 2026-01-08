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
    
    # Test with real GTNH conversation data
    markdown_content = """# 终极硬核整合包格雷科技新视野

<summary>
《格雷科技：新视野》（GregTech: New Horizons，简称 GTNH）是一款基于 Minecraft 1.7.10 版本的深度硬核科技向整合包。它以 GregTech 5 Unofficial 为核心，通过超过 8 年的持续开发，将 300 多个模组深度集成，构建了极其严苛且逻辑严密的科技树，是公认的生存挑战巅峰之作。
</summary>

## 核心机制与游戏体验
GTNH 的核心在于"格雷化"改造，几乎所有模组的合成表都经过重新设计，以匹配其严苛的阶级制度 [4][8]。玩家需要从原始的石器时代开始，历经蒸汽时代、电力时代，最终向星际航行迈进。其游戏过程极其漫长，旨在让玩家在每一毫秒的进度中感受工业发展的成就感 [3][7]。

![GTNH 游戏场景](https://i.ytimg.com/vi/5T-oSWAgaMM/maxresdefault.jpg)

## 科技阶层与任务系统
整合包拥有 15 个清晰的科技等级（Tiers），最终目标是建造"星门"（Stargate）[2]。为了引导玩家不迷失在复杂的工业流程中，GTNH 内置了超过 3900 条任务的巨型任务书，涵盖了从基础生存到高阶多方块结构的详细指导 [4][7]。

- 15 个科技等级
    - 任务数量：3900+
    - 最终目标：建造"星门"

> 机动战士高达系列是日本动画史上最具影响力的动画作品之一，深受全球观众的喜爱。

| 特性 | 详细描述 |
| :--- | :--- |
| **基础版本** | Minecraft 1.7.10 (高度优化) |
| **任务数量** | 3900+ 任务引导 [7] |
| **科技阶层** | 15 个技术等级 [2] |
| **核心模组** | GregTech 5 Unofficial, Thaumcraft 等 [8] |

## 安装与运行建议
由于其高度集成的特性，官方强烈建议使用 **Prism Launcher** 进行安装和管理 [5]。在运行环境方面，虽然基于旧版 MC，但通过社区努力，目前推荐使用 **Java 17-25** 版本以获得最佳的内存管理和性能优化，确保大型自动化工厂运行流畅 [5]。

```bash
curl -s https://raw.githubusercontent.com/GTNewHorizons/GT-New-Horizons-Modpack/master/README.md
java -version
java -Xmx1024M -Xms1024M -jar prism-launcher.jar
```
"""
    
    stages = [
        {
            "name": "instruct",
            "status": "completed",
            "cost": 0.0002,
            "time": 1.83,
            "model": "qwen/qwen3-235b-a22b-2507",
            "description": "Planning search strategy"
        },
        {
            "name": "search",
            "status": "completed",
            "cost": 0.0,
            "time": 0.5,
            "references": [
                {"title": "GTNH 2025 Server Information", "url": "https://stonelegion.com/mc-gtnh-2026/gtnh-2025-server-information-including-client-download/"},
                {"title": "GT New Horizons Wiki", "url": "https://gtnh.miraheze.org/wiki/Main_Page"},
                {"title": "GT New Horizons - GitHub", "url": "https://github.com/GTNewHorizons/GT-New-Horizons-Modpack"},
                {"title": "GT New Horizons - CurseForge", "url": "https://www.curseforge.com/minecraft/modpacks/gt-new-horizons"},
                {"title": "Installing and Migrating - GTNH", "url": "https://gtnh.miraheze.org/wiki/Installing_and_Migrating"},
                {"title": "Modlist - GT New Horizons", "url": "https://wiki.gtnewhorizons.com/wiki/Modlist"},
                {"title": "GregTech: New Horizons - Home", "url": "https://www.gtnewhorizons.com/"},
                {"title": "GT New Horizons - FTB Wiki", "url": "https://ftb.fandom.com/wiki/GT_New_Horizons"}
            ],
            "image_references": [
                {
                    "title": "GTNH Live Lets Play",
                    "url": "https://i.ytimg.com/vi/5T-oSWAgaMM/maxresdefault.jpg", 
                    "thumbnail": "https://tse4.mm.bing.net/th/id/OIP.b_56VnY4nyrzeqp1JetmFQHaEK?pid=Api"
                },
                {
                    "title": "GTNH Modpack Cover",
                    "url": "https://i.mcmod.cn/modpack/cover/20240113/1705139595_29797_dSkE.jpg",
                    "thumbnail": "https://tse1.mm.bing.net/th/id/OIP.KNKaZX1d_4Ueq6vpl1qJNAHaEo?pid=Api"
                },
                {
                    "title": "GTNH Steam Age",
                    "url": "https://i.ytimg.com/vi/8IPwXxqB71w/maxresdefault.jpg",
                    "thumbnail": "https://tse4.mm.bing.net/th/id/OIP.P-KrnI4GBH21yPgwpNPSzAHaEK?pid=Api"
                },
                {
                    "title": "GTNH MCMod Cover",
                    "url": "https://i.mcmod.cn/post/cover/20230201/1675241030_2_VqDc.jpg",
                    "thumbnail": "https://tse2.mm.bing.net/th/id/OIP.GvYz7YWrg-fnpAHjOiW3OAHaEo?pid=Api"
                },
                {
                    "title": "GTNH Tectech Tutorial",
                    "url": "http://i0.hdslb.com/bfs/archive/1ed1e53341fd44018138f2823b2fe6c499fb9c9c.jpg",
                    "thumbnail": "https://tse4.mm.bing.net/th/id/OIP.0Wg7xFHTjhxIV9hKuUo4xwHaEo?pid=Api"
                }
            ]
        },
        {
            "name": "agent",
            "status": "completed",
            "cost": 0.0018,
            "time": 13.0,
            "model": "google/gemini-3-flash-preview",
            "description": "Synthesizing information..."
        }
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
                references=[{"title": f"Ref {i}", "url": "http://example.com"} for i in range(10)],
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

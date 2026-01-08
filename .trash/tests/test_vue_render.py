"""Quick test for the new Vue renderer."""
import asyncio
from pathlib import Path

# Add parent to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.render_vue import ContentRenderer


async def main():
    renderer = ContentRenderer()
    
    test_data = {
        "markdown_content": """# 测试渲染器

这是一个测试，包含 **粗体** 和 *斜体* 文字。

## 代码示例

```python
def hello():
    print("Hello World!")
```

## 数学公式

行内公式: $E = mc^2$

块级公式:

$$
\\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}
$$

## 引用测试

这里有一个引用 [1] 和另一个 [2]。

还有页面引用 [3]。
""",
        "stages_used": [
            {
                "name": "Agent",
                "model": "google/gemini-2.0-flash-exp",
                "provider": "OpenRouter",
                "time": 1.5,
                "cost": 0.001,
                "references": [
                    {"title": "Example Site", "url": "https://example.com"},
                    {"title": "GitHub", "url": "https://github.com"},
                ],
            },
            {
                "name": "Search",
                "model": "DuckDuckGo",
                "provider": "",
                "time": 0.5,
                "cost": 0,
            },
        ],
        "references": [
            {"title": "Example Site", "url": "https://example.com"},
            {"title": "GitHub", "url": "https://github.com"},
        ],
        "page_references": [
            {"title": "Python Docs", "url": "https://docs.python.org"},
        ],
        "stats": {"total_time": 2.5},
    }
    
    output_path = Path(__file__).parent.parent.parent.parent / "data" / "cache" / "vue_test_output.jpg"
    
    success = await renderer.render(
        markdown_content=test_data["markdown_content"],
        output_path=str(output_path),
        stages_used=test_data["stages_used"],
        references=test_data["references"],
        page_references=test_data["page_references"],
        stats=test_data["stats"],
    )
    
    if success:
        print(f"✅ Render successful: {output_path}")
    else:
        print("❌ Render failed")


if __name__ == "__main__":
    asyncio.run(main())

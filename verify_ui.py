import os
import sys
# Add the core directory to sys.path to import render.py directly
sys.path.append(os.path.join(os.getcwd(), "src", "entari_plugin_hyw", "core"))
import render as render_module

async def main():
    renderer = render_module.ContentRenderer()
    
    markdown_content = """
# Hello World
This is a test of the new UI.

- Item 1
- Item 2
"""
    suggestions = ["Suggestion 1", "Suggestion 2", "Suggestion 3"]
    stats = {
        "time": 1.5,
        "vision_duration": 0.5,
        "tool_calls_count": 2
    }
    references = [
        {"title": "Google", "url": "https://google.com"},
        {"title": "OpenAI", "url": "https://openai.com"}
    ]
    
    output_path = "verify_ui_output.png"
    
    print(f"Generating image to {output_path}...")
    await renderer.render(
        markdown_content=markdown_content,
        output_path=output_path,
        suggestions=suggestions,
        stats=stats,
        references=references,
        model_name="gpt-4o",
        search_provider="google_search",
        turns=3,
        session_id="test-session"
    )
    print("Done!")

if __name__ == "__main__":
    asyncio.run(main())

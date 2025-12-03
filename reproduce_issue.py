import asyncio
import os
import sys

# Add the directory containing render.py to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
render_dir = os.path.join(current_dir, "src", "entari_plugin_hyw", "core")
sys.path.insert(0, render_dir)

from render import ContentRenderer

async def test_render_broken_image():
    renderer = ContentRenderer()
    
    # Markdown with a broken image
    markdown_content = """
# Test Broken Image

Here is a broken image:
![Broken Image](https://example.com/nonexistent.png)

Here is a working image (logo):
![Google Logo](https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_272x92dp.png)
    """
    
    output_path = "test_broken_image.png"
    if os.path.exists(output_path):
        os.remove(output_path)
        
    print("Rendering...")
    try:
        await renderer.render(
            markdown_content=markdown_content,
            output_path=output_path,
            model_name="test-model",
            session_id="test-session"
        )
        print(f"Rendered to {output_path}")
        
        if os.path.exists(output_path):
            print("Success: Output file created.")
        else:
            print("Failure: Output file not created.")
            
    except Exception as e:
        print(f"Error during rendering: {e}")

if __name__ == "__main__":
    asyncio.run(test_render_broken_image())

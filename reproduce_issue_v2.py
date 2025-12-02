
import markdown
import re

def test_render(text):
    # Simulate the preprocessing in render.py
    # Fix lists
    text = re.sub(r'(?m)^(?<=\S)\n(?=\s*(\d+\.|[-*+]) )', r'\n\n', text)
    
    # Render
    html = markdown.markdown(
        text.strip(), 
        extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']
    )
    return html

# Case 4: Trailing spaces on previous line
text4 = "Some text  \n#### Title"
print(f"--- Case 4 (Trailing spaces) ---\nInput:\n{text4.replace(' ', '.')}\nOutput:\n{test_render(text4)}\n")

# Case 5: No newline (Inline)
text5 = "Some text#### Title"
print(f"--- Case 5 (Inline) ---\nInput:\n{text5}\nOutput:\n{test_render(text5)}\n")

# Case 6: Proposed fix with regex
def test_render_fixed(text):
    # Fix lists
    text = re.sub(r'(?m)^(?<=\S)\n(?=\s*(\d+\.|[-*+]) )', r'\n\n', text)
    # Fix headers
    text = re.sub(r'(?m)^(?<=\S)\n(?=#{1,6} )', r'\n\n', text)
    
    html = markdown.markdown(
        text.strip(), 
        extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']
    )
    return html

print(f"--- Case 4 Fixed ---\nInput:\n{text4.replace(' ', '.')}\nOutput:\n{test_render_fixed(text4)}\n")

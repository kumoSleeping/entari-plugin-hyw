
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

# Case 1: Header with blank line (Should work)
text1 = "Some text\n\n#### Title"
print(f"--- Case 1 ---\nInput:\n{text1}\nOutput:\n{test_render(text1)}\n")

# Case 2: Header without blank line (Suspected failure)
text2 = "Some text\n#### Title"
print(f"--- Case 2 ---\nInput:\n{text2}\nOutput:\n{test_render(text2)}\n")

# Case 3: Proposed fix
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

print(f"--- Case 3 (Fixed) ---\nInput:\n{text2}\nOutput:\n{test_render_fixed(text2)}\n")

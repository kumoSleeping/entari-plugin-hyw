
import markdown
import re

def render_html(text):
    return markdown.markdown(
        text.strip(), 
        extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']
    )

# Case 1: Wrapped in markdown code block
text1 = """```markdown
#### Title
Some content
```"""
print(f"--- Case 1 (Wrapped) ---\nInput:\n{text1}\nOutput:\n{render_html(text1)}\n")

# Case 2: Wrapped in generic code block
text2 = """```
#### Title
Some content
```"""
print(f"--- Case 2 (Generic Wrapped) ---\nInput:\n{text2}\nOutput:\n{render_html(text2)}\n")

# Case 3: Missing newlines (re-verify)
text3 = "Content\n#### Title"
print(f"--- Case 3 (Missing Newline) ---\nInput:\n{text3}\nOutput:\n{render_html(text3)}\n")


import re
import json

class MockPipeline:
    def __init__(self):
        self.all_web_results = [
            {"_id": 1, "_type": "image", "title": "Poster", "url": "http://img.com/1.jpg", "thumbnail": "http://img.com/t1.jpg"},
            {"_id": 2, "_type": "search", "title": "Repo One", "url": "http://repo.com/1", "domain": "repo.com"},
            {"_id": 4, "_type": "search", "title": "Wiki", "url": "http://wiki.com", "domain": "wiki.com"},
        ]

    def _parse_tagged_response(self, text: str):
        parsed = {"response": "", "references": [], "page_references": [], "image_references": [], "flow_steps": []}
        if not text:
            return parsed

        remaining_text = text
        
        # 2. Extract references from text first (Order by appearance)
        pattern = re.compile(r'\[(search|page|image):(\d+)\]', re.IGNORECASE)
        
        matches = list(pattern.finditer(remaining_text))
        
        search_map = {}  # old_id_str -> new_id (int)
        page_map = {}
        image_map = {}
        
        def process_ref(tag_type, old_id):
            result_item = next((r for r in self.all_web_results if r.get("_id") == old_id and r.get("_type") == tag_type), None)
            
            if not result_item:
                return
                
            entry = {
                "title": result_item.get("title", ""),
                "url": result_item.get("url", ""),
                "domain": result_item.get("domain", "")
            }
            if tag_type == "image":
                 entry["thumbnail"] = result_item.get("thumbnail", "")

            if tag_type == "search":
                if str(old_id) not in search_map:
                    parsed["references"].append(entry)
                    search_map[str(old_id)] = len(parsed["references"])
            elif tag_type == "page":
                if str(old_id) not in page_map:
                    parsed["page_references"].append(entry)
                    page_map[str(old_id)] = len(parsed["page_references"])
            elif tag_type == "image":
                if str(old_id) not in image_map:
                    parsed["image_references"].append(entry)
                    image_map[str(old_id)] = len(parsed["image_references"])

        # Pass 1: Text Body
        for m in matches:
            try:
                process_ref(m.group(1).lower(), int(m.group(2)))
            except ValueError:
                continue

        # 3. Pass 2: References Block (Capture items missed in text)
        ref_block_match = re.search(r'```references\s*(.*?)\s*```', remaining_text, re.DOTALL | re.IGNORECASE)
        if ref_block_match:
            ref_content = ref_block_match.group(1).strip()
            # print(f"DEBUG: Found block: {ref_content}")
            
            for line in ref_content.splitlines(): # using splitlines() is safer
                line = line.strip()
                if not line: continue
                
                # Check for [id] [type] format
                id_match = re.match(r"^\[(\d+)\]\s*\[(search|page|image)\]", line, re.IGNORECASE)
                if id_match:
                    try:
                         # print(f"DEBUG: Processing {id_match.groups()}")
                         process_ref(id_match.group(2).lower(), int(id_match.group(1)))
                    except ValueError:
                        pass
                else:
                    alt_match = re.match(r"^\[(search|page|image):(\d+)\]", line, re.IGNORECASE)
                    if alt_match:
                        try:
                            process_ref(alt_match.group(1).lower(), int(alt_match.group(2)))
                        except ValueError:
                            pass

        return parsed

pipeline = MockPipeline()
text = """
Some text with [search:2].
![Image](http://img.com/1.jpg)

```references
[2] [search] [Repo One](http://repo.com/1)
[4] [search] [Wiki](http://wiki.com)
[1] [image] [Poster](http://img.com/1.jpg)
```
"""

result = pipeline._parse_tagged_response(text)
print(json.dumps(result, indent=2, ensure_ascii=False))

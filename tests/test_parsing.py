
import sys
import os
import re
from typing import Dict, Any

# Standalone version of the parsing logic to verify correctness without complex dependencies
def _parse_tagged_response(content: str) -> Dict[str, Any]:
    """Parse response with ===section=== tags"""
    structured = {
        "response": "",
        "suggestion": [],
        "references": [],
        "tun": ""
    }
    
    try:
        # Extract sections
        sections = {}
        # FIX APPLIED HERE: Initialize to "response"
        current_section = "response" 
        current_content = []
        
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('===') and line.endswith('==='):
                if current_section:
                    sections[current_section] = '\n'.join(current_content).strip()
                current_section = line.strip('=')
                current_content = []
            elif line == "===START===" or line == "===END===":
                continue
            else:
                if current_section:
                    current_content.append(line)
        
        # Capture last section
        if current_section:
            sections[current_section] = '\n'.join(current_content).strip()
            
        # Map to structured fields
        if "response" in sections:
            structured["response"] = sections["response"]
        elif not sections:
            structured["response"] = content
        else:
            pass

        # Parse suggestion (support both for backward compatibility)
        spec_text = sections.get("suggestion") or sections.get("speculation", "")
        if spec_text:
            specs = []
            for line in spec_text.split('\n'):
                # Remove list markers like "1. ", "- "
                cleaned = re.sub(r'^(\d+\.|-|\*)\s*', '', line).strip()
                if cleaned:
                    specs.append(cleaned)
            structured["suggestion"] = specs
            
        # Parse references
        ref_text = sections.get("references", "")
        if ref_text:
            refs = []
            # Match markdown links: [Title](url)
            matches = re.findall(r'\[(.*?)\]\((.*?)\)', ref_text)
            for title, url in matches:
                refs.append({"title": title, "url": url})
            structured["references"] = refs
            
        # Parse tun
        structured["tun"] = sections.get("tun", "")
        
    except Exception as e:
        print(f"Failed to parse tagged response: {e}")
        structured["response"] = content
        
    return structured

def test_parsing():
    # Case 1: Standard Format
    case1 = """===START===
===response===
This is the main response.
===suggestion===
1. Suggestion 1
2. Suggestion 2
===references===
1. [Ref 1](http://example.com)
===END==="""
    
    # Case 2: Missing Start Tags (The issue to fix)
    case2 = """This is the main response without tags.
It has multiple lines.

===suggestion===
1. Suggestion 1
===references===
1. [Ref 1](http://example.com)"""

    # Case 3: Only Content
    case3 = """Just some content."""

    print("--- Case 1: Standard ---")
    res1 = _parse_tagged_response(case1)
    print(f"Response: {res1['response']}")
    print(f"Suggestion: {res1['suggestion']}")
    
    print("\n--- Case 2: Missing Start Tags ---")
    res2 = _parse_tagged_response(case2)
    print(f"Response: {res2['response']}") 
    print(f"Suggestion: {res2['suggestion']}")

    print("\n--- Case 3: Only Content ---")
    res3 = _parse_tagged_response(case3)
    print(f"Response: {res3['response']}")

if __name__ == "__main__":
    test_parsing()

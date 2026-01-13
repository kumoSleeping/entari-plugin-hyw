import asyncio
import os
import sys
from unittest.mock import MagicMock, AsyncMock

# Add src to path
sys.path.append(os.path.abspath("src"))

from entari_plugin_hyw.pipeline import ProcessingPipeline
from entari_plugin_hyw import HywConfig

async def main():
    print("Initializing Pipeline...")
    config = HywConfig(
        model_name="mock-model",
        api_key="mock-key",
        instruct_model_name="mock-instruct",
        fetch_model_name="mock-fetch",
    )
    
    pipeline = ProcessingPipeline(config)
    
    # Mock SearchService
    pipeline.search_service.search = AsyncMock(return_value=[
        {"title": "Test Result 1", "url": "http://example.com/1", "content": "Snippet 1"},
        {"title": "Test Result 2", "url": "http://example.com/2", "content": "Snippet 2"},
    ])
    pipeline.search_service.fetch_page = AsyncMock(return_value={
        "title": "Fetched Page",
        "url": "http://example.com/1",
        "content": "Full content of page 1 including image: ![img](http://img.com/1.png)",
        "images": ["http://img.com/1.png"]
    })
    
    # Mock Client
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Mocked Response", tool_calls=None))]
    mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=10)
    
    # For instruct stage, we want it to return tool calls for search
    instruct_response = MagicMock()
    instruct_response.choices = [MagicMock(message=MagicMock(content="", tool_calls=[
        MagicMock(id="call_1", function=MagicMock(name="web_search", arguments='{"query": "test query"}'))
    ]))]
    instruct_response.usage = MagicMock(prompt_tokens=10, completion_tokens=10)
    
    # For fetch decision, use tool calls
    msg = MagicMock(content="", tool_calls=[
        MagicMock(id="call_fetch", function=MagicMock(name="crawl_page", arguments='{"url": "http://example.com/1"}'))
    ])
    fetch_resp = MagicMock()
    fetch_resp.choices = [MagicMock(message=msg)]
    fetch_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=10)
    
    # For summary
    summary_resp = MagicMock()
    summary_resp.choices = [MagicMock(message=MagicMock(content="Final Summary with [1]", tool_calls=None))]
    summary_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=10)
    
    pipeline.client.chat.completions.create = AsyncMock(side_effect=[
        instruct_response, # Instruct
        fetch_resp,        # Fetch Decision
        summary_resp       # Summary
    ])
    
    print("Executing Pipeline...")
    result = await pipeline.execute("Hello World", [])
    
    print("Result Keys:", result.keys())
    print("LLM Response:", result["llm_response"])
    print("Structured Response:", result["structured_response"])
    print("Stages Used:", len(result["stages_used"]))
    for stage in result["stages_used"]:
        print(f" - {stage['name']} ({stage.get('provider')})")
        if "crawled_pages" in stage:
            print(f"   Crawled: {len(stage['crawled_pages'])}")
        if "image_references" in stage:
            print(f"   Images: {len(stage['image_references'])}")

    await pipeline.close()

if __name__ == "__main__":
    asyncio.run(main())

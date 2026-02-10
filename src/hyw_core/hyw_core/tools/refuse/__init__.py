from ...models import ToolResult

async def refuse(reason: str) -> ToolResult:
    """Use this tool to refuse answering if the query is harmful or illegal."""
    return ToolResult(content=f"Refused: {reason}", should_finish=True)

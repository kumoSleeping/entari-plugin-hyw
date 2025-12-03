import asyncio
from unittest.mock import MagicMock, AsyncMock
import sys
import os

# Add src to path
sys.path.append(os.path.abspath("src"))

# Mock arclet and satori packages
sys.modules["arclet"] = MagicMock()
sys.modules["arclet.alconna"] = MagicMock()
# Define BasicConfModel as a real class for dataclass inheritance
class BasicConfModel:
    pass
# Mock arclet.entari and other dependencies before importing plugin
sys.modules["arclet.entari"] = MagicMock()
sys.modules["arclet.entari"].BasicConfModel = BasicConfModel

sys.modules["arclet.letoderea"] = MagicMock()
sys.modules["arclet.entari.event"] = MagicMock()
sys.modules["arclet.entari.event.command"] = MagicMock()
sys.modules["arclet.entari.event.lifespan"] = MagicMock()

sys.modules["satori"] = MagicMock()
sys.modules["satori.element"] = MagicMock()
sys.modules["satori.exception"] = MagicMock()
sys.modules["satori.adapters"] = MagicMock()
sys.modules["satori.adapters.onebot11"] = MagicMock()
sys.modules["satori.adapters.onebot11.reverse"] = MagicMock()

sys.modules["loguru"] = MagicMock()

# Mock internal modules
sys.modules["entari_plugin_hyw.core.hyw"] = MagicMock()
sys.modules["entari_plugin_hyw.core.history"] = MagicMock()
sys.modules["entari_plugin_hyw.core.render"] = MagicMock()
sys.modules["entari_plugin_hyw.utils.misc"] = MagicMock()

# Now import
from entari_plugin_hyw import process_request, conf, history_manager, renderer, hyw, next_alc
from loguru import logger

async def test_process_request_next_option():
    print("Starting test_process_request_next_option...")
    
    # Setup Mocks
    session = MagicMock()
    session.reply = None
    session.guild.id = "test_guild"
    session.user.id = "test_user"
    session.event.message.id = "msg_123"
    session.send = AsyncMock(return_value=[MagicMock(id="sent_123")])
    
    # Mock MessageChain
    all_param = MagicMock()
    
    # Mock process_images
    from entari_plugin_hyw.utils.misc import process_images
    process_images.side_effect = AsyncMock(return_value=([], None))
    
    # Mock MessageChain behavior
    text_elem = MagicMock()
    text_elem.__str__.return_value = "test input"
    
    # Configure logger to print
    logger.info.side_effect = print
    logger.warning.side_effect = print
    logger.error.side_effect = print
    logger.exception.side_effect = print
    
    # Mock hyw.agent to return different results for step 1 and step 2
    step1_resp = {
        "llm_response": "Step 1 Response",
        "conversation_history": [{"role": "user", "content": "input"}, {"role": "assistant", "content": "Step 1 Response"}],
        "stats": {"time": 1.0, "vision_duration": 0.5},
        "structured_response": {},
        "model_used": "gpt-4-vision",
        "vision_model_used": "gpt-4-vision"
    }
    
    step2_resp = {
        "llm_response": "Step 2 Response",
        "conversation_history": [{"role": "user", "content": "input"}, {"role": "assistant", "content": "Step 1 Response"}, {"role": "user", "content": "next"}, {"role": "assistant", "content": "Step 2 Response"}],
        "stats": {"time": 0.5, "tool_calls_count": 2},
        "structured_response": {},
        "model_used": "gpt-3.5-turbo",
        "vision_model_used": None 
    }
    
    hyw.agent = AsyncMock(side_effect=[step1_resp, step2_resp])
    
    # Mock renderer
    renderer.render = AsyncMock()
    
    # Mock history manager
    history_manager.get_history.return_value = []
    history_manager.get_metadata.return_value = {}
    history_manager.generate_short_code.return_value = "ABCD"
    
    # Mock conf
    conf.models = [{"name": "gpt-4-vision", "icon": "vision_icon"}, {"name": "gpt-3.5-turbo", "icon": "text_icon"}]
    conf.icon = "default_icon"
    conf.reaction = False
    conf.quote = False
    conf.save_conversation = False
    
    # Run process_request with next_prompt
    await process_request(
        session, 
        all_param, 
        selected_model="gpt-4-vision", 
        selected_vision_model="gpt-4-vision",
        next_prompt="Analyze this text",
        next_text_model="gpt-3.5-turbo"
    )
    
    # Verify hyw.agent calls
    print(f"hyw.agent call count: {hyw.agent.call_count}")
    assert hyw.agent.call_count == 2
    
    # Verify renderer call
    print(f"renderer.render call count: {renderer.render.call_count}")
    assert renderer.render.call_count == 1
    
    render_kwargs = renderer.render.call_args[1]
    
    # Check if stats is a list
    stats_arg = render_kwargs.get("stats")
    print(f"Stats arg type: {type(stats_arg)}")
    print(f"Stats arg: {stats_arg}")
    
    assert isinstance(stats_arg, list)
    assert len(stats_arg) == 2
    assert stats_arg[0] == step1_resp["stats"]
    # step2_resp["stats"] is modified in process_request (replaced by merged stats)
    # So we compare against the original values we defined
    expected_step2_stats = {"time": 0.5, "tool_calls_count": 2}
    assert stats_arg[1] == expected_step2_stats
    
    print("Test Passed!")

if __name__ == "__main__":
    try:
        asyncio.run(test_process_request_next_option())
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()

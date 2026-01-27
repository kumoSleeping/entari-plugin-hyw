from hyw_core.browser_control.service import get_screenshot_service, close_screenshot_service
import asyncio

async def test_js():
    service = get_screenshot_service(headless=True)
    
    # Test 1: console.log (returns undefined/None)
    print("Test 1: console.log")
    script1 = "console.log(12345)"
    res1 = await service.execute_script(script1)
    print(f"Result 1: {res1}")

    # Test 2: Return value
    print("\nTest 2: Return value")
    script2 = "return 12345"
    res2 = await service.execute_script(script2)
    print(f"Result 2: {res2}")
    
    # Test 3: Math expression (implicit return?)
    print("\nTest 3: Math expression")
    script3 = "123 + 456"
    res3 = await service.execute_script(script3)
    print(f"Result 3: {res3}")

    await close_screenshot_service()

if __name__ == "__main__":
    asyncio.run(test_js())

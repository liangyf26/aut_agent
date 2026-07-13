import asyncio
from browser_use import Browser

async def test():
    browser = Browser(cdp_url='http://localhost:9222', headless=False, keep_alive=True)
    # Navigate first so there's a page
    await browser.start()
    page = await browser.get_current_page()
    print('page:', type(page).__name__)
    
    # Test arrow function format (what browser-use 0.13.4 expects)
    result = await page.evaluate("() => { return 1+1; }")
    print('Test 1:', result)
    
    result = await page.evaluate("() => { var x = {a:1}; return JSON.stringify(x); }")
    print('Test 2:', result)

asyncio.run(test())
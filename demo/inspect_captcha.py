import asyncio
from browser_use import Browser, ChatOpenAI

async def test():
    browser = Browser(cdp_url='http://localhost:9222', headless=False, keep_alive=True)
    await browser.navigate_to('https://www.zbsykj.com:19096/login?redirect=%2Findex')
    page = await browser.get_current_page()
    await asyncio.sleep(2)
    
    # Click 密码登录
    result = await page.evaluate("() => { var btns = document.querySelectorAll('button'); for (var b of btns) { if (b.textContent.includes('密码登录')) { b.click(); return 'clicked: ' + b.textContent.trim(); } } return 'not found'; }")
    print('click:', result)
    await asyncio.sleep(2)
    
    # Inspect the captcha image
    result = await page.evaluate("() => { var img = document.querySelector('img[src*=captcha], img[alt*=验证码], img[title*=验证码]'); if (!img) { var allImgs = document.querySelectorAll('img'); var list = []; for (var i of allImgs) list.push({src:i.src, alt:i.alt, title:i.title, w:i.offsetWidth, h:i.offsetHeight}); return JSON.stringify(list); } return JSON.stringify({src:img.src, alt:img.alt, title:img.title, w:img.offsetWidth, h:img.offsetHeight}); }")
    print('captcha:', result)
    
    # Inspect captcha area HTML
    result = await page.evaluate("() => { var captchaInputs = document.querySelectorAll('input[placeholder*=验证码]'); var info = []; for (var inp of captchaInputs) { var parent = inp.parentElement; info.push({placeholder:inp.placeholder, parentHTML:parent ? parent.outerHTML.substring(0, 1000) : null}); } return JSON.stringify(info); }")
    print('captcha inputs:', result)

asyncio.run(test())
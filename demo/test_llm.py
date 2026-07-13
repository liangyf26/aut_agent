import requests
from openai import OpenAI

# 先截图验证码
import asyncio
from browser_use import Browser
import base64, io
from PIL import Image

async def test():
    browser = Browser(cdp_url='http://localhost:9222', headless=False, keep_alive=True)
    # 先导航到登录页
    await browser.navigate_to('https://www.zbsykj.com:19096/login?redirect=%2Findex')
    await asyncio.sleep(3)
    page = await browser.get_current_page()
    print('page:', page)
    
    # 切到密码登录
    await page.evaluate("() => { var btns = document.querySelectorAll('button, a'); for (var b of btns) { var t = (b.textContent || '').replace(/\\s+/g, ''); if (t.indexOf('密码登录') > -1 && b.offsetParent !== null) { b.click(); return 'clicked'; } } return 'not found'; }")
    await asyncio.sleep(1.5)
    
    # 截图
    full_b64 = await page.screenshot(format='png')
    img = Image.open(io.BytesIO(base64.b64decode(full_b64)))
    img.save(r'c:\project\aut_agent\demo\test_login.png')
    print('Full screenshot saved, size:', img.size)
    
    # 调用 LLM
    client = OpenAI(api_key='123456', base_url='http://172.28.50.9:30030/v1', timeout=30)
    resp = client.chat.completions.create(
        model='qwen3.6-27b',
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "这是一张数学算式验证码图片，例如 3+5=? 形式。只输出算式，不要其他文字。"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{full_b64}"}},
            ],
        }],
        max_tokens=50,
    )
    print('LLM response:', repr(resp.choices[0].message.content))

asyncio.run(test())
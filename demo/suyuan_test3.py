"""
suyuan_test3.py - Browser-Use 全流程自动化测试脚本
1. 找到"线上备案申请"，点击进入
2. 找到"申请备案"按钮，点击进入申请表
3. 按流程一步步往下走，每新页面截图
4. 模拟填写表单，提交
5. 如有错误，截图并重点加粗记录到 LOG
6. 同一个动作失败 6 次后停止
"""
import sys

# 修复 Windows 控制台中文显示乱码问题
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import asyncio
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from browser_use import Agent, Browser, ChatOpenAI, ActionResult, Tools

load_dotenv()

# ─── 配置 ──────────────────────────────────────────────────────
LOCAL_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL")
LOCAL_API_KEY = os.getenv("LOCAL_LLM_API_KEY")
LOCAL_MODEL = os.getenv("LOCAL_LLM_MODEL")

CDP_URL = "http://localhost:9222"
TARGET_URL = "https://www.zbsykj.com:19096/"

# 输出目录
OUTPUT_DIR = Path(__file__).parent.parent  # browser-use → 上级 bowuse
PICTURE_DIR = OUTPUT_DIR / "pic"
# 时间戳
TIMESTAMP_FULL = datetime.now().strftime("%Y%m%d_%H%M%S")
TIMESTAMP_LOG = datetime.now().strftime("%y%m%d_%H%M")
TIMESTAMP_PIC = datetime.now().strftime("%y%m%d_%H%M")

PICTURE_PATH = PICTURE_DIR / f"pic_{TIMESTAMP_PIC}.png"
LOG_NAME = f"log_{TIMESTAMP_LOG}.md"
LOG_PATH = OUTPUT_DIR / LOG_NAME

print(f"目标: {TARGET_URL}")
print(f"调试端口: {CDP_URL}")
print(f"LLM: {LOCAL_MODEL}")
print(f"日志: {LOG_PATH}")
print(f"截图: {PICTURE_PATH}")
print()


async def main():
    # 浏览器配置
    browser = Browser(
        cdp_url=CDP_URL,
        headless=False,
        keep_alive=True,
    )

    # LLM 配置
    llm = ChatOpenAI(
        model=LOCAL_MODEL,
        api_key=LOCAL_API_KEY,
        base_url=LOCAL_BASE_URL,
        timeout=300,
    )

    PICTURE_DIR.mkdir(exist_ok=True)

    # ─── 自定义工具 ───
    tools = Tools()

    @tools.action(description="获取当前页面标题和URL。")
    async def get_page_info() -> ActionResult:
        try:
            title = await browser.get_current_page_title()
            url = await browser.get_current_page_url()
            return ActionResult(extracted_content=f"页面标题: {title}\n页面URL: {url}")
        except Exception as e:
            return ActionResult(extracted_content=f"获取页面信息失败: {str(e)}")

    @tools.action(description="截图并保存到 pic/pic_yymmdd_HHMM.png。每进入一个新页面，必须立即调用此工具。")
    async def forced_screenshot() -> ActionResult:
        try:
            await browser.take_screenshot(path=str(PICTURE_PATH), full_page=False)
            return ActionResult(extracted_content=f"截图已保存: {PICTURE_PATH}")
        except Exception as e:
            return ActionResult(extracted_content=f"截图失败: {str(e)}")

    @tools.action(description="获取当前页面的完整文本内容。用于分析页面表单、按钮和错误信息。")
    async def get_page_text() -> ActionResult:
        try:
            page = await browser.get_current_page()
            if page:
                text = await page.evaluate("(args) => { return document.body.innerText; }")
                return ActionResult(extracted_content=str(text)[:5000])
            return ActionResult(extracted_content="无法获取当前页面")
        except Exception as e:
            return ActionResult(extracted_content=f"获取页面文本失败: {str(e)}")

    @tools.action(description="获取当前页面的所有可点击元素列表。")
    async def get_clickable_elements() -> ActionResult:
        try:
            page = await browser.get_current_page()
            if page:
                js_code = """
                    (() => {
                        const buttons = Array.from(document.querySelectorAll('button, a, [role="button"], .el-menu-item, .menu-item, [class*="menu"]'))
                            .filter(el => el.offsetParent !== null && el.textContent.trim())
                            .map(el => {
                                const text = el.textContent.trim().substring(0, 100);
                                const tag = el.tagName;
                                const classes = el.className.substring(0, 100);
                                return tag + ' | ' + classes + ' | ' + text;
                            });
                        return buttons.join('\\n---\\n');
                    })()
                """
                result = await page.evaluate(js_code)
                return ActionResult(extracted_content=str(result))
            return ActionResult(extracted_content="无法获取当前页面")
        except Exception as e:
            return ActionResult(extracted_content=f"获取可点击元素失败: {str(e)}")

    # ─── 脚本内部工具函数（Agent 专用入口）───

    @tools.action(description="[脚本内部工具] 自动登录系统。脚本会调用 CDP 自动填写用户名/密码/验证码并提交登录。Agent 不需要传参数，直接调用即可。")
    async def script_login() -> ActionResult:
        """脚本自动登录（Agent 专用入口）"""
        try:
            page = await browser.get_current_page()
            if not page:
                return ActionResult(extracted_content="无法获取当前页面")

            # 检测是否在登录页，如果是则自动登录
            js_code = r"""(function(){
                var url = window.location.href;
                if (!url.includes('login') && !url.includes('Login')) {
                    return '已在主页，无需登录';
                }
                var inputs = document.querySelectorAll('input');
                var userField = null, passField = null, captchaField = null, submitBtn = null;
                for (var i = 0; i < inputs.length; i++) {
                    var inp = inputs[i];
                    var name = (inp.name || '').toLowerCase();
                    var id = (inp.id || '').toLowerCase();
                    var placeholder = (inp.placeholder || '').toLowerCase();
                    var type = (inp.type || '').toLowerCase();
                    if (type !== 'text' && type !== 'password' && type !== 'captcha') continue;
                    if (name.includes('user') || id.includes('user') || placeholder.includes('用户') || placeholder.includes('账号') || placeholder.includes('用户名')) {
                        userField = inp;
                    } else if (name.includes('pass') || id.includes('pass') || placeholder.includes('密码')) {
                        passField = inp;
                    } else if (name.includes('captcha') || id.includes('captcha') || name.includes('code') || id.includes('code') || placeholder.includes('验证码')) {
                        captchaField = inp;
                    }
                }
                // 找提交按钮
                var buttons = document.querySelectorAll('button, input[type=submit]');
                for (var j = 0; j < buttons.length; j++) {
                    var txt = (buttons[j].textContent || buttons[j].value || '').trim();
                    if (txt.includes('登') || txt.includes('登录') || txt.includes('登陆')) {
                        submitBtn = buttons[j];
                        break;
                    }
                }
                // 如果没找到文字包含"登"的按钮，用第一个可见按钮
                if (!submitBtn) {
                    buttons = document.querySelectorAll('button');
                    for (var j = 0; j < buttons.length; j++) {
                        if (buttons[j].offsetParent !== null) { submitBtn = buttons[j]; break; }
                    }
                }
                var result = {urlFound: url};
                // 填写用户名
                if (userField) {
                    userField.value = 'admin';
                    userField.dispatchEvent(new Event('input',{bubbles:true}));
                    userField.dispatchEvent(new Event('change',{bubbles:true}));
                    result.userFilled = true;
                } else {
                    result.userFilled = false;
                }
                // 填写密码
                if (passField) {
                    passField.value = 'admin123';
                    passField.dispatchEvent(new Event('input',{bubbles:true}));
                    passField.dispatchEvent(new Event('change',{bubbles:true}));
                    result.passFilled = true;
                } else {
                    result.passFilled = false;
                }
                // 填写验证码（尝试常见测试验证码）
                if (captchaField) {
                    captchaField.value = '8888';
                    captchaField.dispatchEvent(new Event('input',{bubbles:true}));
                    captchaField.dispatchEvent(new Event('change',{bubbles:true}));
                    result.captchaFilled = true;
                } else {
                    result.captchaFilled = false;
                }
                // 点击登录按钮
                if (submitBtn) {
                    submitBtn.click();
                    result.submitted = true;
                    result.submitBtn = (submitBtn.textContent || submitBtn.value || '').trim();
                } else {
                    result.submitted = false;
                    result.submitBtn = '未找到';
                }
                return JSON.stringify(result);
            })()"""
            result = await page.evaluate(js_code)
            return ActionResult(extracted_content="登录尝试: " + str(result))
        except Exception as e:
            return ActionResult(extracted_content="登录失败: " + str(e))

    @tools.action(description="[脚本内部工具] 预填写表单并勾选协议。脚本会调用 CDP 直接操作 DOM 填写所有可见表单字段，然后勾选所有协议 checkbox。Agent 不需要传参数，直接调用即可。")
    async def script_prefill_form() -> ActionResult:
        try:
            page = await browser.get_current_page()
            if not page:
                return ActionResult(extracted_content="无法获取当前页面")

            # 关闭弹窗 + 填写所有输入框 + 勾选 checkbox + 查找提交按钮
            js_code = r"""(function(){
                var results = {filled:0, errors:[], skipped:0};
                var testValues = {'text':'测试数据','number':'100','email':'test@test.com','tel':'13800138000','date':'2026-01-01','url':'https://test.com','search':'测试'};
                var inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=checkbox]):not([type=radio]), textarea, select');
                inputs.forEach(function(inp) {
                    if (inp.disabled || !inp.offsetParent) { results.skipped++; return; }
                    var t = (inp.type || '').toLowerCase();
                    var val = testValues[t] || '测试数据';
                    try {
                        inp.value = val;
                        inp.dispatchEvent(new Event('input',{bubbles:true, cancelable:true}));
                        inp.dispatchEvent(new Event('change',{bubbles:true, cancelable:true}));
                        inp.dispatchEvent(new Event('blur',{bubbles:true, cancelable:true}));
                        var ev = new CustomEvent('el-input-change',{bubbles:true});
                        inp.dispatchEvent(ev);
                        ev = new CustomEvent('el-input-input',{bubbles:true});
                        inp.dispatchEvent(ev);
                        results.filled++;
                    } catch(e) { results.errors.push(e.message); }
                });
                var cbs = document.querySelectorAll('input[type=checkbox]');
                cbs.forEach(function(cb) {
                    if (!cb.checked && !cb.disabled && cb.offsetParent) {
                        cb.click(); cb.dispatchEvent(new Event('change',{bubbles:true})); cb.dispatchEvent(new Event('input',{bubbles:true}));
                    }
                });
                window.scrollTo(0, document.body.scrollHeight);
                return JSON.stringify(results);
            })()"""
            result = await page.evaluate(js_code)
            return ActionResult(extracted_content="预填写完成: " + str(result))
        except Exception as e:
            return ActionResult(extracted_content="预填写失败: " + str(e))

    @tools.action(description="[脚本内部工具] 提交表单。脚本会调用 CDP 查找并提交表单。Agent 不需要传参数。")
    async def script_submit_form() -> ActionResult:
        try:
            page = await browser.get_current_page()
            if not page:
                return ActionResult(extracted_content="无法获取当前页面")
            js_code = r"""(function(){
                var overlays = document.querySelectorAll('.el-overlay, .el-picker-panel, .el-select-dropdown, .el-calendar, .el-popover, .el-message-box');
                for (var i = 0; i < overlays.length; i++) overlays[i].remove();
                window.scrollTo(0, document.body.scrollHeight);
                var buttons = document.querySelectorAll('button, [role=button]');
                for (var j = 0; j < buttons.length; j++) {
                    var txt = (buttons[j].textContent || '').trim();
                    if (txt && (txt.indexOf('纳入') > -1 || txt.indexOf('提交') > -1 || txt.indexOf('确定') > -1)) {
                        buttons[j].click(); return '已点击提交按钮: ' + txt;
                    }
                }
                var forms = document.querySelectorAll('form');
                for (var k = 0; k < forms.length; k++) {
                    var sb = forms[k].querySelector('button[type=submit], input[type=submit]');
                    if (sb) { sb.click(); return '已点击表单提交'; }
                }
                return '未找到提交按钮';
            })()"""
            result = await page.evaluate(js_code)
            return ActionResult(extracted_content=result)
        except Exception as e:
            return ActionResult(extracted_content="提交失败: " + str(e))

    @tools.action(description="[脚本内部工具] 关闭所有弹窗和遮罩。")
    async def script_close_popups() -> ActionResult:
        try:
            page = await browser.get_current_page()
            if not page:
                return ActionResult(extracted_content="无法获取当前页面")
            js_code = r"""(function(){
                var count = 0;
                document.querySelectorAll('.el-overlay, .el-picker-panel, .el-select-dropdown, .el-calendar, .el-popover, .el-message-box, .el-loading-mask, .el-dialog, .el-message, .el-notification').forEach(function(el) {
                    if (el.offsetHeight > 0 || el.offsetWidth > 0) { el.style.display = 'none'; count++; }
                });
                return '已关闭 ' + count + ' 个弹窗';
            })()"""
            result = await page.evaluate(js_code)
            return ActionResult(extracted_content=str(result))
        except Exception as e:
            return ActionResult(extracted_content="关闭弹窗失败: " + str(e))

    # ─── Agent 任务描述 ───
    task = """你是 browser-use 自动化测试 Agent，执行「线上备案申请」全流程测试。
这是一个测试脚本，所有表单数据都可以随意填写，目标是走完流程。

## 核心规则
1. **登录由 `script_login` 脚本工具完成！** 如果导航后在登录页，立即调用 script_login。
2. **表单填写由脚本内部工具完成！** 调用 `script_prefill_form` 即可一键填写所有字段+勾选协议。
3. **表单提交由脚本内部工具完成！** 调用 `script_submit_form` 即可一键提交。
4. **关闭弹窗用 `script_close_popups`。**
5. 绝对禁止使用 browser-use 内置的 scroll 工具！
6. 每步最多只做一个动作
7. 截图由脚本自动处理，Agent 不需要调用 write_file 或 screenshot 工具
8. 每次操作后观察返回结果，成功就继续，失败就换方案
9. 如果同一个按钮/动作连续失败 6 次，停止该动作，调用 done 报告
10. **不要因看到错误提示就停止，继续往下走！**
11. 如果提交后有验证错误，先调用 script_prefill_form 重新填写，再调用 script_submit_form 重新提交

## 完整流程
1. 关闭弹窗: script_close_popups
2. 导航到 https://www.zbsykj.com:19096/
3. **如果在登录页，调用 script_login 自动登录！**
4. 登录成功后，找到"线上备案申请"菜单并点击
5. 调用 get_page_info 确认页面
6. 找到"申请备案"或"新增"按钮并点击
7. 调用 script_prefill_form 一键填写表单
8. 调用 script_submit_form 一键提交
9. 调用 get_page_info 查看结果
10. 如果成功，截图记录
11. 如果有错误，修复后重试
12. 继续后续流程直到结束

## 流程示例
1. script_close_popups -> 2. navigate -> 3. script_login(如果在登录页) -> 4. click(线上备案申请) -> 5. get_page_info -> 6. click(申请备案) -> 7. script_prefill_form -> 8. script_submit_form -> 9. get_page_info

## done 工具 text 参数格式
完成后调用 done(text="...")，text 中包含：
- 总执行步数
- 到达的最终页面标题和URL
- 填写了几个字段
- 提交了几次
- 遇到了哪些错误
- 流程是否走完
- 下一步建议
"""

    # 创建 Agent
    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        tools=tools,
        use_vision=True,
        llm_timeout=300,
        step_timeout=300,
        max_steps=30,
        max_actions_per_step=1,
        max_failures=6,
        flash_mode=True,
        save_conversation_path=str(OUTPUT_DIR / f"log_{TIMESTAMP_LOG}.conversation.json"),
    )

    print(f"截图: {PICTURE_PATH}")
    print(f"日志: {LOG_PATH}")
    print("Agent 启动中... (flash_mode=true, max_steps=30)")
    print("流程：进入线上备案申请 -> 申请备案 -> 填写表单 -> 提交 -> 继续流程")
    print()

    # 运行
    history = await agent.run()

    # ─── Agent 结束后，由脚本自己截图和写日志（绕过沙箱） ───
    print("=" * 60)
    print("执行完成!")
    print("=" * 60)
    print(f"  执行步数: {history.number_of_steps()}")
    print(f"  是否完成: {history.is_done()}")
    print(f"  是否成功: {history.is_successful()}")
    print(f"  错误数量: {sum(1 for e in history.errors() if e)}")
    print(f"  访问 URL 数: {len(list(dict.fromkeys(history.urls())))}")

    final_result = history.final_result()
    if final_result:
        print(f"  最终结果: {final_result[:300]}")
    print("=" * 60)

    # 截图：由脚本直接调用 browser.take_screenshot
    try:
        screenshot_bytes = await browser.take_screenshot(path=str(PICTURE_PATH), full_page=False)
        print(f"\n[OK] 截图已保存: {PICTURE_PATH}")
        print(f"  文件大小: {len(screenshot_bytes)} 字节")
    except Exception as e:
        print(f"\n[FAIL] 截图失败: {str(e)}")
        screenshot_saved = False
    else:
        screenshot_saved = True

    # 写日志：由脚本自己写到真实目录，包含完整信息
    try:
        urls = list(dict.fromkeys(history.urls()))
        actions = history.action_names()
        contents = history.extracted_content()
        errors = history.errors()

        # 构建日志内容
        log_lines = []
        log_lines.append("# 线上备案申请 - 全流程自动化测试结果\n")
        log_lines.append("## 基本信息")
        log_lines.append(f"- 时间: {TIMESTAMP_FULL}")
        log_lines.append(f"- 脚本: suyuan_test3.py")
        log_lines.append(f"- LLM: {LOCAL_MODEL}")
        log_lines.append("")
        log_lines.append("## 执行摘要")
        log_lines.append(f"- 执行步数: {history.number_of_steps()}")
        log_lines.append(f"- 是否完成: {history.is_done()}")
        log_lines.append(f"- 是否成功: {history.is_successful()}")
        log_lines.append(f"- 错误数量: {sum(1 for e in errors if e)}")
        log_lines.append(f"- 截图: {PICTURE_PATH} (已保存: {screenshot_saved})")
        log_lines.append("")
        log_lines.append("## 访问 URL 列表")
        for i, u in enumerate(urls):
            log_lines.append(f"- {i+1}. {u}")
        log_lines.append("")
        log_lines.append("## 执行步骤")
        for i, a in enumerate(actions):
            log_lines.append(f"{i+1}. {a}")
        log_lines.append("")

        # 提取内容部分
        log_lines.append("## 提取内容")
        for i, c in enumerate(contents):
            if c:
                log_lines.append(f"--- 步骤 {i+1} ---")
                log_lines.append(c[:500])
                log_lines.append("")
        log_lines.append("")

        # 错误记录 - 重点加粗
        error_count = sum(1 for e in errors if e)
        log_lines.append("## 错误记录")
        if error_count > 0:
            log_lines.append(f"**共 {error_count} 个错误（重点关注）:**\n")
            for i, e in enumerate(errors):
                if e:
                    log_lines.append(f"**错误 {i+1}:** {str(e)[:500]}")
                    log_lines.append("")
        else:
            log_lines.append("无错误")
        log_lines.append("")

        # 最终结果
        log_lines.append("## 最终结果")
        log_lines.append(final_result[:1000] if final_result else "无")
        log_lines.append("")

        log_content = "\n".join(log_lines)

        LOG_PATH.write_text(log_content, encoding='utf-8')
        print(f"[OK] 日志已保存: {LOG_PATH}")
        print(f"  文件大小: {LOG_PATH.stat().st_size} 字节")
    except Exception as e:
        print(f"[FAIL] 日志保存失败: {str(e)}")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

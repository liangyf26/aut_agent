"""
suyuan_test3.py - Browser-Use 全流程自动化测试脚本
1. 找到"线上备案申请",点击进入
2. 找到"申请备案"按钮,点击进入申请表
3. 按流程一步步往下走,每新页面截图
4. 模拟填写表单,提交
5. 如有错误,截图并重点加粗记录到 LOG
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

    @tools.action(description="截图并保存到 pic/pic_yymmdd_HHMM.png。每进入一个新页面,必须立即调用此工具。")
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

    # ─── 脚本内部工具函数(Agent 专用入口)───

    @tools.action(description="[脚本内部工具] 自动登录系统。脚本会用 CDP 填写账号密码，并用纯 JS 在浏览器端识别数学算式验证码（如 2-2=?），无需调用外部 LLM。Agent 不需要传参数，直接调用即可。")
    async def script_login() -> ActionResult:
        """脚本自动登录（Agent 专用入口）
        
        验证码识别采用纯浏览器端 JS 实现：
        1. 在 DOM 中找到验证码 <img> 元素
        2. 将图片绘制到 Canvas，读取像素
        3. 自适应阈值二值化 → 垂直投影分割字符 → 基于宽高比+孔洞数的特征识别
        4. 从识别结果中提取 "数字 操作符 数字" 算式并计算答案
        5. 填入答案并提交登录
        """
        try:
            page = await browser.get_current_page()
            if not page:
                return ActionResult(extracted_content="无法获取当前页面")

            js_all = r"""() => {
                var url = window.location.href;
                if (!url.includes('login') && !url.includes('Login')) {
                    return JSON.stringify({skip:true, msg:'已在主页，无需登录'});
                }

                // ═══════════════════════════════════════════════════
                //  辅助函数
                // ═══════════════════════════════════════════════════

                // 找所有 input（含 Shadow DOM）
                function findInputs(root) {
                    var inputs = Array.from(root.querySelectorAll('input'));
                    root.querySelectorAll('*').forEach(function(el) {
                        if (el.shadowRoot) inputs = inputs.concat(findInputs(el.shadowRoot));
                    });
                    return inputs;
                }

                // ─── 验证码识别引擎 ───
                function recognizeCaptcha(imgElement) {
                    var w = imgElement.naturalWidth || imgElement.width || 100;
                    var h = imgElement.naturalHeight || imgElement.height || 30;
                    if (w < 20 || h < 10) return {formula:'', answer:null, error:'图片尺寸异常 w='+w+' h='+h};

                    // 1) 绘制到 Canvas 并获取像素
                    var canvas = document.createElement('canvas');
                    canvas.width = w; canvas.height = h;
                    var ctx = canvas.getContext('2d');
                    ctx.drawImage(imgElement, 0, 0, w, h);
                    var imageData = ctx.getImageData(0, 0, w, h);
                    var pixels = imageData.data;

                    // 2) 自适应阈值二值化  (Otsu)
                    var histogram = new Array(256).fill(0);
                    var totalPx = w * h;
                    for (var i = 0; i < totalPx; i++) {
                        var idx = i * 4;
                        var gray = Math.round(pixels[idx] * 0.299 + pixels[idx+1] * 0.587 + pixels[idx+2] * 0.114);
                        histogram[gray]++;
                    }
                    // Otsu 求阈值
                    var sum = 0;
                    for (var t = 0; t < 256; t++) sum += t * histogram[t];
                    var sumB = 0, wB = 0, wF = 0, maxVariance = 0, threshold = 128;
                    for (var t = 0; t < 256; t++) {
                        wB += histogram[t];
                        if (wB === 0) continue;
                        wF = totalPx - wB;
                        if (wF === 0) break;
                        sumB += t * histogram[t];
                        var mB = sumB / wB;
                        var mF = (sum - sumB) / wF;
                        var between = wB * wF * (mB - mF) * (mB - mF);
                        if (between > maxVariance) { maxVariance = between; threshold = t; }
                    }
                    // 构造二值矩阵 (1 = 前景/文字, 0 = 背景)
                    var binary = [];
                    for (var y = 0; y < h; y++) {
                        binary[y] = [];
                        for (var x = 0; x < w; x++) {
                            var idx = (y * w + x) * 4;
                            var gray = Math.round(pixels[idx] * 0.299 + pixels[idx+1] * 0.587 + pixels[idx+2] * 0.114);
                            binary[y][x] = gray < threshold ? 1 : 0;
                        }
                    }

                    // 3) 垂直投影分割字符
                    var vProj = [];
                    for (var x = 0; x < w; x++) {
                        var cnt = 0;
                        for (var y = 0; y < h; y++) cnt += binary[y][x];
                        vProj[x] = cnt;
                    }
                    var segments = [];
                    var inChar = false, segStart = 0;
                    for (var x = 0; x <= w; x++) {
                        var hasPx = (x < w && vProj[x] > 0);
                        if (hasPx && !inChar) { inChar = true; segStart = x; }
                        else if ((!hasPx || x === w) && inChar) {
                            inChar = false;
                            var segW = x - segStart;
                            if (segW >= 3) segments.push({l:segStart, r:x, w:segW});
                        }
                    }
                    if (segments.length < 2) return {formula:'', answer:null, error:'分割字符失败, 只找到'+segments.length+'段', raw:''};

                    // 4) 按 10x12 网格抽取特征并识别
                    var chars = '';
                    var gridRows = 12, gridCols = 10;
                    for (var s = 0; s < segments.length; s++) {
                        var seg = segments[s];
                        var segW = seg.w, segH = h;
                        var aspect = segW / segH;

                        // 孔洞数统计（多行扫描）
                        var holes = 0;
                        var checkRows = [0.33, 0.5, 0.67];
                        var midX = Math.floor((seg.l + seg.r) / 2);
                        for (var ri = 0; ri < checkRows.length; ri++) {
                            var cy = Math.floor(segH * checkRows[ri]);
                            if (cy < segH && midX < w && binary[cy][midX] === 0) holes++;
                        }
                        // 归一化: 0 个检查点为背景 → 0 洞; 全为背景 → 至少 2 洞
                        holes = Math.min(holes, 2);

                        // 上下半黑像素比
                        var topBlack = 0, botBlack = 0;
                        var midY = Math.floor(segH / 2);
                        for (var y = 0; y < midY; y++)
                            for (var x = seg.l; x < seg.r; x++) if (binary[y][x]) topBlack++;
                        for (var y = midY; y < segH; y++)
                            for (var x = seg.l; x < seg.r; x++) if (binary[y][x]) botBlack++;
                        var topRatio = topBlack / (topBlack + botBlack + 1);

                        // 中线水平跨度（用于区分 - 号）
                        var midLineCount = 0;
                        for (var x = seg.l; x < seg.r; x++) if (binary[midY] && binary[midY][x]) midLineCount++;
                        var midLineRatio = midLineCount / segW;

                        // 竖直中线跨度（用于区分 + 号）
                        var midVertCount = 0;
                        for (var y = 0; y < segH; y++) if (binary[y][midX]) midVertCount++;
                        var midVertRatio = midVertCount / segH;

                        var ch = '?';
                        if (aspect < 0.3) { ch = '1'; }
                        else if (holes >= 2) { ch = '8'; }
                        else if (holes === 1) {
                            if (aspect > 1.15) ch = '0';
                            else if (topRatio > 0.55) ch = '9';
                            else ch = '6';
                        } else {
                            // 无孔洞 → 可能是 2/3/4/5/7 或操作符
                            // 先检测操作符
                            if (midLineRatio > 0.55 && midVertRatio < 0.4) { ch = '-'; }
                            else if (midLineRatio > 0.3 && midVertRatio > 0.45) { ch = '+'; }
                            else if (aspect > 1.05) { ch = '4'; }
                            else if (topRatio < 0.32) { ch = '7'; }
                            else if (topRatio > 0.62) { ch = '2'; }
                            else { ch = (Math.abs(topRatio - 0.5) < 0.12) ? '5' : '3'; }
                        }
                        chars += ch;
                    }

                    // 5) 从 chars 中提取 "数字 操作符 数字" 算式并计算
                    var re = /(\d+)([+\-])(\d+)/;
                    var m = chars.match(re);
                    if (!m) {
                        // 放宽匹配：允许 x/X/* 作为乘号, / 作为除号
                        re = /(\d+)([+\-xX*\/])(\d+)/;
                        m = chars.match(re);
                    }
                    if (m) {
                        var a = parseInt(m[1], 10);
                        var op = m[2];
                        var b = parseInt(m[3], 10);
                        var answer = 0;
                        if (op === '+') answer = a + b;
                        else if (op === '-') answer = a - b;
                        else if (op === 'x' || op === 'X' || op === '*') answer = a * b;
                        else if (op === '/') answer = b !== 0 ? Math.floor(a / b) : 0;
                        return {formula: m[0], answer: answer, raw: chars, threshold: threshold};
                    }
                    return {formula:'', answer:null, raw:chars, error:'无法从识别结果"'+chars+'"中提取算式', threshold:threshold};
                }

                // ═══════════════════════════════════════════════════
                //  主流程
                // ═══════════════════════════════════════════════════

                // 点击"密码登录"切换
                var allBtns = document.querySelectorAll('button, a, [role=button], .el-tabs__item, .tab-item');
                for (var bi = 0; bi < allBtns.length; bi++) {
                    var t = (allBtns[bi].textContent || '').replace(/\s+/g, '').trim();
                    if (t.indexOf('密码登录') > -1 && allBtns[bi].offsetParent !== null) {
                        try { allBtns[bi].click(); } catch(e) {}
                    }
                }

                // 找输入框
                var inputs = findInputs(document);
                var userField = null, passField = null, captchaField = null;
                for (var ii = 0; ii < inputs.length; ii++) {
                    var inp = inputs[ii];
                    var ph = (inp.placeholder || '').toLowerCase();
                    var type = (inp.type || '').toLowerCase();
                    if (!inp.offsetParent && type !== 'password') continue;
                    if (type === 'password') passField = inp;
                    else if (ph.indexOf('验证码') > -1 || ph.indexOf('captcha') > -1) captchaField = inp;
                    else if (ph.indexOf('账号') > -1 || ph.indexOf('手机') > -1 || ph.indexOf('用户名') > -1) userField = inp;
                }

                var info = {};
                if (userField) {
                    userField.focus();
                    userField.value = '18607719993';
                    userField.dispatchEvent(new Event('input',{bubbles:true}));
                    userField.dispatchEvent(new Event('change',{bubbles:true}));
                    info.userFilled = true;
                } else info.userFilled = false;

                if (passField) {
                    passField.focus();
                    passField.value = 'xyy#!31EE';
                    passField.dispatchEvent(new Event('input',{bubbles:true}));
                    passField.dispatchEvent(new Event('change',{bubbles:true}));
                    info.passFilled = true;
                } else info.passFilled = false;

                // 找验证码图片并识别
                if (captchaField) {
                    var captchaImg = null;
                    var el = captchaField;
                    for (var depth = 0; depth < 4 && el; depth++) {
                        el = el.parentElement;
                        if (!el) break;
                        var imgs = el.querySelectorAll('img, svg, canvas');
                        for (var k = 0; k < imgs.length; k++) {
                            var r = imgs[k].getBoundingClientRect();
                            if (r.width >= 40 && r.height >= 10 && r.width <= 250 && r.height <= 80) {
                                captchaImg = imgs[k]; break;
                            }
                        }
                        if (captchaImg) break;
                    }

                    if (captchaImg && captchaImg.tagName === 'IMG') {
                        // 等图片加载完成再识别
                        if (!captchaImg.complete) {
                            info._captchaNotLoaded = true;
                            info.captchaImgSrc = captchaImg.src;
                        } else {
                            var ocrResult = recognizeCaptcha(captchaImg);
                            info.ocr = ocrResult;
                            if (ocrResult.answer !== null && ocrResult.answer !== undefined) {
                                captchaField.focus();
                                captchaField.value = String(ocrResult.answer);
                                captchaField.dispatchEvent(new Event('input',{bubbles:true}));
                                captchaField.dispatchEvent(new Event('change',{bubbles:true}));
                                info.captchaFilled = true;
                                info.captchaAnswer = ocrResult.answer;
                                info.captchaFormula = ocrResult.formula;
                            } else {
                                info.captchaFilled = false;
                                info.ocrError = ocrResult.error || 'OCR 未识别出算式';
                                info.ocrRaw = ocrResult.raw || '';
                            }
                        }
                    } else {
                        info.captchaFilled = false;
                        info.ocrError = '未找到验证码图片';
                    }
                } else {
                    info.captchaFilled = false;
                    info.ocrError = '未找到验证码输入框';
                }

                // 找登录按钮并提交
                var btns = document.querySelectorAll('button, input[type=submit]');
                var submitBtn = null;
                for (var bi2 = 0; bi2 < btns.length; bi2++) {
                    var bt = (btns[bi2].textContent || btns[bi2].value || '').replace(/\s+/g, '').trim();
                    if (bt === '登录' || bt === '登  录') { submitBtn = btns[bi2]; break; }
                }
                if (!submitBtn) {
                    for (var bi3 = 0; bi3 < btns.length; bi3++) {
                        var btx = (btns[bi3].textContent || '').trim();
                        if (btx.indexOf('登') > -1 && btns[bi3].offsetParent !== null) { submitBtn = btns[bi3]; break; }
                    }
                }
                if (submitBtn) { submitBtn.click(); info.submitted = true; } else info.submitted = false;

                return JSON.stringify(info);
            }"""

            result = await page.evaluate(js_all)
            import json
            result_data = json.loads(result)
            if result_data.get('skip'):
                return ActionResult(extracted_content=result_data.get('msg', '已跳过'))

            # 如果验证码图片还没加载，等 1s 后重试一次
            if result_data.get('_captchaNotLoaded'):
                import asyncio
                await asyncio.sleep(1)
                # 第二次调用（图片应该加载好了）
                js_retry = r"""() => {
                    function findCaptchaAndRecognize() {
                        var inputs = document.querySelectorAll('input');
                        var captchaField = null;
                        for (var i = 0; i < inputs.length; i++) {
                            var ph = (inputs[i].placeholder || '').toLowerCase();
                            if (ph.indexOf('验证码') > -1 || ph.indexOf('captcha') > -1) { captchaField = inputs[i]; break; }
                        }
                        if (!captchaField) return JSON.stringify({error:'找不到验证码输入框'});

                        var captchaImg = null;
                        var el = captchaField;
                        for (var depth = 0; depth < 4 && el; depth++) {
                            el = el.parentElement;
                            if (!el) break;
                            var imgs = el.querySelectorAll('img');
                            for (var k = 0; k < imgs.length; k++) {
                                var r = imgs[k].getBoundingClientRect();
                                if (r.width >= 40 && r.height >= 10) { captchaImg = imgs[k]; break; }
                            }
                            if (captchaImg) break;
                        }
                        if (!captchaImg) return JSON.stringify({error:'未找到验证码图片(重试)'});

                        // 复制 recognizeCaptcha 函数（简化内联版本）
                        var w = captchaImg.naturalWidth || captchaImg.width || 100;
                        var h = captchaImg.naturalHeight || captchaImg.height || 30;
                        var canvas = document.createElement('canvas');
                        canvas.width = w; canvas.height = h;
                        var ctx = canvas.getContext('2d');
                        ctx.drawImage(captchaImg, 0, 0, w, h);
                        var imageData = ctx.getImageData(0, 0, w, h);
                        var pixels = imageData.data;

                        var histogram = new Array(256).fill(0);
                        var totalPx = w * h;
                        for (var i = 0; i < totalPx; i++) {
                            var idx = i * 4;
                            var gray = Math.round(pixels[idx] * 0.299 + pixels[idx+1] * 0.587 + pixels[idx+2] * 0.114);
                            histogram[gray]++;
                        }
                        var sum = 0;
                        for (var t = 0; t < 256; t++) sum += t * histogram[t];
                        var sumB = 0, wB = 0, wF = 0, maxVariance = 0, threshold = 128;
                        for (var t = 0; t < 256; t++) {
                            wB += histogram[t];
                            if (wB === 0) continue;
                            wF = totalPx - wB;
                            if (wF === 0) break;
                            sumB += t * histogram[t];
                            var mB = sumB / wB, mF = (sum - sumB) / wF;
                            var between = wB * wF * (mB - mF) * (mB - mF);
                            if (between > maxVariance) { maxVariance = between; threshold = t; }
                        }
                        var binary = [];
                        for (var y = 0; y < h; y++) {
                            binary[y] = [];
                            for (var x = 0; x < w; x++) {
                                var idx2 = (y * w + x) * 4;
                                var g2 = Math.round(pixels[idx2] * 0.299 + pixels[idx2+1] * 0.587 + pixels[idx2+2] * 0.114);
                                binary[y][x] = g2 < threshold ? 1 : 0;
                            }
                        }
                        var vProj = [];
                        for (var x = 0; x < w; x++) {
                            var cnt = 0;
                            for (var y = 0; y < h; y++) cnt += binary[y][x];
                            vProj[x] = cnt;
                        }
                        var segments = [];
                        var inChar = false, segStart = 0;
                        for (var x = 0; x <= w; x++) {
                            var hasPx = (x < w && vProj[x] > 0);
                            if (hasPx && !inChar) { inChar = true; segStart = x; }
                            else if ((!hasPx || x === w) && inChar) {
                                inChar = false;
                                var segW = x - segStart;
                                if (segW >= 3) segments.push({l:segStart, r:x, w:segW});
                            }
                        }
                        var chars = '';
                        for (var s = 0; s < segments.length; s++) {
                            var seg = segments[s];
                            var segW = seg.w, segH = h;
                            var aspect = segW / segH;

                            var holes = 0;
                            var checkRows = [0.33, 0.5, 0.67];
                            var midX = Math.floor((seg.l + seg.r) / 2);
                            for (var ri = 0; ri < checkRows.length; ri++) {
                                var cy = Math.floor(segH * checkRows[ri]);
                                if (cy < segH && midX < w && binary[cy][midX] === 0) holes++;
                            }
                            holes = Math.min(holes, 2);

                            var topBlack = 0, botBlack = 0;
                            var midY = Math.floor(segH / 2);
                            for (var y = 0; y < midY; y++)
                                for (var x = seg.l; x < seg.r; x++) if (binary[y][x]) topBlack++;
                            for (var y = midY; y < segH; y++)
                                for (var x = seg.l; x < seg.r; x++) if (binary[y][x]) botBlack++;
                            var topRatio = topBlack / (topBlack + botBlack + 1);

                            var midLineCount = 0;
                            for (var x = seg.l; x < seg.r; x++) if (binary[midY] && binary[midY][x]) midLineCount++;
                            var midLineRatio = midLineCount / segW;

                            var midVertCount = 0;
                            for (var x = seg.l; x < seg.r; x++) if (binary[x] && binary[x][midX]) midVertCount++;
                            // 修正: 竖直中线
                            midVertCount = 0;
                            for (var y2 = 0; y2 < segH; y2++) if (binary[y2][midX]) midVertCount++;
                            var midVertRatio = midVertCount / segH;

                            var ch = '?';
                            if (aspect < 0.3) ch = '1';
                            else if (holes >= 2) ch = '8';
                            else if (holes === 1) {
                                if (aspect > 1.15) ch = '0';
                                else if (topRatio > 0.55) ch = '9';
                                else ch = '6';
                            } else {
                                if (midLineRatio > 0.55 && midVertRatio < 0.4) ch = '-';
                                else if (midLineRatio > 0.3 && midVertRatio > 0.45) ch = '+';
                                else if (aspect > 1.05) ch = '4';
                                else if (topRatio < 0.32) ch = '7';
                                else if (topRatio > 0.62) ch = '2';
                                else ch = (Math.abs(topRatio - 0.5) < 0.12) ? '5' : '3';
                            }
                            chars += ch;
                        }

                        var re = /(\d+)([+\-xX*\/])(\d+)/;
                        var m = chars.match(re);
                        if (!m) return JSON.stringify({formula:'', answer:null, raw:chars, error:'无法解析算式'});
                        var a = parseInt(m[1], 10), op = m[2], b = parseInt(m[3], 10);
                        var answer = 0;
                        if (op === '+') answer = a + b;
                        else if (op === '-') answer = a - b;
                        else if (op === 'x' || op === 'X' || op === '*') answer = a * b;
                        else if (op === '/') answer = b !== 0 ? Math.floor(a / b) : 0;

                        captchaField.focus();
                        captchaField.value = String(answer);
                        captchaField.dispatchEvent(new Event('input',{bubbles:true}));
                        captchaField.dispatchEvent(new Event('change',{bubbles:true}));
                        return JSON.stringify({formula:m[0], answer:answer, raw:chars, captchaFilled:true});
                    }
                    return findCaptchaAndRecognize();
                }"""
                retry_result = await page.evaluate(js_retry)
                retry_data = json.loads(retry_result)
                return ActionResult(extracted_content=f"登录结果(重试): {json.dumps(retry_data, ensure_ascii=False)}")

            return ActionResult(extracted_content=f"登录结果: {json.dumps(result_data, ensure_ascii=False)}")
        except Exception as e:
            return ActionResult(extracted_content=f"登录失败: {e}")

    @tools.action(description="[脚本内部工具] 预填写表单并勾选协议。脚本会调用 CDP 直接操作 DOM 填写所有可见表单字段,然后勾选所有协议 checkbox。Agent 不需要传参数,直接调用即可。")
    async def script_prefill_form() -> ActionResult:
        try:
            page = await browser.get_current_page()
            if not page:
                return ActionResult(extracted_content="无法获取当前页面")

            js_code = r"""() => {
                var results = {filled:0, selects:0, cascaders:0, dates:0, checkboxes:0, errors:[], skipped:0};
                var testValues = {'text':'测试数据','number':'100','email':'test@test.com','tel':'13800138000','url':'https://test.com','search':'测试'};

                // ─── 辅助函数 ───
                function trigger(el, evt) { el.dispatchEvent(new Event(evt,{bubbles:true,cancelable:true})); }
                function safeClick(el) {
                    try { el.focus(); } catch(e){}
                    try { el.dispatchEvent(new MouseEvent('mousedown',{bubbles:true})); } catch(e){}
                    try { el.click(); } catch(e){}
                    try { el.dispatchEvent(new MouseEvent('mouseup',{bubbles:true})); } catch(e){}
                }
                function closest(el, sel) {
                    while (el && el !== document.documentElement) {
                        if (el.matches && el.matches(sel)) return el;
                        el = el.parentElement;
                    }
                    return null;
                }
                // 用原生 value setter 绕过 Vue 的 getter/setter 拦截
                function setNativeValue(el, val) {
                    var desc = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value');
                    if (desc && desc.set) { desc.set.call(el, val); }
                    else { el.value = val; }
                }
                // 关闭所有已打开的弹窗
                function closeAllPopups() {
                    var overlays = document.querySelectorAll('.el-select-dropdown, .el-cascader__dropdown, .el-cascader-panel, .el-picker-panel, .el-popper');
                    for (var oi = 0; oi < overlays.length; oi++) {
                        overlays[oi].style.display = 'none';
                    }
                    document.body.click();
                }

                // ─── 1) el-select 下拉框 ───
                function fillElSelect(inp) {
                    var selectEl = closest(inp, '.el-select');
                    if (!selectEl) return false;
                    var clickTarget = selectEl.querySelector('.el-select__tags') || selectEl.querySelector('.el-input__inner') || inp;
                    if (clickTarget.offsetParent === null) return false;
                    safeClick(clickTarget);
                    var items = document.querySelectorAll('.el-select-dropdown:not([style*="display: none"]) .el-select-dropdown__item:not(.is-disabled):not(.selected)');
                    if (items.length === 0) { closeAllPopups(); return false; }
                    var picked = null;
                    for (var si = 0; si < items.length; si++) {
                        if (items[si].offsetParent !== null) { picked = items[si]; break; }
                    }
                    if (picked) { safeClick(picked); results.selects++; }
                    else { closeAllPopups(); }
                    return !!picked;
                }

                // ─── 2) el-cascader 级联下拉 ───
                function fillElCascader(inp) {
                    var casEl = closest(inp, '.el-cascader');
                    if (!casEl) return false;
                    safeClick(casEl.querySelector('.el-input__inner') || inp);
                    var panel = document.querySelector('.el-cascader__dropdown:not([style*="display: none"]), .el-cascader-panel:not([style*="display: none"])');
                    if (!panel) { closeAllPopups(); return false; }
                    var menus = panel.querySelectorAll('.el-cascader-menu');
                    for (var mi = 0; mi < menus.length; mi++) {
                        var nodes = menus[mi].querySelectorAll('.el-cascader-node:not(.is-disabled)');
                        if (nodes.length > 0) {
                            var lbl = nodes[0].querySelector('.el-cascader-node__label');
                            safeClick(lbl || nodes[0]);
                        } else { break; }
                    }
                    results.cascaders++;
                    closeAllPopups();
                    return true;
                }

                // ─── 3) el-date-picker 日期选择器 ───
                function fillElDatePicker(inp) {
                    var ph = (inp.placeholder || '');
                    if (ph.indexOf('日期') === -1 && ph.indexOf('date') === -1) return false;
                    safeClick(inp);
                    trigger(inp, 'focus');
                    var panel = document.querySelector('.el-picker-panel:not([style*="display: none"])');
                    if (!panel) { closeAllPopups(); return false; }
                    var cells = panel.querySelectorAll('td.available:not(.disabled)');
                    if (cells.length === 0) { closeAllPopups(); return false; }
                    // 优先选15号（避开边界）
                    var target = null;
                    for (var di = 0; di < cells.length; di++) {
                        var sp = cells[di].querySelector('div span');
                        if (sp && sp.textContent.trim() === '15') { target = cells[di]; break; }
                    }
                    if (!target) target = cells[Math.min(10, cells.length - 1)];
                    if (target) { safeClick(target); results.dates++; return true; }
                    closeAllPopups();
                    return false;
                }

                // ─── 4) el-input-number 数字输入 ───
                function fillElInputNumber(inp) {
                    if ((inp.type || '').toLowerCase() !== 'number') return false;
                    var numEl = closest(inp, '.el-input-number');
                    if (!numEl) return false;
                    var cur = parseFloat(inp.value);
                    if (!isNaN(cur) && cur > 0) { results.skipped++; return true; }
                    setNativeValue(inp, '100');
                    trigger(inp, 'input'); trigger(inp, 'change');
                    inp.dispatchEvent(new CustomEvent('el-input-change',{bubbles:true}));
                    inp.dispatchEvent(new CustomEvent('el-input-input',{bubbles:true}));
                    results.filled++;
                    return true;
                }

                // ═══════════════════════════════════
                //  主流程
                // ═══════════════════════════════════
                closeAllPopups();
                var inputs = document.querySelectorAll('.el-dialog input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=checkbox]):not([type=radio]):not([type=file]), .el-dialog textarea');
                if (inputs.length === 0) inputs = document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=checkbox]):not([type=radio]):not([type=file]), textarea');
                var processed = new Set();
                inputs.forEach(function(inp) {
                    if (processed.has(inp)) return;
                    if (inp.disabled || !inp.offsetParent) { results.skipped++; return; }

                    if (fillElSelect(inp)) { processed.add(inp); return; }
                    if (fillElCascader(inp)) { processed.add(inp); return; }
                    if (fillElDatePicker(inp)) { processed.add(inp); return; }
                    if (fillElInputNumber(inp)) { processed.add(inp); return; }

                    var t = (inp.type || '').toLowerCase();
                    var val = testValues[t] || '测试数据';
                    try {
                        setNativeValue(inp, val);
                        trigger(inp, 'input'); trigger(inp, 'change'); trigger(inp, 'blur');
                        inp.dispatchEvent(new CustomEvent('el-input-change',{bubbles:true}));
                        inp.dispatchEvent(new CustomEvent('el-input-input',{bubbles:true}));
                        results.filled++;
                    } catch(e) { results.errors.push(e.message); }
                    processed.add(inp);
                });

                var cbs = document.querySelectorAll('.el-dialog input[type=checkbox]');
                if (cbs.length === 0) cbs = document.querySelectorAll('input[type=checkbox]');
                cbs.forEach(function(cb) {
                    if (!cb.checked && !cb.disabled && cb.offsetParent) {
                        safeClick(cb); trigger(cb, 'change'); trigger(cb, 'input');
                        results.checkboxes++;
                    }
                });

                closeAllPopups();
                window.scrollTo(0, document.body.scrollHeight);
                return JSON.stringify(results);
            }"""
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
            js_code = r"""() => {
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
            }"""
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
            js_code = r"""() => {
                var count = 0;
                // 只关弹窗/遮罩/消息，不关 .el-dialog（表单主窗口）
                document.querySelectorAll('.el-overlay, .el-picker-panel, .el-select-dropdown, .el-calendar, .el-popover, .el-message-box, .el-loading-mask, .el-message, .el-notification').forEach(function(el) {
                    if (el.offsetHeight > 0 || el.offsetWidth > 0) { el.style.display = 'none'; count++; }
                });
                // 关闭 el-message-box 的遮罩层
                document.querySelectorAll('.v-modal').forEach(function(el) {
                    el.style.display = 'none'; count++;
                });
                // 移除 body 上的 overflow:hidden（el-dialog 打开时会加）
                document.body.style.overflow = '';
                return '已关闭 ' + count + ' 个弹窗';
            }"""
            result = await page.evaluate(js_code)
            return ActionResult(extracted_content=str(result))
        except Exception as e:
            return ActionResult(extracted_content="关闭弹窗失败: " + str(e))

    # ─── Agent 任务描述 ───
    task = """你是 browser-use 自动化测试 Agent,执行「线上备案申请」全流程测试。
这是一个测试脚本,所有表单数据都可以随意填写,目标是走完流程。

## 核心规则
1. **登录由 `script_login` 脚本工具完成!** 如果导航后在登录页,立即调用 script_login。
2. **表单填写由脚本内部工具完成!** 调用 `script_prefill_form` 即可一键填写所有字段+勾选协议。
3. **表单提交由脚本内部工具完成!** 调用 `script_submit_form` 即可一键提交。
4. **关闭弹窗用 `script_close_popups`。**
5. 绝对禁止使用 browser-use 内置的 scroll 工具!
6. 每步最多只做一个动作
7. 截图由脚本自动处理,Agent 不需要调用 write_file 或 screenshot 工具
8. 每次操作后观察返回结果,成功就继续,失败就换方案
9. 如果同一个按钮/动作连续失败 6 次,停止该动作,调用 done 报告
10. **不要因看到错误提示就停止,继续往下走!**
11. 如果提交后有验证错误,先调用 script_prefill_form 重新填写,再调用 script_submit_form 重新提交

## 完整流程
1. 关闭弹窗: script_close_popups
2. 导航到 https://www.zbsykj.com:19096/
3. **如果在登录页,调用 script_login 自动登录!**
4. 登录成功后,找到"线上备案申请"菜单并点击
5. 调用 get_page_info 确认页面
6. 找到"申请备案"或"新增"按钮并点击
7. 调用 script_prefill_form 一键填写表单
8. 调用 script_submit_form 一键提交
9. 调用 get_page_info 查看结果
10. 如果成功,截图记录
11. 如果有错误,修复后重试
12. 继续后续流程直到结束

## 流程示例
1. script_close_popups -> 2. navigate -> 3. script_login(如果在登录页) -> 4. click(线上备案申请) -> 5. get_page_info -> 6. click(申请备案) -> 7. script_prefill_form -> 8. script_submit_form -> 9. get_page_info

## done 工具 text 参数格式
完成后调用 done(text="..."),text 中包含:
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
        max_steps=60,
        max_actions_per_step=1,
        max_failures=6,
        flash_mode=True,
        save_conversation_path=str(OUTPUT_DIR / f"log_{TIMESTAMP_LOG}.conversation.json"),
    )

    print(f"截图: {PICTURE_PATH}")
    print(f"日志: {LOG_PATH}")
    print("Agent 启动中... (flash_mode=true, max_steps=60)")
    print("流程:进入线上备案申请 -> 申请备案 -> 填写表单 -> 提交 -> 继续流程")
    print()

    # 运行
    history = await agent.run()

    # ─── Agent 结束后,由脚本自己截图和写日志(绕过沙箱) ───
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

    # 截图:由脚本直接调用 browser.take_screenshot
    try:
        screenshot_bytes = await browser.take_screenshot(path=str(PICTURE_PATH), full_page=False)
        print(f"\n[OK] 截图已保存: {PICTURE_PATH}")
        print(f"  文件大小: {len(screenshot_bytes)} 字节")
    except Exception as e:
        print(f"\n[FAIL] 截图失败: {str(e)}")
        screenshot_saved = False
    else:
        screenshot_saved = True

    # 写日志:由脚本自己写到真实目录,包含完整信息
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
            log_lines.append(f"**共 {error_count} 个错误(重点关注):**\n")
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

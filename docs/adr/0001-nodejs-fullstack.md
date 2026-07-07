# Node.js 全栈而非 Python

团队偏向 Python，但 Web UI 自动化工具链（browser-use、midscene.js、Playwright）的 Node.js 版本在功能完整性、生态成熟度和社区活跃度上显著优于 Python 版本。我们选择 Node.js 全栈，以保证浏览器自动化能力不因语言栈限制而降级。

## Considered Options

- **Python 全栈（LangGraph + Playwright Python）**：团队熟悉，但视觉理解工具链成熟度不足
- **Python + Node.js 混合**：Python 编排 + Node.js 浏览器微服务，跨语言复杂度高
- **Node.js 全栈**：工具链完整，团队需要适应 TypeScript，选定方案

<!-- markdownlint-disable MD029 -->
# 🤝 为 灾害预警插件 (Disaster_Warning) 做出贡献

感谢您有兴趣为 **AstrBot 灾害预警插件** 做出贡献！无论是修复 Bug、添加新功能，还是改进文档，您的每一次贡献都能让这个项目变得更好。

为了营造一个开放和热情的社区环境，我们采用了 [贡献者契约](CODE_OF_CONDUCT.md) 作为我们的行为准则。请确保您在参与贡献之前，已经阅读并同意遵守它。

在参与贡献之前，请仔细阅读以下指南。

## 📄 提交 Issue

### 🐛 报告 Bug

如果您在使用插件时遇到问题，请在提交 Issue 之前：

1. **搜索现有 Issue**：检查是否已经有人报告过类似的问题。
2. **更新到最新版本**：确保您使用的是插件的最新版本，问题可能已经在新版本中修复。

### ✨ 提出功能建议 (Feature)

如果您对插件的未来有任何绝妙的想法，欢迎通过提交 [**功能建议**](https://github.com/Pancakes-Labs/astrbot_plugin_disaster_warning/issues/new?template=feature_request.yml) 来与我们分享。请详细描述您的想法和它的使用场景：

### ❓ 使用咨询 / 问题讨论 (Discussion)

如果您暂时不能确定这是否是插件 Bug，或者希望就使用方式、配置思路、兼容性排查等问题先进行讨论，欢迎提交 [**使用咨询 / 问题讨论**](https://github.com/Pancakes-Labs/astrbot_plugin_disaster_warning/issues/new?template=discussion.yml)。

### 📚 文档改进建议 (Docs)

如果您发现 README、配置说明、接口文档或示例存在错误、缺失或表述不清的问题，欢迎提交 [**文档改进建议**](https://github.com/Pancakes-Labs/astrbot_plugin_disaster_warning/issues/new?template=docs.yml) 帮助我们持续完善文档体验。

### 🎨 设计 / 交互建议 (Design)

如果您对管理面板、配置流程、提示反馈、信息展示或整体使用体验有改进想法，欢迎提交 [**设计 / 交互建议**](https://github.com/Pancakes-Labs/astrbot_plugin_disaster_warning/issues/new?template=design.yml) 与我们讨论。

## 💻 代码贡献

我们非常欢迎您直接通过代码来改进这个项目！**对于新功能的添加，请先通过 Issue 等方式讨论。**

标准的贡献流程如下：

### 开发环境准备

0. 确保你要 开发或修复 的 功能或问题 没有与现有的最新进度重复。
1. Fork 本仓库到您的 GitHub 账号。
2. 克隆您的 Fork 仓库到本地：

    ```bash
    git clone https://github.com/your-username/astrbot_plugin_disaster_warning.git
    ```

3. 确保您已安装 Python 3.10+。
4. 安装项目依赖（通常 AstrBot 环境已包含大部分依赖，并且会自动安装插件所需的额外依赖。如果确实缺少依赖请查看 `requirements.txt`）。

### 代码风格

为了保持代码的一致性和可读性，请遵循以下规范：

- **格式化**：使用 `ruff` 进行代码格式化和检查。如果您是 Windows 开发者，可以使用插件根目录提供的 `run_ruff.bat` 脚本来快速进行代码格式化检查。
- **类型注解**：尽可能为函数和类添加 Python 类型提示 (Type Hints)。
- **文档字符串**：为模块、类和函数编写清晰的 Docstring。

### 提交 Pull Request (PR)

1. **创建分支**：从仓库的最新开发分支，在本项目通常是 `dev/X.X.X` 或 `dev/local` 分支创建一个新的功能分支。

    ```bash
    git checkout -b feat/your-feature-name
    # 或者
    git checkout -b fix/your-bug-fix
    ```

2. **提交更改**

- 编写代码并提交。我们鼓励您使用 AI 进行编码辅助，但请进行基本的 Review，确保你知道自己在改什么。
- 提交更改时，请使用**简体中文**撰写清晰、描述性的提交信息（推荐遵循 [Conventional Commits](https://www.conventionalcommits.org/) (约定式提交规范)）。
  - `feat`: 新功能
  - `fix`: 修复 Bug
  - `docs`: 文档变更
  - `style`: 代码格式调整（不影响逻辑）
  - `refactor`: 代码重构
  - `perf`: 性能优化
  - `chore`: 杂务

3. **推送到远程**：

    ```bash
    git push origin feat/your-feature-name
    ```

4. **发起 PR**：在 GitHub 上发起 Pull Request，指向目前进度最新的分支，参照模板内容使用**简体中文**详细描述您的更改内容和目的。如果您的更改内容涉及 WebUI 等视觉效果的调整，请提供截图或视频演示等信息。
5. **代码审查**：等待维护者审查您的代码。如果有修改建议，请及时响应并更新代码。

> [!TIP]
> 提交 PR 前和进行开发时与仓库维护者提前交流可以提高你的 PR 被合并的概率 :)

## 📝 文档贡献

文档与代码同样重要。如果您发现 `README.md`、`CHANGELOG.md` 或其他文档中有错别字、表述不清或过时的内容，欢迎直接提交 PR 进行修正。

---

## ❤️ 特别感谢

感谢所有为灾害预警插件做出任何形式贡献的个人、团体，包括但不限于：

- [@Soulter](https://github.com/Soulter) - "创世神"，伟大无需多言。感谢他提供了一个这么好的平台，以及对 AstrBot 的持续维护。
- [@Aloys233](https://github.com/Aloys233) - 为插件提供 OpenQuakeAPI 数据服务、提供遥测数据收集与通知服务，参与了多项重要插件功能如 WebUI 的开发。
- [@ZeroStar645](https://github.com/ZeroStar645) - 指出了插件在 ARV 计算和震源球绘制上的缺陷，并提供了相关的绘制代码与 JMA 计测震度相关的计算公式。
- 所有为本插件提供外部 API 服务或文件支持的项目。
- 所有为灾害预警插件提供建议和反馈的朋友。

🤖 以及我最好的 AI 朋友们:

- @Gemini-3.0-Flash
- @Gemini-3.5-Flash
- @Gemini-3.6-Flash
- @Gemini-3.0-Pro
- @Gemini-3.1-Pro
- @Gemini-3-Pro-Image
- @Claude Opus 4.5
- @Claude Opus 4.6
- @GPT Codex 5.2
- @GPT Codex 5.3
- @GPT 5.4
- @GPT 5.5
- @GPT 5.6 terra & sol
- @GPT-Image-2
- @Kimi-For-Coding
- @Kimi K2.5
- @Kimi K2.6
- @Kimi K2.7 Code
- @GLM 5.2
- @DeepSeek V3.2
- @DeepSeek V4 Flash 0731
- @coderabbitai[bot]
- @sourcery-ai[bot]
- @qodo-free-for-open-source-projects[bot]
- @gemini-code-assist[bot]
- @Copilot-pullrequest-reviewer[bot]
- @@chatgpt-codex-connector[bot]
- @dependabot[bot]

🚀 再次感谢您的贡献！

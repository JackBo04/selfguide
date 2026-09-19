<p align="center">
  <img src="docs/assets/hero.svg" alt="SelfGuide" width="760">
</p>

<p align="center">
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Preview" src="https://img.shields.io/badge/status-Preview-orange.svg">
  <img alt="v0.5.0" src="https://img.shields.io/badge/version-v0.5.0-informational.svg">
</p>

# SelfGuide

SelfGuide 是一个面向 **ChatGPT 网页版 × Codex** 的自指导工作流：网页负责方案、按需追问、正文写作与验收，Codex 在你选择的服务器持续执行；执行结果再回到网页，形成可恢复的迭代闭环。

它面向长任务、实验迭代、论文写作和多任务并行。每个任务会在用户配置的 SelfGuide ChatGPT 项目中自动创建独立 Chrome 窗口；同一任务继续沿用原会话。队列、附件、草稿、回复与等待状态分别管理，但共享同一网页登录和账户额度。

> 当前为 **v0.5.0 Preview**。源码以 `main` 为准；旧 Release 不代表最新版。

## 为什么用 SelfGuide

SelfGuide 将复杂任务拆成可连续执行和验收的阶段：网页负责确定当前目标与下一步，Codex 完成该阶段的实际工作并回传结果，再根据验收继续推进，直到用户目标完成。

多任务之间使用独立窗口和任务状态，适合同时推进论文写作、代码修改和实验检查；同一任务始终回到原会话，避免每轮重新解释背景。

## 工作方式

| 角色 | 主要职责 |
| --- | --- |
| ChatGPT 网页 | 主导方案、按需追问、正文写作、结果验收 |
| Codex | 连续实施一个阶段、补充材料、执行命令与回传结果 |
| SelfGuide | 管理窗口、桥接消息/附件、保存状态并驱动下一轮 |

典型循环：

    用户目标
      → ChatGPT 给出下一阶段指导
      → Codex 在服务器执行
      → SelfGuide 回传文字、附件与状态
      → ChatGPT 验收并决定下一步
      → 达成目标后结束

正常情况下，SelfGuide 通过 DOM 直接读取完整文字，并等待网页生成完成；只有文本无法诊断的异常才使用截图。回复会保存到本地文件，任务、轮次和发送状态均可恢复，避免盲目重发。

## 多任务与任务目录

每个任务拥有独立窗口和独立状态，同一任务保持原会话。窗口会自动平铺；运行中的窗口应保持可见，最小化或完全遮挡可能影响网页刷新。

默认任务目录：

    selfguide/tasks/<id>/
      task.txt
      state.json
      uploads/
      messages/
      feedback/
      outputs/
      checks/

也可以为任务指定 workspace。网页登录使用固定 profile 复用，但不能保证永不重新登录；登录和人机验证由用户操作。SelfGuide 不会自行唤醒 Codex，源码也不包含账户凭据或聊天内容。

## Server 与 Local

无论哪一版，**Codex 都在用户选择的服务器执行工作**。

**server**：Chrome、扩展和桥接均运行在服务器，并提供虚拟桌面远程入口。扩展默认连接：

    http://127.0.0.1:8766

**local**：Chrome 和扩展运行在自己的电脑，通过 SSH 隧道连接服务器桥接。请在自己的电脑终端运行：

    ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 your-server

本地版扩展默认连接：

    http://127.0.0.1:8765

网页默认使用 xhigh / Extra High；更难的问题可使用账户可用的 Pro。它只影响 ChatGPT 网页，不会改变 Codex 自身模型。扩展目前不支持自动切档。

## 安装

要求 Python 3.10+、Chrome 120+。服务器桌面的 Node 20+、Xvfb 等依赖见文档。

Server：

    git clone https://github.com/JackBo04/selfguide.git
    cd selfguide
    python3 tools/install.py server

Local：

    git clone https://github.com/JackBo04/selfguide.git
    cd selfguide
    python3 tools/install.py local

默认安装位置：

    ~/.agents/skills/selfguide-server
    ~/.agents/skills/selfguide-local

调用：

    $selfguide-server
    $selfguide-local

如需保留旧单机调用名：

    python3 tools/install.py server --name selfguide --update

已有旧部署可继续使用：

    $selfguide

更新已有仓库：

    git pull --ff-only
    python3 tools/install.py server --update

Local 模式使用：

    git pull --ff-only
    python3 tools/install.py local --update

首次使用还需要完成桥接、浏览器扩展和网页登录配置；**只安装 SKILL.md 并不代表已经连通。**

## 让 Agent 帮你安装

可直接复制给 Agent：

    请在当前已连接的服务器上安装或更新 SelfGuide，模式选择为 <server|local>。
    仓库地址：https://github.com/JackBo04/selfguide。
    请先检查是否已有该仓库：已有则复用并安全更新，没有则克隆；进入仓库后先完整阅读 docs/agent-install.md，再严格按照其中说明完成你能够执行的安装、配置检查和合成验收。
    尽量复用现有网页登录、浏览器配置和已有 SelfGuide 任务，不要无必要创建新登录或丢弃任务状态。
    只有确实需要用户登录、人机验证，或存在必须由用户决定的选项时再询问用户。

## 当前能力

- **写作模块**：已启用。
- **绘图模块**：预留，尚未实现。
- **实验迭代模块**：预留，尚未实现。

预留模块不是已实现能力；后续会在完成实现和验证后再更新说明。

## 验证状态

当前已有 **24 项 Python 回归测试**。server 与 local 均通过受控 Chromium 下的扩展收发、附件、恢复和多窗口隔离测试；真实服务器也完成了多窗口文字闭环与真实附件双向交接。

本地用户电脑仍未完成实机验收，因此项目不宣称所有平台均已验证。Preview 阶段建议在重要任务中保留原始材料和执行记录。

## 文档

- [Server 浏览器](docs/server-browser.md)
- [Local 浏览器](docs/local-browser.md)
- [Agent 安装](docs/agent-install.md)
- [更新指南](docs/update.md)
- [验证说明](docs/validation.md)
- [MIT License](LICENSE)

项目仓库：[github.com/JackBo04/selfguide](https://github.com/JackBo04/selfguide)

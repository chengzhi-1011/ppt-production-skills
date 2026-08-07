# PPT Production Skills

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

这是可公开访问和复用的开源仓库，源代码采用 Apache License 2.0。

面向 Windows + Codex 的整套 PPT 生产工作流：

1. 分析 Word、PDF、Excel、PPT、图片等材料；
2. 建立证据台账并生成逐页汇报大纲；
3. 经审批后，在 ChatGPT 网页端按原始模板逐页生成设计图；
4. 将每页重建为对象级可编辑 PowerPoint；
5. 使用真实 Microsoft PowerPoint 打开、导出并逐页验证。

生成的页面图片只是中间设计稿，最终交付不能是整页截图拼成的 PPT。

## 包含的 Skills

| Skill | 作用 |
|---|---|
| `ppt-production-pipeline` | 全流程编排、审批、哈希绑定和质量门禁 |
| `ppt-material-to-outline` | 材料盘点、证据约束与逐页大纲 |
| `chatgpt-template-slide-images` | 专用可见浏览器中的模板化逐页出图 |
| `image-to-editable-ppt` | 图片到对象级可编辑 PPT 的重建引擎 |

四个目录必须保持在同一个 `skills` 目录下，因为编排技能会读取相邻技能的契约和脚本。

## 环境要求

- Windows 10/11；
- Microsoft PowerPoint 桌面版；
- Codex Desktop；
- Python 3.10 或更高版本；
- PowerShell 5.1 或更高版本；
- Node.js/npm；
- `agent-browser`：`npm install -g agent-browser`；
- `uv`，用于隔离安装 `editppt`。

## 安装

在 PowerShell 中执行：

```powershell
git clone https://github.com/chengzhi-1011/ppt-production-skills.git
cd ppt-production-skills
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

安装器会：

- 将四个 Skills 复制到 `%USERPROFILE%\.codex\skills`；
- 首次安装 `editppt` 命令；仅在使用 `-ReplaceEditppt` 明确授权时更新已有工具；
- 注册 `%USERPROFILE%\.local\bin\pptflow.cmd`；
- 创建独立的 ChatGPT 浏览器 Profile；
- 运行 `pptflow doctor`，确认依赖与 PowerPoint 注册状态；
- 在替换旧版本前创建带时间戳的可恢复备份，并在安装失败时恢复 Skills、PATH 和命令包装器。

安装器不会读取、复制或上传浏览器凭证，也不会替用户完成登录或 CAPTCHA。

### 安装与升级选项

已有仓库副本时，升级前先在仓库目录执行 `git pull --ff-only`，确认本地没有未合并修改后，再运行下列安装命令。

- 首次安装会用 `uv tool install` 进行非 editable 安装；因此以后移动或卸载 Skills 不会让已安装的 `editppt` 因源码路径消失而损坏。
- 已有本仓库通过 uv 安装的 `editppt` 时，可用 `-SkipEditpptInstall` 保留当前工具，或用 `-ReplaceEditppt` 明确升级。安装器不会覆盖来源不明的同名命令。
- `-SkipEditpptInstall` 要求 PATH 中已经存在可用的 `editppt`；它不是在缺少该命令时跳过依赖检查。
- 若目标目录中已有不带本仓库归属标记的同名 Skills 或 `pptflow.cmd`，安装器默认拒绝覆盖。确认备份范围后才可使用 `-ForceReplace`。
- 全新安装的 `editppt` 若在后续步骤失败，会随本次安装一起回滚；显式升级既有工具前，请自行确认是否需要保留旧版本。

例如，只升级 Skills 并保留当前 `editppt`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -SkipEditpptInstall
```

明确升级由本仓库管理的 uv 工具：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -ReplaceEditppt
```

## 可选云服务配置

图像生成/编辑使用官方 OpenAI API，或用户明确配置的 OpenAI 兼容 API。请通过进程环境或系统密钥管理器提供 `OPENAI_API_KEY`；不要把密钥写进命令参数、聊天、项目文件或 `~/.editppt/config.yaml`。非密钥配置可写入本地配置：

```powershell
editppt config --model gpt-image-2
editppt config --base-url https://example.test/v1 --model openai/gpt-image-2
```

设置第三方 `OPENAI_BASE_URL` 后，该服务会收到 API Key、提示词以及本次任务所需的图片/蒙版。请只使用你信任并明确选择的服务。为避免凭据被写入配置或日志，请勿把用户名、密码或 token 放进 URL；程序会拒绝带身份信息、查询参数或片段的地址，远程服务必须使用 HTTPS（仅本机回环地址可使用 HTTP）。

PaddleOCR-VL 是可选的内容识别后端。其令牌同样只通过 `PADDLE_OCR_TOKEN` 环境变量或密钥管理器提供；未配置时使用本地几何文字检测。

## 使用

在 Codex 中附上材料和模板，然后发送：

```text
使用 $ppt-production-pipeline 制作整套PPT。

用途：项目评审汇报
听众：专家评审组
页数：20页左右
汇报时间：15分钟
语言：中文
比例：16:9
先给我逐页大纲，等我确认后再继续。
允许将本任务所列脱敏材料上传到 ChatGPT 网页端；每页最多1次纠错。
```

大纲确认后回复：

```text
大纲通过，开始做。
```

首次进入 ChatGPT 时，如出现登录或人机验证，请在弹出的专用可见浏览器中手动完成。技能不会读取密码、Cookie、验证码或账号令牌。

## 命令行入口

```powershell
pptflow doctor
pptflow --help
pptflow status <run-directory>
```

`pptflow` 负责状态与质量门禁；日常使用仍建议通过 Codex 直接调用 `$ppt-production-pipeline`。

## 验证

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

验证包括 Skill 结构、Python/PowerShell 语法、私有接口与潜在密钥扫描、客户/生成资产排除、PDF 与图像后端单元测试，以及 Git 工作区检查。CLI 依赖由 `uv.lock` 锁定，GitHub Actions 会在 Windows 环境重复执行验证和构建。

## 卸载

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1 -ConfirmRemoval
```

卸载器仅处理带有本仓库安装归属标记的 Skills，并把它们和未被用户修改的 `pptflow.cmd` 移到带时间戳的备份目录。任一步失败都会恢复已完成的移动；它不会删除 ChatGPT 专用浏览器 Profile、`editppt` 配置或任何凭证。

非 editable 的 `editppt` uv 工具会被保留。如需一并删除，请在确认没有其他流程依赖后执行：

```powershell
uv tool uninstall image-to-editable-ppt-cli
```

## 安全与许可边界

- 仓库不得包含 `.env`、API Key、OCR Token、Cookie、浏览器状态、项目材料、生成图片或 PPT 运行目录。
- `image-to-editable-ppt` 不读取或复用其他应用的登录令牌；API/OCR 密钥只从当前进程环境读取。
- 源代码采用 [Apache License 2.0](LICENSE)。第三方依赖与外部服务保留各自许可证和条款，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- 用户生成的 PPT/图片不会仅因本工具的源代码许可证而自动采用 Apache-2.0；输出仍受输入材料权利和所选服务条款约束。

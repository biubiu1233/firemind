# FireMind

WARDOGS 智能炮兵副官：用自然语言输入炮位/目标坐标，自动给出方位角、距离与仰角 MIL；支持试射修正对话。

> 非官方社区工具，与 BULKHEAD / WARDOGS 无关。射表数据参考社区维护版本，仅供学习与交流。

## 群友怎么用（推荐：公网链接）

**你不需要 24 小时开着 `./run.sh`。** 把 FireMind 部署到云平台后，会得到一个固定 HTTPS 链接，群友随时用浏览器打开即可（手机/电脑都行），无需安装 Python。

### 你要做的一次性部署（约 15 分钟）

1. 把 `/root/firemind` 推到 **GitHub**（见下方「发布到 GitHub」）。
2. 打开 [Render](https://render.com) → 用 GitHub 登录 → **New → Blueprint** → 选你的 `firemind` 仓库（根目录有 `render.yaml`）。
3. 部署完成后复制 **Public URL**，例如 `https://firemind-xxxx.onrender.com`。
4. （可选）在 Render 环境变量里加 `OPENAI_API_KEY`，否则仍用规则引擎，功能完整。

**Render 免费版说明：** 一段时间没人访问会休眠，**第一次打开可能要等 20～40 秒**；之后同一段时间里会快很多。若群友反馈「打不开」，让他们多等半分钟再刷新。

### 可以直接复制到群里的文案

```text
FireMind · WARDOGS 炮兵诸元小工具（非官方）
链接：https://你的地址.onrender.com

用法：
1. 浏览器打开，选 L81 或 SPH-2
2. 游戏里右键地图复制炮位/目标坐标，粘贴到输入框
3. SPH 约 1750m 内首发按页面「高弹道」读数；低/高 MIL 不要混用
4. 试射后把落点坐标贴到「试射修正」

不用装软件。有问题截图距离、选的弹道、RNG/MIL 发我。
```

作品集介绍页（可选）：`https://你的地址.onrender.com/portfolio.html`

无 LLM Key 时仍可用（规则引擎 + 弹道计算）。

## 本地运行

```bash
git clone https://github.com/你的用户名/firemind.git
cd firemind
pip install -r requirements.txt
./run.sh
```

浏览器打开：http://127.0.0.1:8787

可选：复制 `.env.example` 为 `.env`，填入 `OPENAI_API_KEY` 启用 LLM。

## 发布到 GitHub

在项目目录执行：

```bash
cd firemind
git init -b main
git add .
git commit -m "Initial commit: FireMind MVP"
```

在 [GitHub 新建仓库](https://github.com/new)（不要勾选 README），然后：

```bash
git remote add origin https://github.com/你的用户名/firemind.git
git push -u origin main
```

**注意：** 不要把 `.env` 或 API Key 提交上去（已在 `.gitignore` 中忽略）。

## 部署到公网（免费示例：Render）

1. 代码先推到 GitHub。
2. 打开 [Render](https://render.com) 注册并连接 GitHub。
3. **New → Blueprint**，选择本仓库（仓库根目录需有 `render.yaml`）。
4. 如需 LLM，在 Render 环境变量里添加 `OPENAI_API_KEY`（可选）。
5. 部署完成后复制 **Public URL** 分享给群友。

其他平台（Railway、Fly.io、自己的 VPS）同理：`pip install -r requirements.txt`，启动命令：

```bash
export PYTHONPATH=.
gunicorn --bind 0.0.0.0:8787 app:app
```

## 使用示例

```
迫击炮，炮位 x98.43 y110.38，目标 x94.53 y109.03
```

试射后：

```
偏右20米，偏短30米
```

## 简历 / 作品集链接怎么写

部署到公网后，建议简历里放 **一个主链接**（作品集页），页内再链到 Demo：

| 简历写法 | URL 示例 |
|----------|----------|
| **作品集（推荐放简历）** | `https://你的域名/portfolio.html` |
| **在线 Demo** | `https://你的域名/` |
| **源码** | `https://github.com/你/firemind` |

**项目经历一行示例：**

> **FireMind** — WARDOGS 智能炮兵副官（AI 产品 MVP）  
> 自然语言下达射击任务 + 规则弹道引擎 + 试射修正 Copilot；0→1 完成需求、编排方案与可部署 Demo  
> 作品集：https://xxx/portfolio.html · Demo：https://xxx/

部署后编辑 `portfolio.html` 中 GitHub 按钮，或访问  
`/portfolio.html?github=https://github.com/你的用户名/firemind`

**其他载体（可选）：** 飞书/Notion 写一篇案例，首屏放同样三块：痛点 → 方案架构 → Demo 链接。

## 许可证

项目代码可自由使用与修改。`data/weapons.json` 射表来源于社区开源数据，请保留出处说明。

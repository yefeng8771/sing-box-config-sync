# sing-box-config-sync

[![Sync Sources](https://github.com/yefeng8771/sing-box-config-sync/actions/workflows/sync.yml/badge.svg)](https://github.com/yefeng8771/sing-box-config-sync/actions/workflows/sync.yml)
[![content](https://img.shields.io/badge/content-upstream%20owned-blue)](#上游归属与免责声明)

自动聚合同步多个上游 sing-box 仓库 / Gist 中的配置、规则与文档到本仓库,每天定时更新。声明式源清单(`sources.json`),新增源只需加一条配置,无需改代码。

## 上游源

| 目录 | 类型 | 上游 | 说明 |
| --- | --- | --- | --- |
| `sources/repcz-tool/` | github | [`Repcz/Tool@X` / sing-box`](https://github.com/Repcz/Tool/tree/X/sing-box) | 规则集 (`Rules/`) + v1.14.x 客户端/服务端配置模板 |
| `sources/pk-box/` | github | [`huixiao666/pk-box@main`](https://github.com/huixiao666/pk-box) | beta、1.14 和 1.15 alpha 配置模板 |
| `sources/gist-chizi/` | gist | [CHIZI-0618 的 gist](https://gist.github.com/CHIZI-0618/35f59df7b17bf66ea988d775aaf76152) | 上游原始配置 |
| `sources/ref1nd-docs/` | github | [`reF1nd/sing-box@reF1nd-testing-next`](https://github.com/reF1nd/sing-box/tree/reF1nd-testing-next/docs) | 中英文文档及 schema |

## 目录结构

所有上游内容统一收纳在 `sources/` 下,每个源一个子目录,子目录内保留上游原有的相对结构:

```
sources/
├── repcz-tool/      ← github.com/Repcz/Tool @ branch X / sing-box/
│   ├── Rules/       规则集
│   └── v1.14.x/、v1.15.x/ 配置模板
├── pk-box/          ← beta、1.14 与 1.15 alpha 的上游原始模板
├── gist-chizi/      ← gist 35f59df7... 的原始配置
├── ref1nd-docs/     ← reF1nd-testing-next 的文档
└── boxproxy-box/    ← 旧存档，已停止同步，保留原内容

```

## 工作机制

1. **`sources.json`** —— 声明式源清单(类型/仓库/分支/路径/目标目录/说明)。
2. **`sync.py`** —— 同步脚本:
   - `github` 类型:用 `git clone --filter=blob:none --sparse --depth=1` 仅拉取目标子目录,复制到 `sources/<name>/`。**`path` 留空**则镜像整个分支(`sparse-checkout disable`),整仓复制到 `sources/<name>/`(自动排除上游 `.git`)。
   - `gist` 类型:用 Gist API 拉取文件,写入 `sources/<name>/`。带 token 时若返回 401/403(如 Actions 的 `GITHUB_TOKEN` 无 gist scope)会**自动回退匿名**重试(公开 gist 匿名可读)。
   - 每个源的 commit / version 记录到 `.sync-meta.json`。
   - 各源全部成功后才进入后续步骤；失败时不会提交或推送。
3. **`.github/workflows/sync.yml`** —— GitHub Actions,每天 00:00 UTC(08:00 北京)定时触发,也可在 Actions 页手动触发(`workflow_dispatch`)。运行测试、拉取和规范化均成功后才以 `github-actions[bot]` 身份直接提交到 main；无变更则跳过。main 若在运行期间发生变更，非快进推送会失败，等待下一次运行，不强制覆盖。

4. **`normalize.py`** —— 规范化支持的来源到 `normalized/`。`pk-box` 模板原样保存在 `sources/pk-box/`，不生成规范化副本：部分文件含占位符或不是有效 JSON，不能当成可直接运行的配置。其他来源出现 JSON 解析或输出冲突时任务失败，不提交部分结果；原有规范化文件保持不变。旧 `boxproxy-box` 文件保留但不再更新，sing-box 核心构建不受此项移除影响。

## 添加 / 修改源

编辑 `sources.json` 增加一条即可,无需改代码:

```json
{
  "name": "my-new-source",
  "type": "github",
  "repo": "owner/repo",
  "branch": "main",
  "path": "some/subdir",
  "dest": "sources/my-new-source",
  "desc": "可选说明"
}
```

- `type`: `github`(仓库子目录)或 `gist`(Gist)
- `gist` 类型用 `gist_id` 字段代替 `repo`/`branch`/`path`
- `dest` 为本仓库内的目标目录(一般放在 `sources/` 下)
- `desc` 为可选说明(用于文档可读性)

## 本地手动运行

```bash
python sync.py                          # 匿名访问(受 API 限速,公开内容均可读)
GITHUB_TOKEN=xxx python sync.py         # 带 token,更稳(gist 失败会自动回退匿名)
```

## 同步状态

各源当前 commit / version 见 [.sync-meta.json](.sync-meta.json);最近一次同步结果见 [Actions 运行记录](https://github.com/yefeng8771/sing-box-config-sync/actions/workflows/sync.yml)。

## 上游归属与免责声明

本仓库仅做自动化镜像聚合,**不拥有也不改动上游内容的版权**。所有配置、规则、文档的版权归原作者所有,具体许可以各上游仓库 / Gist 声明为准。如上游维护者不希望被聚合,可提 issue 或 PR 移除对应源。

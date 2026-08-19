# sing-box-config-sync

自动聚合同步多个上游 sing-box 仓库 / Gist 中的部分文件到本仓库,每天定时更新。

## 目录结构

所有上游内容统一收纳在 `sources/` 下,每个源一个子目录,子目录内保留上游原有的相对结构:

```
sources/
├── repcz-tool/      ← github.com/Repcz/Tool @ branch X / sing-box/
│   ├── Rules/       规则集 (上百个 .json + .srs)
│   └── v1.14.x/     Client / Server 配置模板
├── pk-box/          ← github.com/huixiao666/pk-box @ main / beta/
│   ├── *.json       各 1.14.0-beta.* 版本配置 (含中文文件名)
│   └── 注释/         带注释的配置
├── gist-chizi/      ← gist 35f59df7... (CHIZI-0618)
│   └── config.jsonc 自用配置 (DNS 替换为公共 DNS)
└── ref1nd-docs/     ← github.com/reF1nd/sing-box @ reF1nd-testing / docs/
    └── ...          官方文档 (中英 .md + schema.json + 安装脚本)
```

## 工作机制

1. `sources.json` —— 声明式源清单(类型/仓库/分支/路径/目标目录)。
2. `sync.py` —— 同步脚本:
   - `github` 类型:用 `git clone --filter=blob:none --sparse --depth=1` 仅拉取目标子目录,复制到 `sources/<name>/`。
   - `gist` 类型:用 Gist API 拉取文件,写入 `sources/<name>/`。
   - 每个源的 commit / version 记录到 `.sync-meta.json`。
   - 采用「临时目录 → 原子替换」写法,某源拉取失败时其旧内容保持不变。
3. `.github/workflows/sync.yml` —— GitHub Actions,每天 00:00 UTC(08:00 北京)定时触发,也可在 Actions 页手动触发(workflow_dispatch)。检测到变更才以 `github-actions[bot]` 身份提交并推送。

## 添加 / 修改源

编辑 `sources.json` 增加一条即可,无需改代码:

```json
{
  "name": "my-new-source",
  "type": "github",
  "repo": "owner/repo",
  "branch": "main",
  "path": "some/subdir",
  "dest": "sources/my-new-source"
}
```

- `type`: `github`(仓库子目录)或 `gist`(Gist)
- `gist` 类型用 `gist_id` 字段代替 `repo`/`branch`/`path`
- `dest` 为本仓库内的目标目录(一般放在 `sources/` 下)

## 本地手动运行

```bash
python sync.py            # 匿名访问(受 API 限速)
GITHUB_TOKEN=xxx python sync.py   # 带 token,更稳
```

## 同步状态

各源当前 commit / version 见 `.sync-meta.json`。

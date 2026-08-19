# 自定义规则集 (custom/)

本目录存放**用户自定义**的 sing-box rule_set，**不受 `sync.py` 同步影响**（sync.py 只管理 `sources/` 下的上游镜像，不会触碰 `custom/`）。daily auto-sync 的 `git add -A` 只会纳入本目录变更，无变更则跳过 commit，绝不删除或覆盖。

## 规则集

| 文件 | 用途 | 引用方向 |
|---|---|---|
| `direct-domains` | 自定义直连域名（VPS/订阅/CDN + 内网检测） | 直连 |
| `proxy-domains` | 自定义代理域名（按需添加，当前为空占位） | 代理 |

每个规则集提供两种格式（与上游 Repcz 规则集一致）：
- `.json` — HeadlessRuleSet v4 源文件，可读、易编辑
- `.srs` — 二进制编译产物，体积更小、加载更快

## direct-domains 内容

**VPS/订阅/CDN**（走直连）：
`hybgzs.com` `198707.xyz` `yi.uy`(覆盖 `bitder.yi.uy`) `proxyscrape.com` `jsdelivr.net` `githubusercontent.com`

**内网/网络连通性检测**（走内网/直连，避免被代理误拦）：
`lancache.steamcontent.com` `10099.com.cn` `msftconnecttest.com` `msftncsi.com` `local` `lan` `home` `internal` `corp`

## 在 sing-box 配置中引用

```json
"route": {
  "rule_set": [
    {
      "tag": "🛰️自定义直连",
      "type": "remote",
      "format": "binary",
      "url": "https://cdn.jsdelivr.net/gh/yefeng8771/sing-box-config-sync@main/custom/direct-domains.srs",
      "download_detour": "🎯全球直连"
    },
    {
      "tag": "🛰️自定义代理",
      "type": "remote",
      "format": "binary",
      "url": "https://cdn.jsdelivr.net/gh/yefeng8771/sing-box-config-sync@main/custom/proxy-domains.srs",
      "download_detour": "🎯全球直连"
    }
  ]
}
```

`route.rules` 引用：
```json
{"rule_set": ["🛰️自定义直连"], "outbound": "🎯全球直连"},
{"rule_set": ["🛰️自定义代理"], "outbound": "🐟全球代理"}
```

## 跨平台共用

域名规则集与 inbound 类型无关，路由器（eBPF 模式）与 Android（TUN 模式，boxproxy/box）共用同一 URL，改域名改 GitHub 一处即全设备同步。

> 注意：进程/协议维度的规则（如 `process_name`/`protocol`）与平台强相关，不宜放进此域名规则集，应各端在自身配置中单独定义。

## 更新流程

1. 编辑 `.json` 源文件
2. 编译 `.srs`：`sing-box rule-set compile <name>.json`（路由器 `/usr/bin/sing-box` 可用）
3. `git add custom/ && git commit -m "..." && git push`
4. jsdelivr CDN 有缓存（~10min），如需即时生效可走 `https://raw.githubusercontent.com/...` 或 `purge.jsdelivr.net`

## 同类规则集分类建议（未来扩展）

- `custom/direct-domains` — 自有服务/CDN/内网检测 → 直连
- `custom/proxy-domains` — 需强制走代理的域名 → 代理
- 如需更细分类（如 `ai-domains`/`streaming-domains`），可新增独立规则集，上游 Repcz 已有的同类集合（AI/Google/Telegram 等）则继续引用上游，避免重复维护。

# sing-box 跨版本演进权威知识库 (1.8 → 1.15 reF1nd-testing-next)

> 自动生成时间：`2026-10-29T11:40:01Z` | 文档基准：`reF1nd-testing-next` (`3cf2b01ce8eb`) | 锁定内核：`440f7a133539`

本知识库由 `build/extract_kb.py` 从上游官方文档 (`deprecated.zh.md`、`migration.zh.md`、`schema.json`)、实战带注释模板 (`pk-box`、`repcz-tool`、`gist-chizi`) 与本仓工程契约自动蒸馏生成，供 LLM / AI Skill / 自动化迁移器直接作为上下文使用。

## 1. 顶层 Schema 结构（1.15 唯一合法根字段）

根据最新同步的 `schema.json`（共包含 111 个类型定义），合法的顶层字段仅限以下列表：

| 顶层字段 | 用途说明 | 1.14 / 1.15 关键演进注意事项 |
| :--- | :--- | :--- |
| `$schema` | JSON Schema 声明 | 推荐指向 `https://raw.githubusercontent.com/reF1nd/sing-box/reF1nd-testing-next/docs/schema.json` |
| `log` | 日志配置 | 生产环境建议 `level: info`、`timestamp: true` |
| `dns` | DNS 服务器与分流规则 | **已移除** `independent_cache`；旧版 `address` 字符串格式已彻底废弃，必须用 `type` + `server`；IP 匹配规则需走 `evaluate` + `match_response` |
| `http_clients` | 共享 HTTP 客户端 **(1.14 新增)** | 替代废弃的 `download_detour`；用于远程 `rule_set`、`providers` 与 `services.dashboard` 下载 |
| `certificate_providers` | 共享证书提供者 **(1.14 新增)** | 替代废弃的 `tls.acme` 内联配置 |
| `endpoints` | 端点模块 **(1.11 新增)** | WireGuard、Tailscale、MASQUE、OpenVPN、OpenConnect 等均作为 Endpoint 配置 |
| `inbounds` | 入站连接 | `tun` 入站自 1.15 起**移除 `stack` 字段**（默认启用自有高性能 `sing-tun` 栈）；`sniff` 与 `domain_strategy` 已移入 `route.rules` |
| `outbounds` | 出站与策略组 | **已移除** `type: block` 与 `type: dns` 特殊出站（改用路由规则动作 `reject` / `hijack-dns`） |
| `providers` | 外部节点订阅提供者 | 远程订阅使用 `http_client` 指定下载客户端，禁用 `download_detour` |
| `route` | 路由规则与规则集 | 必须显式配置 `default_domain_resolver`（防自举死锁）与 `default_http_client`；`rule_set.tag` 支持数组批量展开 `{tag}` |
| `experimental` | 实验性功能 | `cache_file.store_rdrc` 已废弃，统一改为 `store_dns: true` |
| `services` | 面板与附加服务 | 支持 `api`（含内置 Zashboard 下载）、`derp`、`ccm`、`ocm`、`ssm-api`、`usbip` 等 |

## 2. 确定性 AST 迁移规则矩阵（1.8 → 1.15）

| 规则 ID | 废弃版本 | 移除版本 | 废弃写法 / 路径 | 1.15 标准写法（自动转换动作） |
| :--- | :--- | :--- | :--- | :--- |
| `MIG-115-TUN-STACK` | 1.15.0 | 1.17.0 | `inbounds[type=tun].stack` | 移除废弃的 TUN stack 参数（启用 1.15 sing-tun 原生新栈） |
| `MIG-114-DOWNLOAD-DETOUR` | 1.14.0 | 1.16.0 | `route.rule_set[*].download_detour / providers[*].download_detour` | 将 download_detour 迁移为共享 http_clients 与 http_client 引用 |
| `MIG-114-IMPLICIT-HTTP-CLIENT` | 1.14.0 | 1.16.0 | `http_clients / route.default_http_client` | 为远程规则集/Provider 显式补全 http_clients 与 route.default_http_client |
| `MIG-114-TLS-ACME` | 1.14.0 | 1.16.0 | `inbounds[*].tls.acme` | 将内联 tls.acme 迁移为 tls.certificate_provider |
| `MIG-114-DNS-INDEPENDENT-CACHE` | 1.14.0 | 1.16.0 | `dns.independent_cache` | 移除废弃的 dns.independent_cache 选项 |
| `MIG-114-CACHE-STORE-RDRC` | 1.14.0 | 1.16.0 | `experimental.cache_file.store_rdrc` | 将 experimental.cache_file.store_rdrc 替换为 store_dns |
| `MIG-114-DNS-RULE-LEGACY` | 1.14.0 | 1.16.0 | `dns.rules[*].(strategy|rule_set_ip_cidr_accept_empty|ip_cidr|ip_is_private)` | 清理废弃 DNS 规则项并为地址筛选字段补全 match_response |
| `MIG-112-DNS-SERVERS` | 1.12.0 | 1.14.0 | `dns.servers[*].address / dns.fakeip` | 将 1.11 旧版字符串 address DNS 服务器重构为 1.12+ 强类型服务器对象 |
| `MIG-112-OUTBOUND-DNS-RULE` | 1.12.0 | 1.14.0 | `dns.rules[*].outbound / route.default_domain_resolver` | 将废弃的 outbound:any DNS 规则迁移为 route.default_domain_resolver |
| `MIG-112-DIAL-DOMAIN-STRATEGY` | 1.12.0 | 1.14.0 | `outbounds[*].domain_strategy` | 将出站拨号字段 domain_strategy 迁移为 domain_resolver |
| `MIG-111-SPECIAL-OUTBOUNDS` | 1.11.0 | 1.13.0 | `outbounds[type=block|dns] -> route.rules[*].action` | 移除 block/dns 特殊出站并迁移为路由规则动作 (reject / hijack-dns) |
| `MIG-111-INBOUND-SNIFF` | 1.11.0 | 1.13.0 | `inbounds[*].(sniff|sniff_override_destination|sniff_timeout|domain_strategy)` | 将入站上的旧版 sniff 与 domain_strategy 提取为 route.rules 顶部动作 |
| `MIG-110-TUN-ADDRESS` | 1.10.0 | 1.12.0 | `inbounds[type=tun].inet4_address / inet6_address` | 合并 TUN 入站中分离的 IPv4/IPv6 地址与路由段字段 |
| `MIG-108-CLASH-CACHE` | 1.8.0 | 1.10.0 | `experimental.clash_api.cache_file` | 将 experimental.clash_api 下的旧版缓存字段迁移至 experimental.cache_file |

## 3. 核心语法迁移正反例对照（官方 `migration.zh.md` 提炼）

### [1.15.0] 迁移 TUN stack
- **规则要点**：自 1.15.0 起，sing-tun 使用自有 TCP/IP stack，极限性能、能效以及内存占用均大幅领先于所有旧实现。 移除 `stack` 参数以使用。 `stack` 参数自 sing-box 1.15.0 起废弃，并将在 sing-box 1.17.0 中被移除。
- **废弃写法（禁止生成）**：
```json
{
"inbounds": [
{
  "type": "tun",
  "address": ["172.18.0.1/30"],
  "stack": "system"
}
]
}
```
- **1.14/1.15 标准写法**：
```json
{
"inbounds": [
{
  "type": "tun",
  "address": ["172.18.0.1/30"]
}
]
}
```

### [1.14.0] 迁移内联 ACME 到证书提供者
- **规则要点**：TLS 中的内联 ACME 选项已废弃，且可以被证书提供者替代。 `tls.acme` 的大多数字段都可以原样迁移到 ACME 证书提供者中。 sing-box 1.14.0 新增字段参阅 [ACME](/zh/configuration/shared/certificate-provider/acme/) 页面。
- **废弃写法（禁止生成）**：
```json
{
"inbounds": [
{
  "type": "trojan",
  "tls": {
    "enabled": true,
    "acme": {
      "domain": ["example.com"],
      "email": "admin@example.com"
    }
  }
}
]
}
```
- **1.14/1.15 标准写法**：
```json
{
"inbounds": [
{
  "type": "trojan",
  "tls": {
    "enabled": true,
    "certificate_provider": {
      "type": "acme",
      "domain": ["example.com"],
      "email": "admin@example.com"
    }
  }
}
]
}
```

### [1.14.0] 迁移地址筛选字段到响应匹配
- **规则要点**：旧版地址筛选字段（不使用 `match_response` 的 `ip_cidr`、`ip_is_private`）已废弃， 旧版 `rule_set_ip_cidr_accept_empty` DNS 规则项也已废弃。当 DNS 规则使用了 sing-box 1.14.0 新增的功能（例如 `evaluate`、`match_response` 或 `ip_version`）时，
- **废弃写法（禁止生成）**：
```json
{
"dns": {
"rules": [
  {
    "rule_set": "geoip-cn",
    "action": "route",
    "server": "local"
  },
  {
    "action": "route",
    "server": "remote"
  }
]
}
}
```
- **1.14/1.15 标准写法**：
```json
{
"dns": {
"rules": [
  {
    "action": "evaluate",
    "server": "remote"
  },
  {
    "match_response": true,
    "rule_set": "geoip-cn",
    "action": "route",
    "server": "local"
  },
  {
    "action": "route",
    "server": "remote"
  }
]
}
}
```

### [1.14.0] 迁移 independent DNS cache
- **规则要点**：`independent_cache` 已废弃，直接移除该字段即可。
- **废弃写法（禁止生成）**：
```json
{
"dns": {
"independent_cache": true
}
}
```
- **1.14/1.15 标准写法**：
```json
{
"dns": {}
}
```

### [1.14.0] 迁移 store_rdrc
- **规则要点**：`store_rdrc` 已废弃，且可以被 `store_dns` 替代， 后者将完整的 DNS 缓存持久化到缓存文件中。
- **废弃写法（禁止生成）**：
```json
{
"experimental": {
"cache_file": {
  "enabled": true,
  "store_rdrc": true
}
}
}
```
- **1.14/1.15 标准写法**：
```json
{
"experimental": {
"cache_file": {
  "enabled": true,
  "store_dns": true
}
}
}
```

### [1.12.0] 迁移到新的 DNS 服务器格式
- **规则要点**：DNS 服务器已经重构。
- **废弃写法（禁止生成）**：
```json
{
  "dns": {
    "servers": [
      {
        "address": "local"
      }
    ]
  }
}
```
- **1.14/1.15 标准写法**：
```json
{
  "dns": {
    "servers": [
      {
        "type": "local"
      }
    ]
  }
}
```

### [1.12.0] 迁移 outbound DNS 规则项到域解析选项
- **规则要点**：旧的 `outbound` DNS 规则已废弃，且可新的域解析选项代替。
- **废弃写法（禁止生成）**：
```json
{
"dns": {
"servers": [
  {
    "address": "local",
    "tag": "local"
  }
],
"rules": [
  {
    "outbound": "any",
    "server": "local"
  }
]
},
"outbounds": [
{
  "type": "socks",
  "server": "example.org",
  "server_port": 2080
}
]
}
```
- **1.14/1.15 标准写法**：
```json
{
"dns": {
"servers": [
  {
    "type": "local",
    "tag": "local"
  }
]
},
"outbounds": [
{
  "type": "socks",
  "server": "example.org",
  "server_port": 2080,
  "domain_resolver": {
    "server": "local",
    "rewrite_ttl": 60,
    "client_subnet": "1.1.1.1"
  },
  // 或 "domain_resolver": "local",
}
],

// 或

"route": {
"default_domain_resolver": {
  "server": "local",
  "rewrite_ttl": 60,
  "client_subnet": "1.1.1.1"
}
}
}
```

### [1.12.0] 迁移出站域名策略选项到域名解析器
- **规则要点**：拨号字段中的 `domain_strategy` 选项已被弃用，可以用新的域名解析器选项替代。 请注意，由于 sing-box 1.12 中引入的一些新 DNS 服务器使用了拨号字段，一些人错误地认为 `domain_strategy` 与旧 DNS 服务器中的功能相同。
- **废弃写法（禁止生成）**：
```json
{
"outbounds": [
{
  "type": "socks",
  "server": "example.org",
  "server_port": 2080,
  "domain_strategy": "prefer_ipv4",
}
]
}
```
- **1.14/1.15 标准写法**：
```json
{
"dns": {
"servers": [
  {
    "type": "local",
    "tag": "local"
  }
]
},
"outbounds": [
{
  "type": "socks",
  "server": "example.org",
  "server_port": 2080,
  "domain_resolver": {
    "server": "local",
    "strategy": "prefer_ipv4"
  }
}
]
}
```

### [1.11.0] 迁移旧的特殊出站到规则动作
- **规则要点**：旧的特殊出站已被弃用，且可以被规则动作替代。
- **废弃写法（禁止生成）**：
```json
{
  "outbounds": [
    {
      "type": "block",
      "tag": "block"
    }
  ],
  "route": {
    "rules": [
      {
        ...,

        "outbound": "block"
      }
    ]
  }
}
```
- **1.14/1.15 标准写法**：
```json
{
  "route": {
    "rules": [
      {
        ...,

        "action": "reject"
      }
    ]
  }
}
```

### [1.11.0] 迁移旧的入站字段到规则动作
- **规则要点**：入站选项已被弃用，且可以被规则动作替代。
- **废弃写法（禁止生成）**：
```json
{
"inbounds": [
{
  "type": "mixed",
  "sniff": true,
  "sniff_timeout": "1s",
  "domain_strategy": "prefer_ipv4"
}
]
}
```
- **1.14/1.15 标准写法**：
```json
{
"inbounds": [
{
  "type": "mixed",
  "tag": "in"
}
],
"route": {
"rules": [
  {
    "inbound": "in",
    "action": "resolve",
    "strategy": "prefer_ipv4"
  },
  {
    "inbound": "in",
    "action": "sniff",
    "timeout": "1s"
  }
]
}
}
```

### [1.11.0] 迁移 direct 出站中的目标地址覆盖字段到路由字段
- **规则要点**：direct 出站中的目标地址覆盖字段已废弃，且可以被路由字段替代。
- **废弃写法（禁止生成）**：
```json
{
"outbounds": [
{
  "type": "direct",
  "override_address": "1.1.1.1",
  "override_port": 443
}
]
}
```
- **1.14/1.15 标准写法**：
```json
{
"route": {
"rules": [
  {
    "action": "route-options", // 或 route
    "override_address": "1.1.1.1",
    "override_port": 443
  }
]
}
}
```

### [1.11.0] 迁移 WireGuard 出站到端点
- **规则要点**：WireGuard 出站已被弃用，且可以被端点替代。
- **废弃写法（禁止生成）**：
```json
{
"outbounds": [
{
  "type": "wireguard",
  "tag": "wg-out",

  "server": "127.0.0.1",
  "server_port": 10001,
  "system_interface": true,
  "gso": true,
  "interface_name": "wg0",
  "local_address": [
    "10.0.0.1/32"
  ],
  "private_key": "<private_key>",
  "peer_public_key": "<peer_public_key>",
  "pre_shared_key": "<pre_shared_key>",
  "reserved": [0, 0, 0],
  "mtu": 1408
}
]
}
```
- **1.14/1.15 标准写法**：
```json
{
"endpoints": [
{
  "type": "wireguard",
  "tag": "wg-ep",
  "system": true,
  "name": "wg0",
  "mtu": 1408,
  "address": [
    "10.0.0.2/32"
  ],
  "private_key": "<private_key>",
  "listen_port": 10000,
  "peers": [
    {
      "address": "127.0.0.1",
      "port": 10001,
      "public_key": "<peer_public_key>",
      "pre_shared_key": "<pre_shared_key>",
      "allowed_ips": [
        "0.0.0.0/0"
      ],
      "persistent_keepalive_interval": 30,
      "reserved": [0, 0, 0]
    }
  ]
}
]
}
```

### [1.10.0] TUN 地址字段已合并
- **规则要点**：`inet4_address` 和 `inet6_address` 已合并为 `address`， `inet4_route_address` 和 `inet6_route_address` 已合并为 `route_address`， `inet4_route_exclude_address` 和 `inet6_route_exclude_address` 已合并为 `route_exclude_address`。
- **废弃写法（禁止生成）**：
```json
{
"inbounds": [
{
  "type": "tun",
  "inet4_address": "172.19.0.1/30",
  "inet6_address": "fdfe:dcba:9876::1/126",
  "inet4_route_address": [
    "0.0.0.0/1",
    "128.0.0.0/1"
  ],
  "inet6_route_address": [
    "::/1",
    "8000::/1"
  ],
  "inet4_route_exclude_address": [
    "192.168.0.0/16"
  ],
  "inet6_route_exclude_address": [
    "fc00::/7"
  ]
}
]
}
```
- **1.14/1.15 标准写法**：
```json
{
"inbounds": [
{
  "type": "tun",
  "address": [
    "172.19.0.1/30",
    "fdfe:dcba:9876::1/126"
  ],
  "route_address": [
    "0.0.0.0/1",
    "128.0.0.0/1",
    "::/1",
    "8000::/1"
  ],
  "route_exclude_address": [
    "192.168.0.0/16",
    "fc00::/7"
  ]
}
]
}
```

### [1.8.0] 将缓存文件从 Clash API 迁移到独立选项
- **废弃写法（禁止生成）**：
```json
{
"experimental": {
"clash_api": {
  "cache_file": "cache.db", // 默认值
  "cahce_id": "my_profile2",
  "store_mode": true,
  "store_selected": true,
  "store_fakeip": true
}
}
}
```
- **1.14/1.15 标准写法**：
```json
{
"experimental"  : {
"cache_file": {
  "enabled": true,
  "path": "cache.db", // 默认值
  "cache_id": "my_profile2",
  "store_fakeip": true
}
}
}
```

### [1.8.0] 迁移 GeoIP 到规则集
- **废弃写法（禁止生成）**：
```json
{
"route": {
"rules": [
  {
    "geoip": "private",
    "outbound": "direct"
  },
  {
    "geoip": "cn",
    "outbound": "direct"
  },
  {
    "source_geoip": "cn",
    "outbound": "block"
  }
],
"geoip": {
  "download_detour": "proxy"
}
}
}
```
- **1.14/1.15 标准写法**：
```json
{
"route": {
"rules": [
  {
    "ip_is_private": true,
    "outbound": "direct"
  },
  {
    "rule_set": "geoip-cn",
    "outbound": "direct"
  },
  {
    "rule_set": "geoip-us",
    "rule_set_ipcidr_match_source": true,
    "outbound": "block"
  }
],
"rule_set": [
  {
    "tag": "geoip-cn",
    "type": "remote",
    "format": "binary",
    "url": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-cn.srs",
    "download_detour": "proxy"
  },
  {
    "tag": "geoip-us",
    "type": "remote",
    "format": "binary",
    "url": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-us.srs",
    "download_detour": "proxy"
  }
]
},
"experimental": {
"cache_file": {
  "enabled": true // required to save rule-set cache
}
}
}
```

### [1.8.0] 迁移 Geosite 到规则集
- **废弃写法（禁止生成）**：
```json
{
"route": {
"rules": [
  {
    "geosite": "cn",
    "outbound": "direct"
  }
],
"geosite": {
  "download_detour": "proxy"
}
}
}
```

## 4. 工程红线与真实踩坑防呆（Red Lines）

- **[REDLINE-GITHUBUSERCONTENT-DIRECT] 禁止将 githubusercontent.com 放入直连规则集 (direct-domains)**：实测 release-assets.githubusercontent.com 走家宽直连仅 4.5-6.7 KB/s 且伴随 TLS RST/断流，走代理节点可达 7.2 MB/s（差三个数量级）。必须保留在 proxy-domains 中。
- **[REDLINE-AUTO-REDIRECT-MARK-CONFLICT] TUN auto_redirect 与 route.default_mark / outbound.routing_mark 互斥**：启用 tun.auto_redirect 时（如 OpenWrt fw4 nftables），不可同时设置 route.default_mark 或出站 routing_mark，否则会导致内核路由标记冲突。
- **[REDLINE-AUTO-REDIRECT-EXCLUDE-CONFLICT] auto_redirect_disable_mark_mode 与 route_exclude_address_set 互斥**：TUN 入站中 auto_redirect_disable_mark_mode 与 route_exclude_address_set（如 [geoip-cn]）不可同时开启，二者只能选其一。
- **[REDLINE-BOOTSTRAP-RESOLVER] 1.14+ 必须显式指定 route.default_domain_resolver 或 DoH domain_resolver**：1.12 起废弃隐式域名解析，DoH 节点或代理出站若未指定指向直连/hosts 的 domain_resolver，会导致启动时 DNS 自举死锁。

## 5. 实战高频字段设计意图（提炼自 `pk-box` 1.15 注释模板）

| 字段名 | 示例值 | 实战作用与排障解释 |
| :--- | :--- | :--- |
| `log` | `{` | 日志模块：控制日志级别、时间戳等输出行为 |
| `level` | `"trace"` | 日志级别设为 trace，会输出最详细的调试信息（含内部状态、DNS 竞速过程），适合排查问题；日常稳定使用可改为 info 或 warn 以减少日志量 |
| `timestamp` | `true` | 在每条日志前附加时间戳，便于对照事件发生顺序（尤其在调试 DNS race 和路由匹配时非常有用） |
| `dns` | `{` | DNS 模块：定义 DNS 服务器、DNS 规则以及 DNS 缓存行为 |
| `servers` | `[` | DNS 服务器列表，每个元素声明一个可用于解析的 DNS 后端 |
| `type` | `"hosts"` | 服务器类型为 hosts，不从网络查询，而是直接返回 predefined 中预设的 IP |
| `tag` | `"hosts_fix"` | 该服务器的标签为 hosts_fix，后续 server 或 domain_resolver 字段通过这个标签引用它 |
| `predefined` | `{` | 静态映射表：键是域名，值是直接返回的 IP 列表 |
| `cloudflare-dns.com` | `[` | 为 cloudflare-dns.com 预设 IP，避免解析这个 DoH 服务器域名时产生循环依赖 |
| `dns.google` | `[` | 为 dns.google 预设 IP，理由同上：DoH 服务器域名不能再用 DoH 去解析自身 |
| `raw.githubusercontent.com` | `[` | 为 GitHub raw 域名预设 IP，确保规则集下载不被 DNS 解析阻塞或污染 |
| `type` | `"local"` | 类型为 local，调用操作系统提供的 DNS 解析（在 Android 上通常是系统 DNS 或 netd 解析器） |
| `tag` | `"pkpk-dns"` | 标签为 pkpk-dns，后续国内域名、华为域名、微软/苹果中国域名等规则会指向这个服务器，实现“国内域名用本地 DNS 解析”的分流 |
| `type` | `"https"` | 类型为 https，表示使用 DNS over HTTPS（DoH）协议进行查询[reference:3] |
| `tag` | `"cf-dns"` | 标签为 cf-dns，供 DNS 规则和 final 字段引用 |

## 6. 1.15 标准契约生成范式（Best-Practice Skeleton）

```json
{
  "$schema": "https://raw.githubusercontent.com/reF1nd/sing-box/reF1nd-testing-next/docs/schema.json",
  "log": {
    "level": "info",
    "timestamp": true
  },
  "http_clients": [
    {
      "tag": "proxy-hc",
      "detour": "PROXY",
      "version": 2
    }
  ],
  "dns": {
    "servers": [
      {
        "tag": "hosts-bootstrap",
        "type": "hosts",
        "predefined": {
          "cloudflare-dns.com": [
            "1.1.1.1",
            "1.0.0.1"
          ]
        }
      },
      {
        "tag": "proxy-dns",
        "type": "https",
        "server": "cloudflare-dns.com",
        "domain_resolver": "hosts-bootstrap",
        "detour": "PROXY"
      },
      {
        "tag": "direct-dns",
        "type": "udp",
        "server": "223.5.5.5"
      }
    ],
    "rules": [
      {
        "rule_set": [
          "repcz-ads"
        ],
        "action": "reject"
      },
      {
        "rule_set": [
          "geosite-cn"
        ],
        "action": "route",
        "server": "direct-dns"
      }
    ],
    "final": "proxy-dns",
    "optimistic": true
  },
  "inbounds": [
    {
      "type": "tun",
      "tag": "tun-in",
      "address": [
        "172.19.0.1/30",
        "fdfe:dcba:9876::1/126"
      ],
      "auto_route": true,
      "auto_redirect": true,
      "route_exclude_address_set": [
        "geoip-cn"
      ]
    }
  ],
  "outbounds": [
    {
      "type": "selector",
      "tag": "PROXY",
      "outbounds": [
        "DIRECT"
      ],
      "default": "DIRECT"
    },
    {
      "type": "direct",
      "tag": "DIRECT"
    }
  ],
  "route": {
    "default_domain_resolver": "direct-dns",
    "default_http_client": "proxy-hc",
    "rules": [
      {
        "action": "sniff"
      },
      {
        "protocol": "dns",
        "action": "hijack-dns"
      },
      {
        "rule_set": [
          "repcz-ads"
        ],
        "action": "reject"
      },
      {
        "rule_set": [
          "geosite-cn",
          "geoip-cn"
        ],
        "outbound": "DIRECT"
      }
    ],
    "rule_set": [
      {
        "type": "remote",
        "tag": [
          "geosite-cn",
          "geoip-cn"
        ],
        "format": "binary",
        "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/{tag}.srs",
        "http_client": "proxy-hc",
        "update_interval": "24h"
      }
    ],
    "final": "PROXY"
  },
  "experimental": {
    "cache_file": {
      "enabled": true,
      "path": "/etc/sing-box/cache.db",
      "store_dns": true
    }
  }
}
```

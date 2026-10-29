#!/usr/bin/env python3
"""Extract structured migration rules and LLM knowledge pack from synced upstream docs.

Reads:
  - normalized/ref1nd-docs/deprecated.zh.md
  - normalized/ref1nd-docs/migration.zh.md
  - normalized/ref1nd-docs/schema.json
  - sources/pk-box/ annotated templates
  - .sync-meta.json & kernel.lock

Outputs (deterministic — no wall-clock timestamp, so unchanged inputs produce identical bytes):
  - dist/migration-rules.json  (machine-readable AST migration rules + schema summary)
  - dist/sing-box-llm-kb.md    (high-density LLM knowledge base & prompt context)
"""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def parse_deprecated_md(md_text: str) -> list[dict]:
    """Parse versioned deprecation entries from deprecated.zh.md."""
    items = []
    current_version = "unknown"
    current_title = None
    buf = []

    def flush():
        if current_title:
            body = "\n".join(buf).strip()
            link_match = re.search(r"\((/zh/migration/#[^)]+)\)", body)
            removed_match = re.search(r"将在\s*sing-box\s*([0-9.]+)\s*中被移除|已在\s*sing-box\s*([0-9.]+)\s*中被移除", body)
            removed_ver = None
            if removed_match:
                removed_ver = removed_match.group(1) or removed_match.group(2)
            items.append({
                "version_deprecated": current_version,
                "version_removed": removed_ver,
                "title": current_title,
                "description": re.sub(r"\n+", " ", body),
                "migration_anchor": link_match.group(1) if link_match else None,
            })

    for line in md_text.splitlines():
        m_ver = re.match(r"^##\s+([0-9.]+)\s*$", line)
        if m_ver:
            flush()
            current_title = None
            buf = []
            current_version = m_ver.group(1)
            continue
        m_item = re.match(r"^####\s+(.+?)\s*$", line)
        if m_item:
            flush()
            current_title = m_item.group(1)
            buf = []
            continue
        if current_title is not None:
            buf.append(line)
    flush()
    return items


def parse_migration_md(md_text: str) -> list[dict]:
    """Extract before/after migration examples from migration.zh.md."""
    sections = []
    current_ver = "unknown"
    current_h3 = None
    buf = []

    def extract_code_blocks(text: str) -> dict:
        deprecated_blocks = re.findall(
            r'===\s*":material-card-remove:[^"]*"\s*\n+((?:\s{4,}.*\n|\s*\n)+)',
            text,
        )
        migrated_blocks = re.findall(
            r'===\s*":material-card-multiple:[^"]*"\s*\n+((?:\s{4,}.*\n|\s*\n)+)',
            text,
        )

        def clean_block(raw_list):
            cleaned = []
            for raw in raw_list:
                code_m = re.search(r"```json\s*\n(.*?)```", raw, re.DOTALL)
                if code_m:
                    lines = [
                        re.sub(r"^\s{4,8}", "", ln)
                        for ln in code_m.group(1).splitlines()
                    ]
                    cleaned.append("\n".join(lines).strip())
            return cleaned

        return {
            "deprecated_examples": clean_block(deprecated_blocks),
            "migrated_examples": clean_block(migrated_blocks),
        }

    def flush():
        if current_h3:
            raw_text = "\n".join(buf)
            examples = extract_code_blocks(raw_text)
            summary_lines = []
            for ln in buf:
                s = ln.strip()
                if not s or s.startswith("===") or s.startswith("!!!") or s.startswith("```") or s.startswith("["):
                    continue
                if not ln.startswith("    "):
                    summary_lines.append(s)
            sections.append({
                "version": current_ver,
                "title": current_h3,
                "summary": " ".join(summary_lines[:3]),
                **examples,
            })

    for line in md_text.splitlines():
        m_ver = re.match(r"^##\s+([0-9.]+)\s*$", line)
        if m_ver:
            flush()
            current_h3 = None
            buf = []
            current_ver = m_ver.group(1)
            continue
        m_h3 = re.match(r"^###\s+(.+?)\s*$", line)
        if m_h3:
            flush()
            current_h3 = re.sub(r":material-[a-z0-9-]+:\s*", "", m_h3.group(1)).strip()
            buf = []
            continue
        if current_h3 is not None:
            buf.append(line)
    flush()
    return sections


def extract_schema_summary(schema_path: Path) -> dict:
    """Summarize top-level keys and key definitions from schema.json."""
    if not schema_path.exists():
        return {}
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    top_props = list(schema.get("properties", {}).keys())
    defs = schema.get("$defs", {})

    def collect_enum_or_types(def_name: str) -> list[str]:
        d = defs.get(def_name, {})
        out = []
        for item in d.get("anyOf", []) + d.get("oneOf", []):
            if "properties" in item and "type" in item["properties"]:
                t_prop = item["properties"]["type"]
                if "const" in t_prop:
                    out.append(t_prop["const"])
                elif "enum" in t_prop:
                    out.extend(t_prop["enum"])
        return sorted(set(out))

    return {
        "schema_id": schema.get("$id", ""),
        "top_level_properties": top_props,
        "definitions_count": len(defs),
        "dns_server_types": collect_enum_or_types("DNSServer"),
        "inbound_types": collect_enum_or_types("Inbound"),
        "outbound_types": collect_enum_or_types("Outbound"),
        "endpoint_types": collect_enum_or_types("Endpoint"),
        "service_types": collect_enum_or_types("Service"),
    }


def extract_pkbox_annotations(pk_root: Path, max_notes: int = 25) -> list[dict]:
    """Extract inline rationale comments from pk-box 1.15 annotated templates."""
    notes = []
    target = pk_root / "1.15.0-alpha.xxx" / "注解" / "1.15.0-alpha.13.json"
    if not target.exists():
        candidates = sorted(pk_root.rglob("*注解*/*.json")) + sorted(pk_root.rglob("*注释*/*.json"))
        target = candidates[-1] if candidates else None
    if not target or not target.exists():
        return notes

    for line in target.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r'^\s*"([^"]+)"\s*:\s*([^/]+?)\s*//\s*(.+)$', line)
        if m:
            key, val_frag, comment = m.group(1), m.group(2).strip().rstrip(","), m.group(3).strip()
            if len(comment) > 12 and not comment.endswith("结束"):
                notes.append({
                    "field": key,
                    "value_sample": val_frag[:40],
                    "rationale": comment,
                    "source_file": str(target.relative_to(ROOT)),
                })
                if len(notes) >= max_notes:
                    break
    return notes


def build_ast_rules_catalog() -> list[dict]:
    """Return the deterministic AST migration rules catalog used by migrate.py."""
    return [
        {
            "id": "MIG-115-TUN-STACK",
            "version_deprecated": "1.15.0",
            "version_removed": "1.17.0",
            "severity": "error",
            "path": "inbounds[type=tun].stack",
            "title": "移除废弃的 TUN stack 参数（启用 1.15 sing-tun 原生新栈）",
            "action": "delete_key",
            "rationale": "自 1.15.0 起 sing-tun 使用自有 TCP/IP 栈，性能、能效与内存占用全面领先，且为 multi_queue 的前提。",
        },
        {
            "id": "MIG-114-DOWNLOAD-DETOUR",
            "version_deprecated": "1.14.0",
            "version_removed": "1.16.0",
            "severity": "error",
            "path": "route.rule_set[*].download_detour / providers[*].download_detour",
            "title": "将 download_detour 迁移为共享 http_clients 与 http_client 引用",
            "action": "lift_to_http_clients",
            "rationale": "1.14.0 废弃了规则集与 Provider 上的 download_detour 以及隐式默认 HTTP 客户端，要求显式声明顶层 http_clients 和 route.default_http_client。",
        },
        {
            "id": "MIG-114-IMPLICIT-HTTP-CLIENT",
            "version_deprecated": "1.14.0",
            "version_removed": "1.16.0",
            "severity": "warning",
            "path": "http_clients / route.default_http_client",
            "title": "为远程规则集/Provider 显式补全 http_clients 与 route.default_http_client",
            "action": "ensure_explicit_http_client",
            "rationale": "1.14.0 起未配置 http_clients 和 default_http_client 的隐式默认 HTTP 客户端已废弃，将在 1.16.0 移除。",
        },
        {
            "id": "MIG-114-TLS-ACME",
            "version_deprecated": "1.14.0",
            "version_removed": "1.16.0",
            "severity": "error",
            "path": "inbounds[*].tls.acme",
            "title": "将内联 tls.acme 迁移为 tls.certificate_provider",
            "action": "rewrite_tls_acme",
            "rationale": "1.14.0 废弃了 TLS 内联 acme 字段，统一改为内联或共享的 ACME certificate_provider。",
        },
        {
            "id": "MIG-114-DNS-INDEPENDENT-CACHE",
            "version_deprecated": "1.14.0",
            "version_removed": "1.16.0",
            "severity": "error",
            "path": "dns.independent_cache",
            "title": "移除废弃的 dns.independent_cache 选项",
            "action": "delete_key",
            "rationale": "1.14.0 废弃了 independent_cache，推荐使用 dns.optimistic 乐观缓存配合 cache_file.store_dns。",
        },
        {
            "id": "MIG-114-CACHE-STORE-RDRC",
            "version_deprecated": "1.14.0",
            "version_removed": "1.16.0",
            "severity": "error",
            "path": "experimental.cache_file.store_rdrc",
            "title": "将 experimental.cache_file.store_rdrc 替换为 store_dns",
            "action": "replace_key_with_store_dns",
            "rationale": "store_rdrc 已在 1.14.0 废弃，store_dns 可将完整 DNS 缓存持久化到 cache.db。",
        },
        {
            "id": "MIG-114-DNS-RULE-LEGACY",
            "version_deprecated": "1.14.0",
            "version_removed": "1.16.0",
            "severity": "error",
            "path": "dns.rules[*].(strategy|rule_set_ip_cidr_accept_empty|ip_cidr|ip_is_private)",
            "title": "清理废弃 DNS 规则项并为地址筛选字段补全 match_response",
            "action": "upgrade_dns_rules_114",
            "rationale": "1.14.0 废弃了 DNS 规则中的 strategy 与 rule_set_ip_cidr_accept_empty；使用 ip_cidr 或 ip_is_private 必须配合 evaluate 与 match_response。",
        },
        {
            "id": "MIG-112-DNS-SERVERS",
            "version_deprecated": "1.12.0",
            "version_removed": "1.14.0",
            "severity": "error",
            "path": "dns.servers[*].address / dns.fakeip",
            "title": "将 1.11 旧版字符串 address DNS 服务器重构为 1.12+ 强类型服务器对象",
            "action": "rewrite_legacy_dns_servers",
            "rationale": "1.12.0 重构了 DNS 服务器格式（已于 1.14.0 移除旧格式），address 拆分为 type + server，address_resolver 改为 domain_resolver，fakeip 内聚为 type=fakeip 服务器。",
        },
        {
            "id": "MIG-112-OUTBOUND-DNS-RULE",
            "version_deprecated": "1.12.0",
            "version_removed": "1.14.0",
            "severity": "error",
            "path": "dns.rules[*].outbound / route.default_domain_resolver",
            "title": "将废弃的 outbound:any DNS 规则迁移为 route.default_domain_resolver",
            "action": "migrate_outbound_dns_rule",
            "rationale": "1.12.0 废弃了通过 dns.rules.outbound 解析节点域名的做法，要求显式指定 route.default_domain_resolver 或 outbound.domain_resolver 以避免自举死锁。",
        },
        {
            "id": "MIG-112-DIAL-DOMAIN-STRATEGY",
            "version_deprecated": "1.12.0",
            "version_removed": "1.14.0",
            "severity": "error",
            "path": "outbounds[*].domain_strategy",
            "title": "将出站拨号字段 domain_strategy 迁移为 domain_resolver",
            "action": "migrate_outbound_domain_strategy",
            "rationale": "拨号字段中的 domain_strategy 已在 1.12.0 废弃，统一使用 domain_resolver.strategy。",
        },
        {
            "id": "MIG-111-SPECIAL-OUTBOUNDS",
            "version_deprecated": "1.11.0",
            "version_removed": "1.13.0",
            "severity": "error",
            "path": "outbounds[type=block|dns] -> route.rules[*].action",
            "title": "移除 block/dns 特殊出站并迁移为路由规则动作 (reject / hijack-dns)",
            "action": "rewrite_special_outbounds",
            "rationale": "1.11.0 废弃并在 1.13.0 移除了 block 与 dns 特殊出站，改为在 route.rules 中使用 action: reject 与 action: hijack-dns。",
        },
        {
            "id": "MIG-111-INBOUND-SNIFF",
            "version_deprecated": "1.11.0",
            "version_removed": "1.13.0",
            "severity": "error",
            "path": "inbounds[*].(sniff|sniff_override_destination|sniff_timeout|domain_strategy)",
            "title": "将入站上的旧版 sniff 与 domain_strategy 提取为 route.rules 顶部动作",
            "action": "hoist_inbound_sniff_and_strategy",
            "rationale": "1.11.0 废弃并在 1.13.0 移除了入站嗅探与域名策略字段，统一收归到 route.rules 的 sniff 与 resolve 规则动作中。",
        },
        {
            "id": "MIG-110-TUN-ADDRESS",
            "version_deprecated": "1.10.0",
            "version_removed": "1.12.0",
            "severity": "error",
            "path": "inbounds[type=tun].inet4_address / inet6_address",
            "title": "合并 TUN 入站中分离的 IPv4/IPv6 地址与路由段字段",
            "action": "merge_tun_inet_addresses",
            "rationale": "1.10.0 将 inet4_address/inet6_address 合并为 address 数组，route_address 与 route_exclude_address 同理。",
        },
        {
            "id": "MIG-108-CLASH-CACHE",
            "version_deprecated": "1.8.0",
            "version_removed": "1.10.0",
            "severity": "error",
            "path": "experimental.clash_api.cache_file",
            "title": "将 experimental.clash_api 下的旧版缓存字段迁移至 experimental.cache_file",
            "action": "migrate_clash_api_cache",
            "rationale": "1.8.0 起缓存文件独立为 experimental.cache_file 模块。",
        },
    ]


def build_red_lines() -> list[dict]:
    return [
        {
            "id": "REDLINE-GITHUBUSERCONTENT-DIRECT",
            "title": "禁止将 githubusercontent.com 放入直连规则集 (direct-domains)",
            "severity": "critical",
            "detail": "实测 release-assets.githubusercontent.com 走家宽直连仅 4.5-6.7 KB/s 且伴随 TLS RST/断流，走代理节点可达 7.2 MB/s（差三个数量级）。必须保留在 proxy-domains 中。",
        },
        {
            "id": "REDLINE-AUTO-REDIRECT-MARK-CONFLICT",
            "title": "TUN auto_redirect 与 route.default_mark / outbound.routing_mark 互斥",
            "severity": "critical",
            "detail": "启用 tun.auto_redirect 时（如 OpenWrt fw4 nftables），不可同时设置 route.default_mark 或出站 routing_mark，否则会导致内核路由标记冲突。",
        },
        {
            "id": "REDLINE-AUTO-REDIRECT-EXCLUDE-CONFLICT",
            "title": "auto_redirect_disable_mark_mode 与 route_exclude_address_set 互斥",
            "severity": "critical",
            "detail": "TUN 入站中 auto_redirect_disable_mark_mode 与 route_exclude_address_set（如 [geoip-cn]）不可同时开启，二者只能选其一。",
        },
        {
            "id": "REDLINE-BOOTSTRAP-RESOLVER",
            "title": "1.14+ 必须显式指定 route.default_domain_resolver 或 DoH domain_resolver",
            "severity": "high",
            "detail": "1.12 起废弃隐式域名解析，DoH 节点或代理出站若未指定指向直连/hosts 的 domain_resolver，会导致启动时 DNS 自举死锁。",
        },
    ]


TOP_LEVEL_FIELD_NOTES: dict[str, tuple[str, str]] = {
    "$schema": (
        "JSON Schema 声明",
        "推荐指向 `https://raw.githubusercontent.com/reF1nd/sing-box/reF1nd-testing-next/docs/schema.json`",
    ),
    "log": ("日志配置", "生产环境建议 `level: info`、`timestamp: true`"),
    "dns": (
        "DNS 服务器与分流规则",
        "**已移除** `independent_cache`；旧版 `address` 字符串格式已彻底废弃，必须用 `type` + `server`；IP 匹配规则需走 `evaluate` + `match_response`",
    ),
    "http_clients": (
        "共享 HTTP 客户端 **(1.14 新增)**",
        "替代废弃的 `download_detour`；用于远程 `rule_set`、`providers` 与 `services.dashboard` 下载",
    ),
    "certificate": (
        "顶层证书对象 **(1.12 新增)**",
        "替代入站 `tls.certificate` 内联写法；与 `certificate_providers` 二选一，动态签发走后者",
    ),
    "certificate_providers": (
        "共享证书提供者 **(1.14 新增)**",
        "替代废弃的 `tls.acme` 内联配置",
    ),
    "endpoints": (
        "端点模块 **(1.11 新增)**",
        "WireGuard、Tailscale、MASQUE、OpenVPN、OpenConnect 等均作为 Endpoint 配置",
    ),
    "inbounds": (
        "入站连接",
        "`tun` 入站的 `stack` 自 1.15 **废弃**、1.17 移除（默认启用自有高性能 `sing-tun` 栈）；`sniff` 与 `domain_strategy` 已移入 `route.rules`",
    ),
    "outbounds": (
        "出站与策略组",
        "**已移除** `type: block` 与 `type: dns` 特殊出站（改用路由规则动作 `reject` / `hijack-dns`）",
    ),
    "providers": (
        "外部节点订阅提供者",
        "远程订阅使用 `http_client` 指定下载客户端，禁用 `download_detour`",
    ),
    "route": (
        "路由规则与规则集",
        "必须显式配置 `default_domain_resolver`（防自举死锁）与 `default_http_client`；`rule_set.tag` 支持数组批量展开 `{tag}`",
    ),
    "experimental": ("实验性功能", "`cache_file.store_rdrc` 已废弃，统一改为 `store_dns: true`"),
    "services": (
        "面板与附加服务",
        "支持 `api`（含内置 Zashboard 下载）、`derp`、`ccm`、`ocm`、`ssm-api`、`usbip` 等",
    ),
    "network_namespaces": (
        "网络命名空间定义",
        "供出站 `netns` 等字段按 tag 引用，需要内核与权限支持",
    ),
    "ntp": (
        "内建 NTP 客户端",
        "为 TLS / Shadowsocks / VMess 等协议提供时间，适用于无法自行同步时间的环境",
    ),
}


def generate_markdown_kb(data: dict) -> str:
    meta = data["metadata"]
    schema = data["schema_summary"]
    lines = [
        "# sing-box 跨版本演进权威知识库 (1.8 → 1.15 reF1nd-testing-next)",
        "",
        f"> 文档基准：`reF1nd-testing-next` (`{meta['ref1nd_docs_commit'][:12]}`) | 锁定内核：`{meta['kernel_commit'][:12]}` | 上游规则：`Repcz/Tool` (`{meta['repcz_tool_commit'][:12]}`) | 模板：`pk-box` (`{meta['pk_box_commit'][:12]}`)",
        "",
        "本知识库由 `build/extract_kb.py` 从上游官方文档 (`deprecated.zh.md`、`migration.zh.md`、`schema.json`) 与实战带注释模板 (`sources/pk-box`) 自动蒸馏生成，版本锚点取自 `.sync-meta.json` 与 `kernel.lock`，供 LLM / AI Skill / 自动化迁移器直接作为上下文使用。产物为确定性输出（不含生成时间戳），只在输入变化时更新。",
        "",
        "## 1. 顶层 Schema 结构（1.15 唯一合法根字段）",
        "",
        "根据最新同步的 `schema.json`（共包含 " + str(schema.get("definitions_count", 0)) + " 个类型定义），合法的顶层字段为以下 " + str(len(schema.get("top_level_properties", []))) + " 项（表格由 `schema.json` 的 `properties` 自动生成，不手工维护）：",
        "",
        "| 顶层字段 | 用途说明 | 1.14 / 1.15 关键演进注意事项 |",
        "| :--- | :--- | :--- |",
    ]
    for field in schema.get("top_level_properties", []):
        usage, notice = TOP_LEVEL_FIELD_NOTES.get(field, ("（见 `schema.json` 对应定义）", "—"))
        lines.append(f"| `{field}` | {usage} | {notice} |")
    lines.extend([
        "",
        "## 2. 确定性 AST 迁移规则矩阵（1.8 → 1.15）",
        "",
        "| 规则 ID | 废弃版本 | 移除版本 | 废弃写法 / 路径 | 1.15 标准写法（自动转换动作） |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ])
    for r in data["ast_rules"]:
        lines.append(
            f"| `{r['id']}` | {r['version_deprecated']} | {r['version_removed']} | `{r['path']}` | {r['title']} |"
        )

    lines.extend([
        "",
        "## 3. 核心语法迁移正反例对照（官方 `migration.zh.md` 提炼）",
        "",
    ])
    for sec in data["migration_sections"]:
        if not sec["deprecated_examples"] and not sec["migrated_examples"]:
            continue
        lines.append(f"### [{sec['version']}] {sec['title']}")
        if sec["summary"]:
            lines.append(f"- **规则要点**：{sec['summary']}")
        if sec["deprecated_examples"]:
            lines.append("- **废弃写法（禁止生成）**：")
            lines.append("```json")
            lines.append(sec["deprecated_examples"][0])
            lines.append("```")
        if sec["migrated_examples"]:
            lines.append("- **1.14/1.15 标准写法**：")
            lines.append("```json")
            lines.append(sec["migrated_examples"][0])
            lines.append("```")
        lines.append("")

    lines.extend([
        "## 4. 工程红线与真实踩坑防呆（Red Lines）",
        "",
    ])
    for rl in data["red_lines"]:
        lines.append(f"- **[{rl['id']}] {rl['title']}**：{rl['detail']}")

    if data.get("pkbox_field_rationales"):
        lines.extend([
            "",
            "## 5. 实战高频字段设计意图（提炼自 `pk-box` 1.15 注释模板）",
            "",
            "| 字段名 | 示例值 | 实战作用与排障解释 |",
            "| :--- | :--- | :--- |",
        ])
        for note in data["pkbox_field_rationales"][:15]:
            val = note["value_sample"].replace("|", "\\|")
            rat = note["rationale"].replace("|", "\\|")
            lines.append(f"| `{note['field']}` | `{val}` | {rat} |")

    lines.extend([
        "",
        "## 6. 1.15 标准契约生成范式（Best-Practice Skeleton）",
        "",
        "```json",
        json.dumps(
            {
                "$schema": "https://raw.githubusercontent.com/reF1nd/sing-box/reF1nd-testing-next/docs/schema.json",
                "log": {"level": "info", "timestamp": True},
                "http_clients": [
                    {"tag": "proxy-hc", "detour": "PROXY", "version": 2}
                ],
                "dns": {
                    "servers": [
                        {
                            "tag": "hosts-bootstrap",
                            "type": "hosts",
                            "predefined": {"cloudflare-dns.com": ["1.1.1.1", "1.0.0.1"]},
                        },
                        {
                            "tag": "proxy-dns",
                            "type": "https",
                            "server": "cloudflare-dns.com",
                            "domain_resolver": "hosts-bootstrap",
                            "detour": "PROXY",
                        },
                        {"tag": "direct-dns", "type": "udp", "server": "223.5.5.5"},
                    ],
                    "rules": [
                        {"rule_set": ["repcz-ads"], "action": "reject"},
                        {"rule_set": ["geosite-cn"], "action": "route", "server": "direct-dns"},
                    ],
                    "final": "proxy-dns",
                    "optimistic": True,
                },
                "inbounds": [
                    {
                        "type": "tun",
                        "tag": "tun-in",
                        "address": ["172.19.0.1/30", "fdfe:dcba:9876::1/126"],
                        "auto_route": True,
                        "auto_redirect": True,
                        "route_exclude_address_set": ["geoip-cn"],
                    }
                ],
                "outbounds": [
                    {"type": "selector", "tag": "PROXY", "outbounds": ["DIRECT"], "default": "DIRECT"},
                    {"type": "direct", "tag": "DIRECT"},
                ],
                "route": {
                    "default_domain_resolver": "direct-dns",
                    "default_http_client": "proxy-hc",
                    "rules": [
                        {"action": "sniff"},
                        {"protocol": "dns", "action": "hijack-dns"},
                        {"rule_set": ["repcz-ads"], "action": "reject"},
                        {"rule_set": ["geosite-cn", "geoip-cn"], "outbound": "DIRECT"},
                    ],
                    "rule_set": [
                        {
                            "type": "remote",
                            "tag": ["geosite-cn", "geoip-cn"],
                            "format": "binary",
                            "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/{tag}.srs",
                            "http_client": "proxy-hc",
                            "update_interval": "24h",
                        }
                    ],
                    "final": "PROXY",
                },
                "experimental": {
                    "cache_file": {"enabled": True, "path": "/etc/sing-box/cache.db", "store_dns": True}
                },
            },
            indent=2,
            ensure_ascii=False,
        ),
        "```",
        "",
    ])
    return "\n".join(lines)


def main(out_dir: Path | None = None) -> int:
    dep_path = ROOT / "normalized" / "ref1nd-docs" / "deprecated.zh.md"
    mig_path = ROOT / "normalized" / "ref1nd-docs" / "migration.zh.md"
    schema_path = ROOT / "normalized" / "ref1nd-docs" / "schema.json"
    sync_meta_path = ROOT / ".sync-meta.json"
    kernel_lock_path = ROOT / "kernel.lock"

    sync_meta = json.loads(sync_meta_path.read_text(encoding="utf-8")) if sync_meta_path.exists() else {}
    kernel_lock = yaml.safe_load(kernel_lock_path.read_text(encoding="utf-8")) if kernel_lock_path.exists() else {}

    deprecated_items = parse_deprecated_md(dep_path.read_text(encoding="utf-8")) if dep_path.exists() else []
    migration_sections = parse_migration_md(mig_path.read_text(encoding="utf-8")) if mig_path.exists() else []
    schema_summary = extract_schema_summary(schema_path)
    pk_notes = extract_pkbox_annotations(ROOT / "sources" / "pk-box")

    data = {
        "metadata": {
            "ref1nd_docs_commit": sync_meta.get("ref1nd-docs", {}).get("commit", "unknown"),
            "repcz_tool_commit": sync_meta.get("repcz-tool", {}).get("commit", "unknown"),
            "pk_box_commit": sync_meta.get("pk-box", {}).get("commit", "unknown"),
            "kernel_commit": (kernel_lock.get("current") or {}).get("commit", "unknown"),
        },
        "schema_summary": schema_summary,
        "ast_rules": build_ast_rules_catalog(),
        "deprecated_items": deprecated_items,
        "migration_sections": migration_sections,
        "red_lines": build_red_lines(),
        "pkbox_field_rationales": pk_notes,
    }

    dist_dir = out_dir if out_dir is not None else ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    rules_out = dist_dir / "migration-rules.json"
    rules_out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    kb_out = dist_dir / "sing-box-llm-kb.md"
    kb_out.write_text(generate_markdown_kb(data) + "\n", encoding="utf-8")

    def shown(p: Path) -> str:
        try:
            return str(p.relative_to(ROOT))
        except ValueError:
            return str(p)

    print(
        f"Extracted {len(data['ast_rules'])} AST rules, "
        f"{len(deprecated_items)} deprecated items, "
        f"{len(migration_sections)} migration sections -> {shown(rules_out)} & {shown(kb_out)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

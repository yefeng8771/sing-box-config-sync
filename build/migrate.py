#!/usr/bin/env python3
"""Deterministic AST Migration Compiler & Linter for sing-box (1.8 -> 1.15 reF1nd-testing-next).

Features:
  - Tolerant JSON/JSONC parser (strips // and /* */ comments and trailing commas)
  - 14 deterministic AST migration rules (1.8 -> 1.15), catalogued in dist/migration-rules.json
  - Top-level key validation against normalized/ref1nd-docs/schema.json (not a full schema validator)
  - Logical conflict & red-line checks (auto_redirect + default_mark, githubusercontent.com in direct-domains, etc.)
  - Batch upstream template auditor (--batch-audit)

Usage:
  python build/migrate.py input.jsonc -o output.json --report report.json
  python build/migrate.py --batch-audit --out dist/template-migration-audit.json
"""
import argparse
import copy
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import normalize  # reuse strip_comments and strip_trailing_commas


def parse_jsonc(text: str) -> dict:
    cleaned = normalize.strip_trailing_commas(normalize.strip_comments(text))
    return json.loads(cleaned)


def parse_legacy_dns_address(addr: str) -> dict:
    """Convert 1.11 legacy dns.servers[*].address string into 1.12+ typed fields."""
    addr = addr.strip()
    if addr == "local":
        return {"type": "local"}
    if addr == "fakeip":
        return {"type": "fakeip"}
    if addr.startswith("rcode://"):
        return {"type": "rcode_legacy", "rcode": addr.removeprefix("rcode://").upper()}
    if addr.startswith("dhcp://"):
        iface = addr.removeprefix("dhcp://")
        out = {"type": "dhcp"}
        if iface and iface != "auto":
            out["interface"] = iface
        return out
    if "://" in addr:
        parsed = urlparse(addr)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or parsed.netloc
        port = parsed.port
        type_map = {
            "https": "https",
            "h3": "h3",
            "tls": "tls",
            "quic": "quic",
            "tcp": "tcp",
            "udp": "udp",
        }
        stype = type_map.get(scheme, scheme)
        out = {"type": stype, "server": host}
        if port:
            out["server_port"] = port
        if parsed.path and parsed.path not in ("", "/", "/dns-query"):
            out["path"] = parsed.path
        return out
    # Plain IP or host[:port] -> UDP
    if ":" in addr and addr.count(":") == 1:
        host, port_s = addr.rsplit(":", 1)
        if port_s.isdigit():
            return {"type": "udp", "server": host, "server_port": int(port_s)}
    return {"type": "udp", "server": addr}


def dns_server_tags(cfg: dict) -> list[str]:
    """Collect declared dns.servers tags (legacy or 1.12+ shaped entries)."""
    tags = []
    dns = cfg.get("dns")
    if isinstance(dns, dict) and isinstance(dns.get("servers"), list):
        for srv in dns["servers"]:
            if isinstance(srv, dict) and isinstance(srv.get("tag"), str) and srv["tag"]:
                tags.append(srv["tag"])
    return tags


def pick_dns_server_tag(cfg: dict) -> str | None:
    """Pick a tag that actually exists in dns.servers for domain_resolver / evaluate.

    Prefers dns.final, then a server tagged "remote" (common historical default),
    then the first declared server. Returns None when no server is tagged, so
    callers can skip the migration instead of emitting a dangling tag reference.
    """
    tags = dns_server_tags(cfg)
    if not tags:
        return None
    dns = cfg.get("dns")
    final = dns.get("final") if isinstance(dns, dict) else None
    if isinstance(final, str) and final in tags:
        return final
    if "remote" in tags:
        return "remote"
    return tags[0]


def migrate_config(cfg_in: dict) -> tuple[dict, list[dict], list[dict]]:
    """Run all deterministic AST migration passes on a parsed config dict.

    Returns: (migrated_cfg, applied_migrations, warnings)
    """
    cfg = copy.deepcopy(cfg_in)
    applied = []
    warnings = []

    def record(rule_id: str, path: str, before_desc: str, after_desc: str):
        applied.append({
            "rule_id": rule_id,
            "path": path,
            "before": before_desc,
            "after": after_desc,
        })

    # 1. MIG-108-CLASH-CACHE: experimental.clash_api.cache_file -> experimental.cache_file
    exp = cfg.get("experimental")
    if isinstance(exp, dict) and isinstance(exp.get("clash_api"), dict):
        capi = exp["clash_api"]
        legacy_cache_keys = ("cache_file", "cache_id", "cahce_id", "store_mode", "store_selected", "store_fakeip")
        found_keys = [k for k in legacy_cache_keys if k in capi]
        if found_keys:
            cf = exp.setdefault("cache_file", {"enabled": True})
            if isinstance(capi.get("cache_file"), str):
                cf.setdefault("path", capi.pop("cache_file"))
            elif "cache_file" in capi:
                capi.pop("cache_file")
            for cid_key in ("cache_id", "cahce_id"):
                if cid_key in capi:
                    cf.setdefault("cache_id", capi.pop(cid_key))
            if capi.pop("store_fakeip", None) is True:
                cf["store_fakeip"] = True
            capi.pop("store_mode", None)
            capi.pop("store_selected", None)
            cf.setdefault("enabled", True)
            record(
                "MIG-108-CLASH-CACHE",
                "experimental.clash_api",
                f"内联缓存字段 {found_keys}",
                "迁移至独立模块 experimental.cache_file",
            )

    # 2. MIG-110-TUN-ADDRESS & MIG-115-TUN-STACK & MIG-111-INBOUND-SNIFF & MIG-114-TLS-ACME
    inbounds = cfg.get("inbounds")
    hoisted_route_rules = []
    if isinstance(inbounds, list):
        for idx, ib in enumerate(inbounds):
            if not isinstance(ib, dict):
                continue
            ib_path = f"inbounds[{idx}]"
            ib_tag = ib.get("tag") or f"in-{idx}"

            # MIG-115-TUN-STACK
            if ib.get("type") == "tun" and "stack" in ib:
                old_stack = ib.pop("stack")
                record(
                    "MIG-115-TUN-STACK",
                    f"{ib_path}.stack",
                    f'"stack": {json.dumps(old_stack)}',
                    "移除 stack 参数（默认启用 1.15 sing-tun 原生新栈）",
                )

            # MIG-110-TUN-ADDRESS
            if ib.get("type") == "tun":
                for v4_k, v6_k, merged_k in (
                    ("inet4_address", "inet6_address", "address"),
                    ("inet4_route_address", "inet6_route_address", "route_address"),
                    ("inet4_route_exclude_address", "inet6_route_exclude_address", "route_exclude_address"),
                ):
                    if v4_k in ib or v6_k in ib:
                        merged = list(ib.get(merged_k, [])) if isinstance(ib.get(merged_k), list) else (
                            [ib[merged_k]] if merged_k in ib else []
                        )
                        for k in (v4_k, v6_k):
                            if k in ib:
                                val = ib.pop(k)
                                if isinstance(val, list):
                                    merged.extend(val)
                                elif isinstance(val, str):
                                    merged.append(val)
                        ib[merged_k] = merged
                        record(
                            "MIG-110-TUN-ADDRESS",
                            f"{ib_path}.{merged_k}",
                            f"{v4_k}/{v6_k}",
                            f'合并为 "{merged_k}": {json.dumps(merged, ensure_ascii=False)}',
                        )

            # MIG-111-INBOUND-SNIFF
            sniff_enabled = ib.pop("sniff", None)
            sniff_override = ib.pop("sniff_override_destination", None)
            sniff_timeout = ib.pop("sniff_timeout", None)
            ib_dom_strategy = ib.pop("domain_strategy", None)
            if any(x is not None for x in (sniff_enabled, sniff_override, sniff_timeout, ib_dom_strategy)):
                ib.setdefault("tag", ib_tag)
                if ib_dom_strategy:
                    hoisted_route_rules.append({
                        "inbound": ib["tag"],
                        "action": "resolve",
                        "strategy": ib_dom_strategy,
                    })
                if sniff_enabled:
                    s_rule = {"inbound": ib["tag"], "action": "sniff"}
                    if sniff_timeout:
                        s_rule["timeout"] = sniff_timeout
                    hoisted_route_rules.append(s_rule)
                    if sniff_override:
                        # 1.15 中目标地址覆盖是独立动作，不能作为 sniff 动作的字段。
                        hoisted_route_rules.append({
                            "inbound": ib["tag"],
                            "action": "sniff-override-destination",
                        })
                record(
                    "MIG-111-INBOUND-SNIFF",
                    ib_path,
                    "入站内联 sniff / domain_strategy",
                    "提取为 route.rules 顶部的 sniff / sniff-override-destination / resolve 动作规则",
                )

            # MIG-114-TLS-ACME
            tls = ib.get("tls")
            if isinstance(tls, dict) and isinstance(tls.get("acme"), dict):
                acme_obj = tls.pop("acme")
                tls["certificate_provider"] = {"type": "acme", **acme_obj}
                record(
                    "MIG-114-TLS-ACME",
                    f"{ib_path}.tls.acme",
                    "内联 tls.acme 对象",
                    '迁移为 tls.certificate_provider: {"type": "acme", ...}',
                )

    # 3. MIG-111-SPECIAL-OUTBOUNDS & MIG-112-DIAL-DOMAIN-STRATEGY
    outbounds = cfg.get("outbounds")
    block_tags = set()
    dns_tags = set()
    if isinstance(outbounds, list):
        kept_outbounds = []
        for idx, ob in enumerate(outbounds):
            if not isinstance(ob, dict):
                kept_outbounds.append(ob)
                continue
            ob_type = ob.get("type")
            ob_tag = ob.get("tag", "")
            if ob_type == "block":
                if ob_tag:
                    block_tags.add(ob_tag)
                record(
                    "MIG-111-SPECIAL-OUTBOUNDS",
                    f"outbounds[{idx}]",
                    f'{{"type": "block", "tag": "{ob_tag}"}}',
                    '移除特殊出站（路由规则改为 "action": "reject"）',
                )
                continue
            if ob_type == "dns":
                if ob_tag:
                    dns_tags.add(ob_tag)
                record(
                    "MIG-111-SPECIAL-OUTBOUNDS",
                    f"outbounds[{idx}]",
                    f'{{"type": "dns", "tag": "{ob_tag}"}}',
                    '移除特殊出站（路由规则改为 "action": "hijack-dns"）',
                )
                continue

            # Clean references to removed block_tags inside selector/urltest outbounds if needed later
            if "domain_strategy" in ob:
                ds = ob.pop("domain_strategy")
                default_res = (cfg.get("route") or {}).get("default_domain_resolver")
                if isinstance(default_res, dict):
                    default_res = default_res.get("server")
                res_server = default_res if isinstance(default_res, str) and default_res else None
                if res_server is None:
                    # 只允许引用真实存在的 dns.servers 标签，否则产出悬空引用
                    res_server = pick_dns_server_tag(cfg)
                if res_server is None:
                    warnings.append({
                        "code": "MIG-112-NO-DNS-SERVER",
                        "severity": "warning",
                        "path": f"outbounds[{idx}].domain_strategy",
                        "message": (
                            "配置中没有带 tag 的 DNS 服务器，domain_strategy 无法迁移为 "
                            "domain_resolver（1.15 要求 server 必填），该字段已被移除"
                        ),
                    })
                elif "domain_resolver" not in ob:
                    ob["domain_resolver"] = {"server": res_server, "strategy": ds}
                    record(
                        "MIG-112-DIAL-DOMAIN-STRATEGY",
                        f"outbounds[{idx}].domain_strategy",
                        f'"domain_strategy": "{ds}"',
                        f'"domain_resolver": {{"server": "{res_server}", "strategy": "{ds}"}}',
                    )
            kept_outbounds.append(ob)
        cfg["outbounds"] = kept_outbounds

    # Remove references to deleted block/dns tags inside selector/urltest groups
    if block_tags or dns_tags:
        removed_special = block_tags | dns_tags
        for ob in cfg.get("outbounds", []):
            if isinstance(ob, dict) and isinstance(ob.get("outbounds"), list):
                ob["outbounds"] = [t for t in ob["outbounds"] if t not in removed_special]
                if ob.get("default") in removed_special and ob["outbounds"]:
                    ob["default"] = ob["outbounds"][0]

    # 4. DNS migrations: MIG-112-DNS-SERVERS, MIG-112-OUTBOUND-DNS-RULE, MIG-114-DNS-INDEPENDENT-CACHE, MIG-114-DNS-RULE-LEGACY
    dns = cfg.get("dns")
    hoisted_dns_rules = []
    outbound_resolver_server = None
    if isinstance(dns, dict):
        if "independent_cache" in dns:
            old_ic = dns.pop("independent_cache")
            record(
                "MIG-114-DNS-INDEPENDENT-CACHE",
                "dns.independent_cache",
                f'"independent_cache": {json.dumps(old_ic)}',
                "移除废弃字段（改用 dns.optimistic + cache_file.store_dns）",
            )

        legacy_fakeip_cfg = dns.pop("fakeip", None)
        servers = dns.get("servers")
        if isinstance(servers, list):
            new_servers = []
            for idx, srv in enumerate(servers):
                if not isinstance(srv, dict):
                    new_servers.append(srv)
                    continue
                if "address" in srv and "type" not in srv:
                    raw_addr = srv.pop("address")
                    parsed_srv = parse_legacy_dns_address(raw_addr)
                    if parsed_srv.get("type") == "rcode_legacy":
                        record(
                            "MIG-112-DNS-SERVERS",
                            f"dns.servers[{idx}]",
                            f'"address": "{raw_addr}"',
                            "移除 rcode 伪服务器（1.12+ 改用 dns.rules action=predefined/reject）",
                        )
                        continue
                    new_s = {**parsed_srv, **srv}
                    if "address_resolver" in new_s:
                        new_s["domain_resolver"] = new_s.pop("address_resolver")
                    srv_strategy = new_s.pop("strategy", None)
                    srv_subnet = new_s.pop("client_subnet", None)
                    if (srv_strategy or srv_subnet) and new_s.get("tag"):
                        # If a rule targets this server tag, strategy/client_subnet belongs on the rule or top-level dns
                        if srv_strategy and "strategy" not in dns:
                            dns["strategy"] = srv_strategy
                    if new_s.get("type") == "fakeip" and isinstance(legacy_fakeip_cfg, dict):
                        for fk in ("inet4_range", "inet6_range"):
                            if fk in legacy_fakeip_cfg and fk not in new_s:
                                new_s[fk] = legacy_fakeip_cfg[fk]
                    new_servers.append(new_s)
                    record(
                        "MIG-112-DNS-SERVERS",
                        f"dns.servers[{idx}]",
                        f'"address": "{raw_addr}"',
                        f'转换为 1.12+ 强类型服务器 {{"type": "{new_s.get("type")}", ...}}',
                    )
                else:
                    if "address_resolver" in srv:
                        srv["domain_resolver"] = srv.pop("address_resolver")
                        record(
                            "MIG-112-DNS-SERVERS",
                            f"dns.servers[{idx}].address_resolver",
                            "address_resolver",
                            "domain_resolver",
                        )
                    new_servers.append(srv)
            if isinstance(legacy_fakeip_cfg, dict) and legacy_fakeip_cfg.get("enabled"):
                if not any(isinstance(s, dict) and s.get("type") == "fakeip" for s in new_servers):
                    f_srv = {"type": "fakeip", "tag": "fakeip"}
                    for fk in ("inet4_range", "inet6_range"):
                        if fk in legacy_fakeip_cfg:
                            f_srv[fk] = legacy_fakeip_cfg[fk]
                    new_servers.append(f_srv)
                    record(
                        "MIG-112-DNS-SERVERS",
                        "dns.fakeip",
                        "顶层 dns.fakeip 对象",
                        '迁移为 dns.servers 中的 {"type": "fakeip"} 服务器',
                    )
            dns["servers"] = new_servers

        dns_rules = dns.get("rules")
        if isinstance(dns_rules, list):
            new_dns_rules = []
            needs_evaluate_server = None
            for idx, r in enumerate(dns_rules):
                if not isinstance(r, dict):
                    new_dns_rules.append(r)
                    continue
                r_path = f"dns.rules[{idx}]"

                # MIG-112-OUTBOUND-DNS-RULE
                if "outbound" in r:
                    ob_val = r.pop("outbound")
                    target_srv = r.get("server")
                    if target_srv and not outbound_resolver_server:
                        outbound_resolver_server = target_srv
                    record(
                        "MIG-112-OUTBOUND-DNS-RULE",
                        f"{r_path}.outbound",
                        f'"outbound": {json.dumps(ob_val)}, "server": "{target_srv}"',
                        f'移除 outbound DNS 规则并迁移至 route.default_domain_resolver = "{target_srv}"',
                    )
                    if not any(k in r for k in ("domain", "domain_suffix", "domain_keyword", "domain_regex", "rule_set", "clash_mode", "query_type")):
                        continue

                # MIG-114-DNS-RULE-LEGACY
                if "rule_set_ip_cidr_accept_empty" in r:
                    r.pop("rule_set_ip_cidr_accept_empty")
                    record(
                        "MIG-114-DNS-RULE-LEGACY",
                        f"{r_path}.rule_set_ip_cidr_accept_empty",
                        '"rule_set_ip_cidr_accept_empty": true',
                        "移除 1.14 废弃字段",
                    )
                if "strategy" in r:
                    old_strat = r.pop("strategy")
                    record(
                        "MIG-114-DNS-RULE-LEGACY",
                        f"{r_path}.strategy",
                        f'"strategy": "{old_strat}"',
                        "移除 1.14 废弃的 DNS 规则内联 strategy 选项",
                    )
                if ("ip_cidr" in r or "ip_is_private" in r) and "match_response" not in r:
                    if needs_evaluate_server is None:
                        # evaluate 的 server 必填，只能指向真实存在的 dns.servers 标签
                        needs_evaluate_server = pick_dns_server_tag(cfg)
                    if needs_evaluate_server is None:
                        warnings.append({
                            "code": "MIG-114-NO-EVALUATE-SERVER",
                            "severity": "warning",
                            "path": r_path,
                            "message": (
                                "ip_cidr/ip_is_private 匹配响应需要前置 evaluate 动作，"
                                "但配置中没有带 tag 的 DNS 服务器，已跳过 match_response 迁移"
                            ),
                        })
                    else:
                        r["match_response"] = True
                        record(
                            "MIG-114-DNS-RULE-LEGACY",
                            r_path,
                            "ip_cidr/ip_is_private 未设置 match_response",
                            f'补全 "match_response": true 并前置 evaluate 动作规则（server: "{needs_evaluate_server}"）',
                        )
                if "server" in r and "action" not in r:
                    r["action"] = "route"
                new_dns_rules.append(r)

            if needs_evaluate_server and not any(
                isinstance(x, dict) and x.get("action") == "evaluate" for x in new_dns_rules
            ):
                new_dns_rules.insert(0, {"action": "evaluate", "server": needs_evaluate_server})
            dns["rules"] = new_dns_rules

    # 5. MIG-114-CACHE-STORE-RDRC
    if isinstance(exp, dict) and isinstance(exp.get("cache_file"), dict):
        cf = exp["cache_file"]
        if "store_rdrc" in cf:
            old_rdrc = cf.pop("store_rdrc")
            if old_rdrc:
                cf["store_dns"] = True
            record(
                "MIG-114-CACHE-STORE-RDRC",
                "experimental.cache_file.store_rdrc",
                f'"store_rdrc": {json.dumps(old_rdrc)}',
                '"store_dns": true',
            )

    # 6. Route rules & Rule-set / Provider HTTP client migrations
    route = cfg.setdefault("route", {}) if isinstance(cfg.get("route"), dict) or hoisted_route_rules or outbound_resolver_server else None
    if isinstance(route, dict):
        if outbound_resolver_server and "default_domain_resolver" not in route:
            route["default_domain_resolver"] = outbound_resolver_server

        r_rules = route.get("rules")
        if isinstance(r_rules, list):
            updated_rules = []
            for idx, rr in enumerate(r_rules):
                if not isinstance(rr, dict):
                    updated_rules.append(rr)
                    continue
                ob_target = rr.get("outbound")
                if ob_target in block_tags or ob_target == "block":
                    rr.pop("outbound", None)
                    rr["action"] = "reject"
                    record(
                        "MIG-111-SPECIAL-OUTBOUNDS",
                        f"route.rules[{idx}]",
                        f'"outbound": "{ob_target}"',
                        '"action": "reject"',
                    )
                elif ob_target in dns_tags or (rr.get("protocol") == "dns" and ob_target == "dns"):
                    rr.pop("outbound", None)
                    rr["action"] = "hijack-dns"
                    record(
                        "MIG-111-SPECIAL-OUTBOUNDS",
                        f"route.rules[{idx}]",
                        f'"outbound": "{ob_target}"',
                        '"action": "hijack-dns"',
                    )
                updated_rules.append(rr)
            if hoisted_route_rules:
                updated_rules = hoisted_route_rules + updated_rules
            route["rules"] = updated_rules
        elif hoisted_route_rules:
            route["rules"] = hoisted_route_rules

    # 7. MIG-114-DOWNLOAD-DETOUR & MIG-114-IMPLICIT-HTTP-CLIENT
    http_clients = cfg.get("http_clients")
    if not isinstance(http_clients, list):
        http_clients = []

    detour_to_hc_tag = {}
    for hc in http_clients:
        if isinstance(hc, dict) and hc.get("tag") and hc.get("detour"):
            detour_to_hc_tag[hc["detour"]] = hc["tag"]

    def get_or_create_hc_for_detour(detour_tag: str) -> str:
        if detour_tag in detour_to_hc_tag:
            return detour_to_hc_tag[detour_tag]
        slug = detour_tag.lower().replace(" ", "-")
        tag_name = f"hc-{slug}" if slug.isascii() else f"hc-{len(http_clients) + 1}"
        existing_tags = {h.get("tag") for h in http_clients if isinstance(h, dict)}
        while tag_name in existing_tags:
            tag_name += "-1"
        http_clients.append({"tag": tag_name, "detour": detour_tag, "version": 2})
        detour_to_hc_tag[detour_tag] = tag_name
        return tag_name

    if isinstance(route, dict) and isinstance(route.get("rule_set"), list):
        has_remote_rs = False
        for idx, rs in enumerate(route["rule_set"]):
            if not isinstance(rs, dict):
                continue
            if rs.get("type") == "remote":
                has_remote_rs = True
            if "download_detour" in rs:
                detour_val = rs.pop("download_detour")
                hc_tag = get_or_create_hc_for_detour(detour_val)
                rs["http_client"] = hc_tag
                record(
                    "MIG-114-DOWNLOAD-DETOUR",
                    f"route.rule_set[{idx}].download_detour",
                    f'"download_detour": "{detour_val}"',
                    f'替换为 "http_client": "{hc_tag}" 并注册顶层 http_clients',
                )
        if has_remote_rs and not http_clients and "default_http_client" not in route:
            # Determine a sensible default outbound for http_clients
            default_detour = route.get("final")
            hc_entry = {"tag": "default-hc", "version": 2}
            if isinstance(default_detour, str) and default_detour:
                hc_entry["detour"] = default_detour
            http_clients.append(hc_entry)
            route["default_http_client"] = "default-hc"
            record(
                "MIG-114-IMPLICIT-HTTP-CLIENT",
                "route.default_http_client",
                "未定义 http_clients（1.14 废弃隐式 HTTP 客户端）",
                '显式声明顶层 http_clients 与 "default_http_client": "default-hc"',
            )

    if isinstance(cfg.get("providers"), list):
        for idx, pv in enumerate(cfg["providers"]):
            if isinstance(pv, dict) and "download_detour" in pv:
                detour_val = pv.pop("download_detour")
                hc_tag = get_or_create_hc_for_detour(detour_val)
                pv["http_client"] = hc_tag
                record(
                    "MIG-114-DOWNLOAD-DETOUR",
                    f"providers[{idx}].download_detour",
                    f'"download_detour": "{detour_val}"',
                    f'替换为 "http_client": "{hc_tag}" 并注册顶层 http_clients',
                )

    if http_clients:
        cfg["http_clients"] = http_clients
        if isinstance(route, dict) and "default_http_client" not in route:
            first_tag = http_clients[0].get("tag")
            if first_tag:
                route["default_http_client"] = first_tag

    # 8. Logical Conflict & Red-line Checks
    warnings.extend(validate_config(cfg))
    return cfg, applied, warnings


def validate_config(cfg: dict) -> list[dict]:
    """Check schema compliance, logical conflicts, and repository red-lines."""
    issues = []

    schema_path = ROOT / "normalized" / "ref1nd-docs" / "schema.json"
    if schema_path.exists():
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        allowed_top = set(schema.get("properties", {}).keys())
        for k in cfg.keys():
            if k not in allowed_top:
                issues.append({
                    "code": "SCHEMA-UNKNOWN-TOP-KEY",
                    "severity": "error",
                    "path": k,
                    "message": f"顶层字段 '{k}' 不在 1.15 schema.json 允许列表 {sorted(allowed_top)} 中",
                })

    # Check logical conflicts from build.py spec §6.4
    route = cfg.get("route") if isinstance(cfg.get("route"), dict) else {}
    default_mark = route.get("default_mark")
    inbounds = cfg.get("inbounds") if isinstance(cfg.get("inbounds"), list) else []
    tun = next((i for i in inbounds if isinstance(i, dict) and i.get("type") == "tun"), None)

    if tun and tun.get("auto_redirect"):
        if default_mark:
            issues.append({
                "code": "CONFLICT-AUTO-REDIRECT-MARK",
                "severity": "error",
                "path": "route.default_mark",
                "message": "冲突：tun.auto_redirect 与 route.default_mark 互斥，不可同时启用",
            })
        for idx, o in enumerate(cfg.get("outbounds", [])):
            if isinstance(o, dict) and o.get("routing_mark"):
                issues.append({
                    "code": "CONFLICT-AUTO-REDIRECT-ROUTING-MARK",
                    "severity": "error",
                    "path": f"outbounds[{idx}].routing_mark",
                    "message": f"冲突：tun.auto_redirect 与出站 {o.get('tag')} 的 routing_mark 互斥",
                })
        if tun.get("auto_redirect_disable_mark_mode") and tun.get("route_exclude_address_set"):
            issues.append({
                "code": "CONFLICT-MARK-MODE-EXCLUDE-SET",
                "severity": "error",
                "path": "inbounds[type=tun]",
                "message": "冲突：auto_redirect_disable_mark_mode 与 route_exclude_address_set 二选一，不可同时启用",
            })

    # Check bootstrap domain resolver warning
    if cfg.get("dns") and not route.get("default_domain_resolver"):
        issues.append({
            "code": "WARN-MISSING-DEFAULT-DOMAIN-RESOLVER",
            "severity": "warning",
            "path": "route.default_domain_resolver",
            "message": "建议显式指定 route.default_domain_resolver（指向直连/本地 DNS），避免 1.14+ 节点域名或 DoH 自举死锁",
        })

    return issues


def check_custom_direct_domains_redline() -> list[dict]:
    """Ensure githubusercontent.com never creeps back into custom/direct-domains.json."""
    direct_path = ROOT / "custom" / "direct-domains.json"
    if not direct_path.exists():
        return []
    text = direct_path.read_text(encoding="utf-8")
    if "githubusercontent.com" in text:
        return [{
            "code": "REDLINE-GITHUBUSERCONTENT-DIRECT",
            "severity": "critical",
            "path": "custom/direct-domains.json",
            "message": "严重红线违规：custom/direct-domains.json 中包含 githubusercontent.com！实测直连仅 4.5 KB/s 且断流，必须移至 proxy-domains.json",
        }]
    return []


def run_batch_audit(out_path: Path) -> int:
    """Audit all upstream configs in normalized/ and sources/pk-box/."""
    targets = []
    for sub in ("normalized/repcz-tool", "normalized/gist-chizi", "sources/pk-box"):
        base = ROOT / sub
        if base.exists():
            for p in sorted(base.rglob("*")):
                if p.is_file() and p.suffix.lower() in (".json", ".jsonc"):
                    if "Rules/" in str(p):
                        continue
                    targets.append(p)

    results = []
    stats = {"total": 0, "clean_115": 0, "auto_migrated": 0, "unparseable_template": 0}

    for p in targets:
        rel = str(p.relative_to(ROOT))
        stats["total"] += 1
        try:
            cfg = parse_jsonc(p.read_text(encoding="utf-8"))
        except Exception as exc:
            stats["unparseable_template"] += 1
            results.append({
                "file": rel,
                "status": "unparseable-placeholder-template",
                "parse_error": str(exc),
                "migrations_needed": 0,
                "applied_rules": [],
                "validation_issues": [],
            })
            continue

        if not isinstance(cfg, dict):
            continue
        _, applied, issues = migrate_config(cfg)
        if not applied and not [i for i in issues if i["severity"] == "error"]:
            stats["clean_115"] += 1
            status = "1.15-compliant"
        else:
            stats["auto_migrated"] += 1
            status = "auto-migratable"

        results.append({
            "file": rel,
            "status": status,
            "migrations_needed": len(applied),
            "applied_rules": applied,
            "validation_issues": issues,
        })

    redline_issues = check_custom_direct_domains_redline()
    report = {
        "summary": stats,
        "custom_redline_check": "PASS" if not redline_issues else "FAIL",
        "custom_redline_issues": redline_issues,
        "files": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"Batch audit complete: {stats['total']} templates inspected "
        f"({stats['clean_115']} already 1.15-compliant, "
        f"{stats['auto_migrated']} auto-migratable, "
        f"{stats['unparseable_template']} placeholder templates) -> {out_path.resolve().relative_to(ROOT) if out_path.resolve().is_relative_to(ROOT) else out_path}"
    )
    return 0 if not redline_issues else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="sing-box 1.8 -> 1.15 AST Migration Compiler")
    ap.add_argument("input", nargs="?", help="Input JSON/JSONC config path")
    ap.add_argument("-o", "--out", help="Output migrated JSON path (or audit report path)")
    ap.add_argument("--report", help="Optional path to write migration diff report JSON")
    ap.add_argument("--batch-audit", action="store_true", help="Audit all upstream templates in repo")
    args = ap.parse_args()

    if args.batch_audit:
        out_p = Path(args.out) if args.out else ROOT / "dist" / "template-migration-audit.json"
        return run_batch_audit(out_p)

    if not args.input:
        ap.error("Provide an input config file or use --batch-audit")

    in_path = Path(args.input)
    raw_text = in_path.read_text(encoding="utf-8")
    cfg = parse_jsonc(raw_text)
    migrated, applied, issues = migrate_config(cfg)

    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(migrated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        print(json.dumps(migrated, indent=2, ensure_ascii=False))

    if args.report:
        rep_p = Path(args.report)
        rep_p.parent.mkdir(parents=True, exist_ok=True)
        rep_p.write_text(
            json.dumps({"applied_migrations": applied, "validation_issues": issues}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    print(
        f"Migrated {in_path.name}: {len(applied)} AST rule(s) applied, {len(issues)} validation notice(s).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

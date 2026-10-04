#!/usr/bin/env python3
"""Build sing-box configs from contracts.

Reads intents.yaml + devices/<device>.yaml + providers.yaml,
generates dist/<device>.json, and lints deprecated / conflicting fields.

Usage:
    python build/build.py --device router [--providers providers.yaml]
                         [--out dist/router.json] [--check-only]

Providers: real providers.yaml (gitignored, injected from Secrets) or,
when missing, an inline fake local provider so CI can still run
`sing-box check`.

Exit codes: 0 ok, 2 lint failure, 1 other error.
"""
import argparse
import copy
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# ---- spec §6: deprecated fields (build fails on any hit) ----
# (version deprecated, version removed)
DEPRECATED_KEYS = {
    "download_detour",
    "independent_cache",
    "store_rdrc",
    "rule_set_ip_cidr_accept_empty",
    "strategy",  # legacy DNS rule action option
}
# tls.acme is nested; handled structurally below.


def lint_deprecated(obj, trail=""):
    """Recursively reject deprecated fields. Raises ValueError on hit."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{trail}.{k}" if trail else k
            if k in DEPRECATED_KEYS:
                raise ValueError(f"deprecated field: {p}")
            if k == "acme" and trail.endswith("tls"):
                raise ValueError(f"deprecated inline tls.acme: {p}")
            if k == "stack" and "tun" in trail:
                raise ValueError(f"deprecated TUN stack option: {p}")
            lint_deprecated(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            lint_deprecated(v, f"{trail}[{i}]")


def lint_dns_rules(dns):
    """DNS rules using ip_cidr/ip_is_private without match_response are deprecated."""
    for i, r in enumerate(dns.get("rules", [])):
        if not isinstance(r, dict):
            continue
        if ("ip_cidr" in r or "ip_is_private" in r) and "match_response" not in r:
            raise ValueError(
                f"deprecated DNS rule #{i}: ip_cidr/ip_is_private without match_response"
            )


def lint_conflicts(cfg):
    """spec §6.4: conflicting field combinations."""
    route = cfg.get("route", {})
    default_mark = route.get("default_mark")
    tun = next(
        (i for i in cfg.get("inbounds", []) if isinstance(i, dict) and i.get("type") == "tun"),
        None,
    )
    if tun and tun.get("auto_redirect"):
        if default_mark:
            raise ValueError("conflict: auto_redirect + route.default_mark")
        for o in cfg.get("outbounds", []):
            if isinstance(o, dict) and o.get("routing_mark"):
                raise ValueError(
                    f"conflict: auto_redirect + routing_mark on {o.get('tag')}"
                )
        if tun.get("auto_redirect_disable_mark_mode") and tun.get(
            "route_exclude_address_set"
        ):
            raise ValueError(
                "conflict: auto_redirect_disable_mark_mode + route_exclude_address_set"
            )


def expand_tag(url: str, tag: str) -> str:
    return url.replace("{tag}", tag)


def build_dns(intents):
    """spec §5 DNS skeleton (real IP, no fakeip)."""
    servers = [
        {"tag": "proxy-dns", "type": "https", "server": "1.1.1.1", "detour": "PROXY"},
        {"tag": "direct-dns", "type": "udp", "server": "223.5.5.5"},
    ]
    rules = []
    for it in intents:
        rs = it["rule_set"]
        if it["dns"] == "reject":
            rules.append({"rule_set": rs, "action": "reject"})
        elif it["dns"] == "direct":
            rules.append({"rule_set": rs, "action": "route", "server": "direct-dns"})
        else:  # proxy
            rules.append({"rule_set": rs, "action": "route", "server": "proxy-dns"})
    return {
        "servers": servers,
        "rules": rules,
        "final": "proxy-dns",
    }


def build_route(intents, rule_sets, extra_rules=None):
    rules = [{"action": "sniff"}, {"protocol": "dns", "action": "hijack-dns"}]
    # package_rules（phone）优先级高于意图规则
    rules.extend(extra_rules or [])
    for it in intents:
        rules.append({"rule_set": it["rule_set"], "outbound": it["route"]})
    rs_decl = []
    for rs in rule_sets:
        decl = {
            "type": "remote",
            "tag": rs["tag"],
            "format": rs.get("format", "binary"),
            "url": expand_tag(rs["url"], rs["tag"]),
        }
        # pin 住的 URL 不带 update_interval：升级走人工放行，不无脑跟
        if "update_interval" in rs:
            decl["update_interval"] = rs["update_interval"]
        rs_decl.append(decl)
    return {
        "rules": rules,
        "rule_set": rs_decl,
        "final": "Final",
        # 1.12 起废弃隐式解析，1.14 必须显式指定；direct-dns 避免代理 bootstrapping 死锁
        "default_domain_resolver": "direct-dns",
    }


def build_groups(groups):
    outbounds = []
    for g in groups:
        o = {"tag": g["name"], "type": g["type"]}
        if g["type"] == "selector":
            o["outbounds"] = g["outbounds"]
            o["default"] = g.get("default", g["outbounds"][0])
        elif g["type"] == "urltest":
            if g.get("providers") == "all":
                o["use_all_providers"] = True
            else:
                o["providers"] = g["providers"]
            if "include" in g:
                o["include"] = g["include"]
            o["url"] = g.get("url", "https://www.gstatic.com/generate_204")
            o["interval"] = g.get("interval", "3m")
            if "tolerance" in g:
                o["tolerance"] = g["tolerance"]
        outbounds.append(o)
    # builtin outbounds referenced by intents/groups
    outbounds.append({"type": "direct", "tag": "DIRECT"})
    outbounds.append({"type": "block", "tag": "REJECT"})
    return outbounds


def build_tun_inbound(dev):
    tun = dev.get("inbound_tun", {})
    inbound = {
        "type": "tun",
        "tag": tun.get("tag", "tun-in"),
        "address": tun.get("address", ["172.19.0.1/30", "fdfe:dcba:9876::1/126"]),
    }
    # 数据驱动：device yaml 里写什么就透传什么（不写 stack = 新栈）
    for k in ("auto_route", "auto_redirect",
              "route_exclude_address_set", "route_address_set",
              "include_mac_address", "exclude_mac_address",
              "include_package", "exclude_package",
              "dns_mode", "multi_queue", "platform",
              "auto_redirect_disable_mark_mode"):
        if tun.get(k) is not None:
            inbound[k] = tun[k]
    return inbound


def build_experimental(dev):
    """experimental 段由 device yaml 决定（router/phone 路径、监听地址不同）。"""
    exp = dev.get("experimental", {})
    out = {}
    if "cache_file" in exp:
        out["cache_file"] = exp["cache_file"]
    if "clash_api" in exp:
        out["clash_api"] = exp["clash_api"]
    return out


def build_package_rules(dev):
    """phone 专属：package_name 应用分流表，优先级高于意图规则。"""
    rules = []
    for pr in dev.get("package_rules", []) or []:
        rules.append({
            "package_name": pr["package_name"],
            "outbound": pr["outbound"],
        })
    return rules


def fake_providers():
    return [
        {
            "type": "local",
            "tag": "ci-fake",
            "path": "providers/ci-fake.json",
        }
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="router")
    ap.add_argument("--providers", default="providers.yaml")
    ap.add_argument("--out", default=None)
    ap.add_argument("--check-only", action="store_true",
                    help="lint contracts only, don't write output")
    args = ap.parse_args()

    try:
        intents_doc = yaml.safe_load((ROOT / "intents.yaml").read_text(encoding="utf-8"))
        dev = yaml.safe_load(
            (ROOT / "devices" / f"{args.device}.yaml").read_text(encoding="utf-8")
        )
    except FileNotFoundError as e:
        print(f"missing contract: {e}", file=sys.stderr)
        return 1

    # contracts themselves must not use deprecated fields
    try:
        lint_deprecated(intents_doc, "intents.yaml")
        lint_deprecated(dev, f"devices/{args.device}.yaml")
    except ValueError as e:
        print(f"LINT FAIL: {e}", file=sys.stderr)
        return 2

    providers_path = ROOT / args.providers
    if providers_path.exists():
        providers = yaml.safe_load(providers_path.read_text(encoding="utf-8"))["providers"]
        print(f"using {args.providers} ({len(providers)} providers)")
    else:
        providers = fake_providers()
        print(f"{args.providers} not found, using fake local provider for check")
    try:
        lint_deprecated(providers, args.providers)
    except ValueError as e:
        print(f"LINT FAIL: {e}", file=sys.stderr)
        return 2

    intents = intents_doc["intents"]
    rule_sets = intents_doc.get("rule_sets", [])

    dns = build_dns(intents)
    route = build_route(intents, rule_sets, build_package_rules(dev))

    cfg = {
        "log": {"level": dev.get("log", {}).get("level", "info"), "timestamp": True},
        "dns": dns,
        "inbounds": [build_tun_inbound(dev)],
        "outbounds": build_groups(intents_doc.get("groups", [])),
        "route": route,
        "providers": providers,
        "experimental": build_experimental(dev),
    }

    try:
        lint_deprecated(cfg)
        lint_dns_rules(dns)
        lint_conflicts(cfg)
    except ValueError as e:
        print(f"LINT FAIL: {e}", file=sys.stderr)
        return 2

    if args.check_only:
        print("contracts lint OK")
        return 0

    out = Path(args.out) if args.out else ROOT / "dist" / f"{args.device}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {out} ({len(json.dumps(cfg))} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

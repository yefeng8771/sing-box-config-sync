import json
import tempfile
import unittest
from pathlib import Path

from build import extract_kb, migrate

ROOT = Path(__file__).resolve().parents[1]


class MigrateCompilerTests(unittest.TestCase):
    def test_110_to_115_full_ast_migration(self):
        legacy_jsonc = """
        {
          // 1.10 - 1.14 混合旧版配置（含注释与尾随逗号）
          "dns": {
            "independent_cache": true,
            "servers": [
              {"tag": "local-dns", "address": "local"},
              {"tag": "cf-doh", "address": "https://cloudflare-dns.com/dns-query", "address_resolver": "local-dns"},
              {"tag": "fakeip", "address": "fakeip"},
            ],
            "fakeip": {
              "enabled": true,
              "inet4_range": "198.18.0.0/15",
            },
            "rules": [
              {"outbound": "any", "server": "local-dns"},
              {"rule_set": "geoip-cn", "ip_is_private": true, "rule_set_ip_cidr_accept_empty": true, "strategy": "ipv4_only", "server": "local-dns"},
            ],
            "final": "cf-doh",
          },
          "inbounds": [
            {
              "type": "tun",
              "tag": "tun-in",
              "stack": "system",
              "inet4_address": "172.19.0.1/30",
              "inet6_address": "fdfe:dcba:9876::1/126",
              "sniff": true,
              "sniff_timeout": "300ms",
              "domain_strategy": "prefer_ipv4",
            },
            {
              "type": "trojan",
              "tag": "trojan-in",
              "tls": {
                "enabled": true,
                "acme": {"domain": ["example.com"], "email": "ops@example.com"}
              }
            }
          ],
          "outbounds": [
            {"type": "direct", "tag": "direct", "domain_strategy": "prefer_ipv4"},
            {"type": "block", "tag": "block-out"},
            {"type": "dns", "tag": "dns-out"},
            {"type": "selector", "tag": "PROXY", "outbounds": ["direct", "block-out"], "default": "block-out"}
          ],
          "route": {
            "rules": [
              {"protocol": "dns", "outbound": "dns-out"},
              {"rule_set": ["ads"], "outbound": "block-out"},
            ],
            "rule_set": [
              {
                "type": "remote",
                "tag": "ads",
                "format": "binary",
                "url": "https://example.com/ads.srs",
                "download_detour": "PROXY",
              }
            ],
            "final": "PROXY"
          },
          "experimental": {
            "clash_api": {
              "external_controller": "127.0.0.1:9090",
              "cache_file": "legacy.db",
              "store_fakeip": true
            },
            "cache_file": {
              "enabled": true,
              "store_rdrc": true
            }
          }
        }
        """
        cfg = migrate.parse_jsonc(legacy_jsonc)
        out, applied, issues = migrate.migrate_config(cfg)
        rule_ids = {r["rule_id"] for r in applied}

        # Verify all expected rules fired
        for expected in (
            "MIG-115-TUN-STACK",
            "MIG-114-DOWNLOAD-DETOUR",
            "MIG-114-TLS-ACME",
            "MIG-114-DNS-INDEPENDENT-CACHE",
            "MIG-114-CACHE-STORE-RDRC",
            "MIG-114-DNS-RULE-LEGACY",
            "MIG-112-DNS-SERVERS",
            "MIG-112-OUTBOUND-DNS-RULE",
            "MIG-112-DIAL-DOMAIN-STRATEGY",
            "MIG-111-SPECIAL-OUTBOUNDS",
            "MIG-111-INBOUND-SNIFF",
            "MIG-110-TUN-ADDRESS",
            "MIG-108-CLASH-CACHE",
        ):
            self.assertIn(expected, rule_ids)

        # Verify 1.15 TUN stack removed and addresses merged
        tun = out["inbounds"][0]
        self.assertNotIn("stack", tun)
        self.assertEqual(tun["address"], ["172.19.0.1/30", "fdfe:dcba:9876::1/126"])
        self.assertNotIn("sniff", tun)

        # Verify TLS ACME migrated to certificate_provider
        trojan_tls = out["inbounds"][1]["tls"]
        self.assertNotIn("acme", trojan_tls)
        self.assertEqual(trojan_tls["certificate_provider"]["type"], "acme")

        # Verify special outbounds removed and route rules converted to actions
        ob_types = [o["type"] for o in out["outbounds"]]
        self.assertNotIn("block", ob_types)
        self.assertNotIn("dns", ob_types)
        actions = [r.get("action") for r in out["route"]["rules"]]
        self.assertIn("sniff", actions)
        self.assertIn("resolve", actions)
        self.assertIn("hijack-dns", actions)
        self.assertIn("reject", actions)

        # Verify download_detour migrated to http_clients
        rs = out["route"]["rule_set"][0]
        self.assertNotIn("download_detour", rs)
        self.assertEqual(rs["http_client"], "hc-proxy")
        self.assertEqual(out["http_clients"][0]["tag"], "hc-proxy")
        self.assertEqual(out["route"]["default_http_client"], "hc-proxy")

        # Verify DNS servers & rules upgraded
        self.assertNotIn("independent_cache", out["dns"])
        self.assertEqual(out["dns"]["servers"][0]["type"], "local")
        self.assertEqual(out["dns"]["servers"][1]["type"], "https")
        self.assertEqual(out["dns"]["servers"][1]["domain_resolver"], "local-dns")
        self.assertEqual(out["dns"]["servers"][2]["inet4_range"], "198.18.0.0/15")
        self.assertEqual(out["route"]["default_domain_resolver"], "local-dns")
        self.assertEqual(out["dns"]["rules"][0]["action"], "evaluate")
        self.assertTrue(out["dns"]["rules"][1]["match_response"])

        # Verify cache_file store_dns
        self.assertTrue(out["experimental"]["cache_file"]["store_dns"])
        self.assertNotIn("store_rdrc", out["experimental"]["cache_file"])

        # Zero schema errors
        errors = [i for i in issues if i["severity"] == "error"]
        self.assertEqual(errors, [])

    def test_conflict_and_redline_detection(self):
        bad_cfg = {
            "inbounds": [
                {
                    "type": "tun",
                    "auto_redirect": True,
                    "auto_redirect_disable_mark_mode": True,
                    "route_exclude_address_set": ["geoip-cn"],
                }
            ],
            "route": {"default_mark": 666},
            "unknown_top_field": True,
        }
        issues = migrate.validate_config(bad_cfg)
        codes = {i["code"] for i in issues}
        self.assertIn("SCHEMA-UNKNOWN-TOP-KEY", codes)
        self.assertIn("CONFLICT-AUTO-REDIRECT-MARK", codes)
        self.assertIn("CONFLICT-MARK-MODE-EXCLUDE-SET", codes)
        self.assertEqual(migrate.check_custom_direct_domains_redline(), [])

    def test_extract_kb_generates_rules_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "dist"
            self.assertEqual(extract_kb.main(out_dir=out_dir), 0)

            rules = json.loads((out_dir / "migration-rules.json").read_text(encoding="utf-8"))
            kb = (out_dir / "sing-box-llm-kb.md").read_text(encoding="utf-8")

            self.assertGreater(len(rules["ast_rules"]), 0)
            self.assertGreater(len(rules["deprecated_items"]), 0)
            # 产物必须是确定性输出，不含生成时间戳（否则每日同步必产生空提交）
            self.assertNotIn("generated_at", rules["metadata"])
            # 顶层字段表必须覆盖 schema 的全部顶层字段
            for field in rules["schema_summary"]["top_level_properties"]:
                self.assertIn(f"| `{field}` |", kb)

            # 同一输入再跑一次必须字节级一致
            out_dir2 = Path(tmp) / "dist2"
            self.assertEqual(extract_kb.main(out_dir=out_dir2), 0)
            self.assertEqual(
                (out_dir / "migration-rules.json").read_bytes(),
                (out_dir2 / "migration-rules.json").read_bytes(),
            )
            self.assertEqual(
                (out_dir / "sing-box-llm-kb.md").read_bytes(),
                (out_dir2 / "sing-box-llm-kb.md").read_bytes(),
            )

    def test_sniff_override_migrates_to_separate_action(self):
        """sniff_override_destination 必须迁为独立动作，不能作为 sniff 的字段。"""
        cfg = {
            "inbounds": [
                {
                    "type": "tun",
                    "tag": "tun-in",
                    "sniff": True,
                    "sniff_timeout": "100ms",
                    "sniff_override_destination": True,
                }
            ],
            "outbounds": [{"type": "direct", "tag": "direct"}],
        }
        out, _, _ = migrate.migrate_config(cfg)
        actions = [r.get("action") for r in out["route"]["rules"]]
        self.assertIn("sniff", actions)
        self.assertIn("sniff-override-destination", actions)
        for rule in out["route"]["rules"]:
            self.assertNotIn("override_destination", rule)

    def test_evaluate_server_points_to_existing_dns_tag(self):
        """缺 dns.final 时，evaluate 必须指向真实存在的 server tag。"""
        cfg = {
            "dns": {
                "servers": [{"tag": "dns-local", "address": "local"}],
                "rules": [{"rule_set": ["cn"], "ip_is_private": True}],
            },
            "outbounds": [{"type": "direct", "tag": "direct"}],
        }
        out, _, _ = migrate.migrate_config(cfg)
        tags = {s["tag"] for s in out["dns"]["servers"] if isinstance(s, dict) and s.get("tag")}
        evaluate = [r for r in out["dns"]["rules"] if r.get("action") == "evaluate"]
        self.assertEqual(len(evaluate), 1)
        self.assertIn(evaluate[0]["server"], tags)

    def test_no_dns_server_produces_no_dangling_reference(self):
        """没有任何带 tag 的 DNS 服务器时，不得产出悬空引用。"""
        cfg = {
            "dns": {"rules": [{"ip_is_private": True}]},
            "outbounds": [{"type": "direct", "tag": "direct", "domain_strategy": "prefer_ipv4"}],
        }
        out, _, issues = migrate.migrate_config(cfg)
        codes = {i["code"] for i in issues}

        self.assertNotIn("domain_strategy", out["outbounds"][0])
        self.assertNotIn("domain_resolver", out["outbounds"][0])
        self.assertIn("MIG-112-NO-DNS-SERVER", codes)

        dns_rule = out["dns"]["rules"][0]
        self.assertNotIn("match_response", dns_rule)
        self.assertIn("MIG-114-NO-EVALUATE-SERVER", codes)


if __name__ == "__main__":
    unittest.main()

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
        rc = extract_kb.main()
        self.assertEqual(rc, 0)
        self.assertTrue((ROOT / "dist" / "migration-rules.json").exists())
        self.assertTrue((ROOT / "dist" / "sing-box-llm-kb.md").exists())


if __name__ == "__main__":
    unittest.main()

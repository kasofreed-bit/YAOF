"""Fault-injection checks for the build gate, not firmware boot tests.

Fixtures: package manifest from successful build 39280782139; Passwall recipe
from QiuSimons/OpenWrt-Add at 01c1b9c927791bd33f7bfdcfe454e5b8a4cdcf3f.
Run: python3 -m unittest discover -s tests -v
"""
import gzip
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"


class R2SVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.prepared = self.parent / "openwrt"
        seed = ROOT / "SEED/R2S/config.seed"
        (self.parent / "SEED/R2S").mkdir(parents=True)
        shutil.copyfile(seed, self.parent / "SEED/R2S/config.seed")
        disabled = set(re.findall(r"^# CONFIG_PACKAGE_(.+) is not set$", seed.read_text(), re.M))
        self.packages = dict(line.split(" - ", 1) for line in
                             (FIXTURES / "r2s-before-trim.manifest").read_text().splitlines())
        self.packages = {k: v for k, v in self.packages.items() if k not in disabled}
        self.config = "\n".join("CONFIG_PACKAGE_" + p + "=y" for p in self.packages) + "\n"
        self.config += "CONFIG_TARGET_rockchip_armv8_DEVICE_friendlyarm_nanopi-r2s=y\n"
        for option in ("INCLUDE_Xray", "INCLUDE_Geoview", "INCLUDE_V2ray_Geodata",
                       "Nftables_Transparent_Proxy"):
            self.config += "CONFIG_PACKAGE_luci-app-passwall_" + option + "=y\n"
        recipe_dir = self.prepared / "package/new/openwrt_helloworld"
        for package, fixture in (("luci-app-passwall", "passwall.Makefile"),
                                 ("xray-core", "xray.Makefile")):
            (recipe_dir / package).mkdir(parents=True)
            shutil.copyfile(FIXTURES / fixture, recipe_dir / package / "Makefile")
        self.recipe = recipe_dir / "luci-app-passwall/Makefile"
        self.target = self.prepared / "bin/targets/rockchip/armv8"
        self.target.mkdir(parents=True)
        self.manifest = self.target / "openwrt-rockchip-armv8-friendlyarm_nanopi-r2s.manifest"

    def run_gate(self, mode="config", config=None, packages=None):
        (self.prepared / ".config").write_text(self.config if config is None else config)
        packages = self.packages if packages is None else packages
        self.manifest.write_text("".join(f"{p} - {v}\n" for p, v in packages.items()))
        return subprocess.run([sys.executable, str(ROOT / "SCRIPTS/10_verify_r2s.py"), mode],
                              cwd=self.prepared, capture_output=True, text=True)

    def assert_rejected(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_xray_only_selection_is_accepted(self):
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rtl8152_selection_does_not_require_generic_usbnet(self):
        config = self.config.replace("CONFIG_PACKAGE_kmod-usb-net=y\n", "")
        result = self.run_gate(config=config)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_removed_components_cannot_be_reselected(self):
        for package in ("sing-box", "haproxy", "shadowsocks-rust-sslocal",
                        "shadowsocksr-libev-ssr-redir", "simple-obfs-client", "v2ray-plugin",
                        "luci-app-upnp", "miniupnpd-nftables", "wireguard-tools",
                        "kmod-wireguard", "vmlinux-btf", "kmod-backlight-pwm", "mosdns",
                        "luci-app-flowcone", "flowcone"):
            with self.subTest(package=package):
                self.assert_rejected(self.run_gate(config=self.config + f"CONFIG_PACKAGE_{package}=y\n"),
                                     "disabled packages selected: " + package)

    def test_default_optional_core_switch_cannot_regress(self):
        self.assert_rejected(self.run_gate(config=self.config +
                             "CONFIG_PACKAGE_luci-app-passwall_INCLUDE_SingBox=y\n"),
                             "disabled packages selected: luci-app-passwall_INCLUDE_SingBox")

    def test_boot_network_dns_and_ipv6_dependencies_are_protected(self):
        for package in ("base-files", "busybox", "procd", "fstools", "netifd", "dropbear",
                        "kmod-usb-net-rtl8152", "r8152-firmware", "kmod-mii", "kmod-libphy",
                        "kmod-crypto-sha256", "nftables-json", "kmod-nft-tproxy",
                        "chinadns-ng", "dnsmasq-full", "geoview", "v2ray-geosite",
                        "odhcp6c", "odhcpd-ipv6only", "kmod-nf-conntrack6"):
            with self.subTest(package=package):
                config = self.config.replace(f"CONFIG_PACKAGE_{package}=y\n", "")
                self.assert_rejected(self.run_gate(config=config), "missing selections: " + package)

    def test_prepared_recipe_new_hard_dependency_is_protected(self):
        self.recipe.write_text(self.recipe.read_text().replace("LUCI_DEPENDS:=", "LUCI_DEPENDS:=+new-helper "))
        self.assert_rejected(self.run_gate(), "missing selections: new-helper")

    def test_dependency_conflict_with_trim_policy_stops_build(self):
        self.recipe.write_text(self.recipe.read_text().replace("LUCI_DEPENDS:=", "LUCI_DEPENDS:=+sing-box "))
        self.assert_rejected(self.run_gate(), "required packages disabled: sing-box")

    def test_changed_dependency_syntax_needs_review(self):
        self.recipe.write_text(self.recipe.read_text().replace("LUCI_DEPENDS:=", "LUCI_DEPENDS:=+FEATURE:helper "))
        self.assert_rejected(self.run_gate(), "dependency syntax changed; review required")

    def test_trimmed_manifest_accepted_with_synthetic_gzip_payloads(self):
        # Valid gzip containers containing random test bytes, not bootable images.
        payload = gzip.compress(os.urandom(1024 * 1024 + 128))
        for filesystem in ("ext4", "squashfs"):
            (self.target / f"openwrt-rockchip-armv8-friendlyarm_nanopi-r2s-{filesystem}-sysupgrade.img.gz").write_bytes(payload)
        # Real r8152 dependencies must suffice when phone adapters no longer
        # pull the unrelated generic USB-network driver into the image.
        config = self.config.replace("CONFIG_PACKAGE_kmod-usb-net=y\n", "")
        packages = {k: v for k, v in self.packages.items() if k != "kmod-usb-net"}
        result = self.run_gate("image", config=config, packages=packages)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_removed_components_cannot_reappear_in_image(self):
        for package in ("sing-box", "haproxy", "miniupnpd-nftables", "kmod-wireguard", "vmlinux-btf",
                        "luci-app-flowcone", "flowcone"):
            with self.subTest(package=package):
                self.assert_rejected(self.run_gate("image", packages={**self.packages, package: "test"}),
                                     "contains disabled packages: " + package)

    def test_image_missing_ethernet_dns_or_ipv6_stops_publication(self):
        for package in ("kmod-usb-net-rtl8152", "chinadns-ng", "kmod-nft-tproxy", "odhcp6c"):
            with self.subTest(package=package):
                packages = {k: v for k, v in self.packages.items() if k != package}
                self.assert_rejected(self.run_gate("image", packages=packages), "missing packages: " + package)

    def test_wrong_xray_image_version_stops_publication(self):
        self.assert_rejected(self.run_gate("image", packages={**self.packages, "xray-core": "0.0.0-r1"}),
                             "Xray in image does not match")


if __name__ == "__main__":
    unittest.main()

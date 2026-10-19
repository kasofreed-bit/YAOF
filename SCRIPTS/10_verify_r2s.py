#!/usr/bin/env python3
"""Build-time checks, run from the prepared OpenWrt directory."""
import json
import os
from pathlib import Path
import re
import sys


REQUIRED = {
    "luci-app-passwall", "xray-core", "sing-box", "chinadns-ng", "dns2socks",
    "microsocks", "luci-app-mosdns", "mosdns", "geo2txt", "v2ray-geoip",
    "v2ray-geosite", "luci-app-upnp", "miniupnpd-nftables", "dnsmasq-full",
    "firewall4", "luci-base", "luci-nginx", "luci-theme-aurora",
}


def require(condition, message):
    if not condition:
        raise SystemExit("R2S verification failed: " + message)


def main():
    require(len(sys.argv) == 2 and sys.argv[1] in {"config", "image"},
            "usage: 10_verify_r2s.py config|image")
    config = Path(".config").read_text()
    selected = set(re.findall(r"^CONFIG_PACKAGE_(.+)=y$", config, re.M))
    seed = Path("../SEED/R2S/config.seed").read_text()
    disabled = set(re.findall(r"^# CONFIG_PACKAGE_(.+) is not set$", seed, re.M))
    require(not REQUIRED - selected, "missing selections: " + ", ".join(sorted(REQUIRED - selected)))
    require(not disabled & selected, "disabled packages selected: " + ", ".join(sorted(disabled & selected)))
    for symbol in (
        "CONFIG_TARGET_rockchip_armv8_DEVICE_friendlyarm_nanopi-r2s",
        "CONFIG_PACKAGE_luci-app-passwall_INCLUDE_Xray",
        "CONFIG_PACKAGE_luci-app-passwall_Nftables_Transparent_Proxy",
    ):
        require(symbol + "=y" in config.splitlines(), "missing " + symbol)

    recipe = Path("package/new/openwrt_helloworld/xray-core/Makefile").read_text()
    version = re.search(r"^PKG_VERSION:=(\d+\.\d+\.\d+)$", recipe, re.M)
    require(version, "unrecognized Xray package version")
    expected = version.group(1)
    print("R2S package selection verified; expected Xray " + expected)
    if sys.argv[1] == "config":
        return

    target = Path("bin/targets/rockchip/armv8")
    manifests = sorted(target.glob("*nanopi-r2s*.manifest"))
    require(manifests, "no R2S image manifest")
    reports = []
    for manifest in manifests:
        packages = {}
        for line in manifest.read_text().splitlines():
            name, separator, package_version = line.partition(" - ")
            if separator:
                packages[name] = package_version
        require(not REQUIRED - packages.keys(), manifest.name + " missing packages: " +
                ", ".join(sorted(REQUIRED - packages.keys())))
        require(not disabled & packages.keys(), manifest.name + " contains disabled packages: " +
                ", ".join(sorted(disabled & packages.keys())))
        require(re.fullmatch(re.escape(expected) + r"(?:[-+].*)?", packages["xray-core"]),
                "Xray in image does not match selected release: " + packages["xray-core"])
        reports.append({"manifest": manifest.name,
                        "packages": {name: packages[name] for name in sorted(REQUIRED)}})
    images = []
    for filesystem in ("ext4", "squashfs"):
        matches = sorted(target.glob("*nanopi-r2s-" + filesystem + "-sysupgrade.img.gz"))
        require(len(matches) == 1, "expected one R2S " + filesystem + " sysupgrade image")
        image = matches[0]
        require(image.stat().st_size > 1024 * 1024, "unexpectedly small image: " + image.name)
        with image.open("rb") as stream:
            require(stream.read(2) == b"\x1f\x8b", "invalid gzip header: " + image.name)
        images.append(image.name)
    Path("r2s-verification.json").write_text(json.dumps({
        "commit": os.environ.get("GITHUB_SHA"), "expected_xray": expected,
        "manifests": reports, "images": images,
    }, ensure_ascii=False, indent=2) + "\n")
    print("R2S firmware manifests, required packages, Xray version and image files verified.")


if __name__ == "__main__":
    main()

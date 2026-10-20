#!/bin/bash
# Build-time only; run after package preparation, before make defconfig.
set -euo pipefail

# These unused recipes caused recursive Kconfig dependencies in the first build.
# Keep Passwall, its protocol cores, UPnP and system packages intact.
rm -rf package/new/homeproxy \
  package/new/openwrt_helloworld/luci-app-momo \
  package/new/openwrt_helloworld/momo \
  package/new/openwrt_helloworld/mihomo-alpha \
  package/new/openwrt_helloworld/mihomo-meta \
  package/new/OpenWrt-mihomo \
  feeds/luci/applications/luci-app-weechat

for package_name in luci-app-homeproxy luci-app-momo momo mihomo-alpha \
  mihomo-meta luci-app-nikki nikki luci-app-weechat; do
  rm -f "package/feeds/luci/$package_name" "package/feeds/packages/$package_name"
done

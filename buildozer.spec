[app]
title = CLC Kenya
package.name = clckenya
package.domain = org.clckenya

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json

version = 1.0.0

requirements = python3,kivy,flet

# Permissions
android.permissions = INTERNET,ACCESS_NETWORK_STATE

# Features
android.features = android.hardware.touchscreen

# Buildozer settings
[buildozer]
log_level = 2
warn_on_root = 1

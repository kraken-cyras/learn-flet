[app]
title = CLC KENYA
package.name = clck
package.domain = org.clckenya

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json

version = 1.0.0

requirements = python3,kivy,flet,requests,certifi,urllib3,chardet,idna,Pillow,openssl,sdl2,pyjnius,android,sqlite3

android.permissions = INTERNET,ACCESS_NETWORK_STATE,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE,CAMERA,RECORD_AUDIO,VIBRATE,WAKE_LOCK,FOREGROUND_SERVICE

android.features = android.hardware.touchscreen

icon.filename = assets/icon.png
# Supported architectures
arch = armeabi-v7a, arm64-v8a

# Buildozer settings
[buildozer]
log_level = 2
warn_on_root = 1

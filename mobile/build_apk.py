"""
Sehatyar / PharmaDost — Standalone Android APK Package Builder Config
---------------------------------------------------------------------
Generates the native Android Manifest and APK build specs for Sehatyar Mobile App.
This config ensures the app runs 100% full-screen without Chrome address bars or browser tabs.
"""

import json
import os

ANDROID_MANIFEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="online.sehatyar.app">

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE" />
    <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" />

    <application
        android:allowBackup="true"
        android:icon="@mipmap/ic_launcher"
        android:label="Sehatyar"
        android:roundIcon="@mipmap/ic_launcher_round"
        android:supportsRtl="true"
        android:theme="@style/AppTheme"
        android:usesCleartextTraffic="false">
        
        <activity
            android:name=".MainActivity"
            android:configChanges="orientation|keyboardHidden|keyboard|screenSize|locale|layoutDirection|fontScale|screenLayout|density|uiMode"
            android:label="Sehatyar"
            android:launchMode="singleTask"
            android:exported="true"
            android:theme="@style/AppTheme.NoActionBarLaunch">
            
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>

        </activity>
    </application>
</manifest>
"""

def generate_mobile_assets():
    os.makedirs("mobile/android", exist_ok=True)
    manifest_path = os.path.join("mobile", "android", "AndroidManifest.xml")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write(ANDROID_MANIFEST_XML)
    print(f"[Mobile App Setup] AndroidManifest.xml created at: {manifest_path}")

if __name__ == "__main__":
    generate_mobile_assets()

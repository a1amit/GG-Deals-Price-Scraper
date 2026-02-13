# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for GG Deals Scraper – single-file .exe build."""

import os
import importlib

block_cipher = None

# Locate the undetected_chromedriver package so we can bundle it
uc_pkg = os.path.dirname(importlib.import_module("undetected_chromedriver").__file__)

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("templates", "templates"),
        (uc_pkg, "undetected_chromedriver"),
    ],
    hiddenimports=[
        "undetected_chromedriver",
        "undetected_chromedriver.patcher",
        "undetected_chromedriver.reactor",
        "undetected_chromedriver.cdp",
        "undetected_chromedriver.dprocess",
        "undetected_chromedriver.webelement",
        "undetected_chromedriver.options",
        "undetected_chromedriver.devtool",
        "selenium",
        "selenium.webdriver",
        "selenium.webdriver.chrome",
        "selenium.webdriver.chrome.service",
        "selenium.webdriver.chrome.options",
        "selenium.webdriver.common.by",
        "selenium.webdriver.support",
        "selenium.webdriver.support.ui",
        "selenium.webdriver.support.expected_conditions",
        "selenium.common.exceptions",
        "scraper",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="GG Deals Scraper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Keep console visible so users can see scraper logs
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

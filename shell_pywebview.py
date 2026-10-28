#!/usr/bin/env python3
"""Native window via pywebview (macOS WKWebView / Windows WebView2). pip install pywebview"""
import sys
import webview


class Api:
    def __init__(self):
        self.win = None

    def pin(self, on):
        self.win.on_top = bool(on)


api = Api()
api.win = webview.create_window("Livescribe", sys.argv[1], width=460, height=700, on_top=True,
                                js_api=api, background_color="#0d0f13")
webview.start()

#!/usr/bin/env python3
"""Native window via pywebview (macOS WKWebView / Windows WebView2). pip install pywebview"""
import sys


def run(url):
    import webview

    class Api:
        win = None

        def pin(self, on):
            self.win.on_top = bool(on)

    api = Api()
    api.win = webview.create_window("Livescribe", url, width=460, height=700, on_top=True,
                                    js_api=api, background_color="#0d0f13")
    webview.start()


if __name__ == "__main__":
    run(sys.argv[1])

"""Drive the real Instagram web DM UI via CDP and capture the POST requests
it makes when sending a message (to the self-thread). Records to /tmp/uiposts.json.
"""
import asyncio
import json
import time
import urllib.parse
import urllib.request

import websockets

COOKIES = {}
for line in open("session/cookies.txt"):
    line = line.strip()
    if line.startswith("#HttpOnly_"):
        line = line[len("#HttpOnly_"):]
    if not line or line.startswith("#"):
        continue
    p = line.split("\t")
    if len(p) != 7:
        continue
    COOKIES[p[5]] = {"domain": "." + p[0].lstrip("."), "path": p[2],
                     "secure": p[3] == "TRUE", "value": p[6]}


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.mid = 0
        self.events = []

    async def send(self, method, params=None, session_id=None):
        self.mid += 1
        msg = {"id": self.mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        await self.ws.send(json.dumps(msg))
        # wait for matching id
        while True:
            data = json.loads(await self.ws.recv())
            if data.get("id") == self.mid:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})
            self.events.append(data)

    async def eval_js(self, expr):
        r = await self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                                 "awaitPromise": True})
        return r.get("result", {}).get("value")


async def main():
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:9223/json"))
    page = next(t for t in targets if t["type"] == "page")
    posts = []
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        for name, c in COOKIES.items():
            await cdp.send("Network.setCookie", {
                "name": name, "value": c["value"], "domain": c["domain"], "path": c["path"],
                "secure": c["secure"], "httpOnly": True, "sameSite": "None"})
        await cdp.send("Network.enable")
        await cdp.send("Page.enable")
        await cdp.send("Runtime.enable")
        await cdp.send("Page.navigate", {"url": "https://www.instagram.com/direct/inbox/"})
        await asyncio.sleep(12)

        async def pump():
            while True:
                try:
                    data = json.loads(await ws.recv())
                except Exception:
                    return
                m = data.get("method", "")
                if m == "Network.requestWillBeSent":
                    r = data["params"]["request"]
                    if r["method"] == "POST" and "instagram.com" in r["url"]:
                        posts.append({"url": r["url"], "headers": r["headers"],
                                      "postData": (r.get("postData") or "")[:600]})

        pump_task = asyncio.create_task(pump())

        # where are we?
        print("route:", await cdp.eval_js("location.pathname"))

        # find thread list items
        js = """
        (() => {
          const rows = [...document.querySelectorAll('div[role=\"listitem\"], a[href*=\"/direct/t/\"]')];
          return rows.slice(0, 12).map(r => {
            const rect = r.getBoundingClientRect();
            return {tag: r.tagName, text: (r.innerText||'').slice(0,60), x: rect.x+rect.width/2, y: rect.y+rect.height/2};
          });
        })()
        """
        rows = await cdp.eval_js(js)
        print("rows:", json.dumps(rows, indent=1)[:1200])
        if not rows:
            print("no rows found; dumping some HTML")
            print((await cdp.eval_js("document.body.innerText.slice(0,500)")))
            return

        # click the first row (self-thread should be first per inbox order)
        target = rows[0]
        await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": target["x"], "y": target["y"], "button": "left", "clickCount": 1})
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": target["x"], "y": target["y"], "button": "left", "clickCount": 1})
        await asyncio.sleep(5)
        print("after click route:", await cdp.eval_js("location.pathname"))

        # focus the message input and type
        ok = await cdp.eval_js("""
        (() => {
          const el = document.querySelector('div[contenteditable=\"true\"][role=\"textbox\"]');
          if (!el) return false;
          el.focus();
          return true;
        })()
        """)
        print("textbox focused:", ok)
        await cdp.send("Input.insertText", {"text": "aerogram ui capture probe"})
        await asyncio.sleep(1.5)
        # send with Enter
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        await asyncio.sleep(6)
        pump_task.cancel()
    json.dump(posts, open("/tmp/uiposts.json", "w"), indent=1)
    print("captured POSTs:", len(posts))
    for p in posts:
        print("URL:", p["url"][:140])
        h = {k: v for k, v in p["headers"].items()
             if k.lower().startswith(("x-", "content-type", "sec-fetch", "origin", "referer", "accept"))}
        print("  headers:", json.dumps(h))
        print("  body:", p["postData"][:300])
        print()


if __name__ == "__main__":
    asyncio.run(main())

"""Capture Instagram web's DGW/MQTT-over-WSS traffic with headless Chrome + CDP.

v2: records requestId per frame so frames can be mapped to their socket URL,
and sends a self-DM via the REST API midway to generate incoming realtime traffic.
"""
import asyncio
import base64
import json
import time
import urllib.parse
import urllib.request

import websockets

DEBUG_PORT = 9223
COOKIES = {}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"


def load_cookies():
    for line in open("session/cookies.txt"):
        line = line.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _flag, path, secure, _exp, name, value = parts
        COOKIES[name] = {
            "domain": domain.lstrip("."),
            "path": path,
            "secure": secure == "TRUE",
            "name": name,
            "value": urllib.parse.unquote(value) if name in ("sessionid", "rur") else value,
        }


def send_self_dm():
    import httpx
    self_uid = COOKIES.get("ds_user_id", "")
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in COOKIES.values())
    r = httpx.post(
        "https://www.instagram.com/api/v1/direct_v2/threads/broadcast/text/",
        headers={
            "Cookie": cookie_header,
            "User-Agent": UA,
            "X-IG-App-ID": "936619743392459",
            "X-CSRFToken": COOKIES.get("csrftoken", ""),
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/",
        },
        data={
            "text": f"realtime capture trigger {int(asyncio.get_event_loop().time())}",
            "recipient_users": f'["{self_uid}"]',
            "_uuid": "b03a34b9-3d61-4d0d-90f6-43d6979d7818",
            "client_context": "capture-probe",
        },
        timeout=15,
    )
    print("self-DM status:", r.status_code, r.text[:200])


async def main():
    load_cookies()
    out = open("/tmp/wscapture2.jsonl", "w")
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json"))
    page = next(t for t in targets if t["type"] == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024) as ws:
        mid = 0

        async def send(method, params=None, session_id=None):
            nonlocal mid
            mid += 1
            msg = {"id": mid, "method": method, "params": params or {}}
            if session_id:
                msg["sessionId"] = session_id
            await ws.send(json.dumps(msg))
            return mid

        sid = None
        ws_urls = {}
        f = open("/dev/null")

        async def pump():
            while True:
                raw = await ws.recv()
                data = json.loads(raw)
                if "id" in data:
                    continue
                m = data.get("method", "")
                p = data.get("params", {})
                rid = p.get("requestId", "")
                if m == "Network.webSocketCreated":
                    ws_urls[rid] = p.get("url", "")
                    out.write(json.dumps({"t": time.time() - t0, "event": m, "rid": rid,
                                          "url": p.get("url")}) + "\n")
                    out.flush()
                elif m in ("Network.webSocketFrameSent", "Network.webSocketFrameReceived"):
                    resp = p.get("response", {})
                    rec = {"t": time.time() - t0, "event": m, "rid": rid,
                           "url": ws_urls.get(rid, "?"), "opcode": resp.get("opcode")}
                    if resp.get("opcode") == 2:
                        rec["b64"] = resp.get("payloadData")
                    else:
                        rec["text"] = (resp.get("payloadData") or "")[:5000]
                    out.write(json.dumps(rec) + "\n")
                    out.flush()

        t0 = time.time()
        pump_task = asyncio.create_task(pump())

        for c in COOKIES.values():
            await send("Network.setCookie", {
                "name": c["name"], "value": c["value"], "domain": "." + c["domain"],
                "path": c["path"], "secure": c["secure"], "httpOnly": True, "sameSite": "None",
            })
        await send("Network.enable")
        await send("Page.enable")
        await send("Page.navigate", {"url": "https://www.instagram.com/direct/inbox/"})

        # wait for load, then trigger an incoming message via REST
        await asyncio.sleep(35)
        await asyncio.get_event_loop().run_in_executor(None, send_self_dm)
        await asyncio.sleep(30)
        pump_task.cancel()
    out.close()
    print("done -> /tmp/wscapture2.jsonl")


if __name__ == "__main__":
    asyncio.run(main())

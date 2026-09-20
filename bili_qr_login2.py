"""Robust Bilibili QR login via passport API, parse cookies from poll response, save credential.json."""
import json
import time
import urllib.parse
from pathlib import Path

import qrcode
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
PNG_PATH = r"E:\AIbulid\hermesonly\bili_qr.png"

s = requests.Session()
s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})

# 1. generate qrcode
r = s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate", timeout=15)
g = r.json()
assert g["code"] == 0, g
qr_url = g["data"]["url"]
qr_key = g["data"]["qrcode_key"]
print("GENERATED key=" + qr_key[:8], flush=True)

qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=8, border=4)
qr.add_data(qr_url)
qr.make(fit=True)
qr.make_image(fill_color="black", back_color="white").save(PNG_PATH)
print("QR_SAVED", flush=True)

# 2. poll
while True:
    r = s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
              params={"qrcode_key": qr_key}, timeout=15)
    j = r.json()
    d = j.get("data") or {}
    code = int(d.get("code", -1))
    if code == 86101:
        print("SCAN", flush=True)
    elif code == 86090:
        print("CONF", flush=True)
    elif code == 86038:
        print("TIMEOUT", flush=True)
        break
    elif code == 0:
        url = d.get("url", "")
        print("DONE", flush=True)
        print("URL_KEYS " + json.dumps(
            {k: "len" + str(len(v[0])) if v else "" for k, v in
             urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()},
            ensure_ascii=False), flush=True)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        cookies = {}
        for name in ("SESSDATA", "bili_jct", "DedeUserID"):
            if name in q and q[name]:
                cookies[name.lower()] = q[name][0]
        # NEW: bilibili now delivers SESSDATA/bili_jct via Set-Cookie, not url
        sd = s.cookies.get_dict()
        for cname in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3", "buvid4"):
            if cname in sd and sd[cname]:
                cookies[cname.lower()] = sd[cname]
        cookies.setdefault("buvid3", "")
        cookies.setdefault("buvid4", "")
        print("PARSED " + json.dumps({k: (v[:6] + "...len" + str(len(v)) if v else "")
              for k, v in cookies.items()}), flush=True)
        cred = {
            "sessdata": cookies.get("sessdata", ""),
            "bili_jct": cookies.get("bili_jct", ""),
            "ac_time_value": d.get("refresh_token", ""),
            "buvid3": cookies.get("buvid3", ""),
            "buvid4": cookies.get("buvid4", ""),
            "dedeuserid": cookies.get("dedeuserid", ""),
            "saved_at": time.time(),
        }
        cfg = Path.home() / ".bilibili-cli"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "credential.json").write_text(
            json.dumps(cred, indent=2, ensure_ascii=False))
        print("LOGIN_DONE", flush=True)
        break
    else:
        print("UNKNOWN code=" + str(code), flush=True)
        break
    time.sleep(2)

import json
import re
import sys
import time
from http.cookiejar import CookieJar
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from pypdf import PdfReader

API_URL = sys.argv[1].rstrip("/")
MAILPIT_URL = sys.argv[2].rstrip("/")
EMAIL = sys.argv[3]
PASSWORD = sys.argv[4]
for value in (API_URL, MAILPIT_URL):
    if urlparse(value).scheme not in {"http", "https"}:
        raise ValueError("Las URLs deben usar HTTP o HTTPS.")


def json_request(url: str, method="GET", payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(  # noqa: S310 - URLs are restricted to HTTP(S) above.
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - validated HTTP(S) request.
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


status, login = json_request(f"{API_URL}/api/auth/login", "POST", {"email": EMAIL, "password": PASSWORD})
assert status == 200 and login["requires_2fa"] is True, login

code = None
for _ in range(20):
    status, inbox = json_request(f"{MAILPIT_URL}/api/v1/messages")
    assert status == 200, inbox
    messages = inbox.get("messages", [])
    if messages:
        message_id = messages[0]["ID"]
        status, message = json_request(f"{MAILPIT_URL}/api/v1/message/{message_id}")
        assert status == 200, message
        match = re.search(r"\b\d{6}\b", message.get("Text", ""))
        if match:
            code = match.group(0)
            break
    time.sleep(0.25)
assert code, "No se recibió el código 2FA en Mailpit."

status, verified = json_request(
    f"{API_URL}/api/auth/verify-2fa",
    "POST",
    {"code": code},
    login["pending_token"],
)
assert status == 200 and verified["user"]["role"] == "super_admin", verified
access_token = verified["access_token"]
status, profile = json_request(f"{API_URL}/api/auth/me", token=access_token)
assert status == 200 and profile["user"]["email"] == EMAIL, profile
status, users = json_request(f"{API_URL}/api/admin/users", token=access_token)
assert status == 200 and any(user["email"] == EMAIL for user in users["users"]), users
status, applications = json_request(f"{API_URL}/api/admin/applications?status=submitted", token=access_token)
assert status == 200, applications
status, _ = json_request(f"{API_URL}/api/auth/logout", "POST", {}, access_token)
assert status == 200
status, _ = json_request(f"{API_URL}/api/auth/me", token=access_token)
assert status == 401

# Exercise the HTML panel through Nginx, a cookie session and real Redis-backed revocation.
opener = build_opener(HTTPCookieProcessor(CookieJar()))


def web_request(path, form=None):
    request = Request(  # noqa: S310 - API_URL is restricted to HTTP(S).
        API_URL + path, data=urlencode(form).encode() if form is not None else None
    )
    with opener.open(request, timeout=60) as response:  # noqa: S310 - API_URL is restricted to HTTP(S).
        return response.url, response.read()


def csrf(content):
    return re.search(r'name="csrf_token" value="([^"]+)"', content.decode()).group(1)


_, page = web_request("/admin/login")
left, right = re.search(r"(\d+) \+ (\d+) =", page.decode()).groups()
_, inbox = json_request(f"{MAILPIT_URL}/api/v1/messages")
previous_messages = {message["ID"] for message in inbox["messages"]}
url, page = web_request(
    "/admin/login", dict(csrf_token=csrf(page), email=EMAIL, password=PASSWORD, captcha=str(int(left) + int(right)))
)
assert url.endswith("/admin/2fa"), url
new_code = None
for _ in range(20):
    _, inbox = json_request(f"{MAILPIT_URL}/api/v1/messages")
    for message in inbox["messages"]:
        if message["ID"] not in previous_messages:
            _, content = json_request(f"{MAILPIT_URL}/api/v1/message/{message['ID']}")
            match = re.search(r"\b\d{6}\b", content.get("Text", ""))
            if match:
                new_code = match.group(0)
                break
    if new_code:
        break
    time.sleep(0.25)
assert new_code, "No se recibió el segundo factor del panel."
url, page = web_request("/admin/2fa", dict(csrf_token=csrf(page), code=new_code))
assert url.endswith("/admin/dashboard"), url
for path in ["/admin/applications?type=new", "/admin/comparison?type=renewal", "/admin/settings"]:
    url, content = web_request(path)
    assert "/admin/login" not in url and b"<h1>" in content, path
_, content = web_request("/admin/applications/export.csv?type=new&status=submitted")
assert content.startswith(b"\xef\xbb\xbf") and b"HE27-" in content
if applications["items"]:
    item_id = applications["items"][0]["id"]
    _, content = web_request(f"/admin/applications/{item_id}/file.pdf")
    pdf = PdfReader(BytesIO(content))
    assert any(len(page.images) for page in pdf.pages)
    assert "Apoyo para desplazarse." in "\n".join(page.extract_text() for page in pdf.pages)
_, page = web_request("/admin/dashboard")
url, _ = web_request("/admin/logout", dict(csrf_token=csrf(page)))
url, _ = web_request("/admin/dashboard")
assert url.endswith("/admin/login")
print(
    json.dumps(
        dict(status="ok", twoFactor=True, adminApi=True, adminDashboard=True, csv=True, pdf=True, logoutRevoked=True)
    )
)

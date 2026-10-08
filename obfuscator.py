import re
import uuid
import secrets
import base64
import urllib.parse
from typing import Dict, Any


def generate_worker_name(prefix: str = "zeus") -> str:
    """Generate a random worker name with zeus prefix."""
    return f"{prefix}-{secrets.token_hex(4)}"


def generate_admin_path() -> str:
    """Generate a secret admin path for security."""
    return f"/panel_{secrets.token_hex(4)}"


def generate_uuid() -> str:
    return str(uuid.uuid4())


def generate_d1_name() -> str:
    return f"zeus_db_{secrets.token_hex(3)}"


def obfuscate_worker_js(source_code: str, config: Dict[str, Any]) -> str:
    """
    JavaScript obfuscation for anti-DPI/anti-detection by censorship systems:
    - Template variable substitution
    - String Array Extraction + Base64 Encoding
    - Array Rotation via IIFE
    - Dead code injection
    """
    code = source_code
    code = code.replace("{{NODE_NAME}}", config.get("worker_name", "zeus-node"))
    code = code.replace("{{ADMIN_PATH}}", config.get("admin_path", "/panel_secret"))
    code = code.replace("{{ROOT_UUID}}", config.get("uuid", generate_uuid()))
    code = code.replace("{{D1_DATABASE}}", config.get("d1_name", "zeus_db"))
    code = code.replace("{{SALT_KEY}}", config.get("salt", secrets.token_hex(16)))

    # String extraction to base64 array
    # Negative lookahead (?!\s*[:{]) skips object keys and import paths
    string_list = []
    string_map = {}

    def string_replacer(match):
        full  = match.group(0)
        val   = match.group(2)
        after = match.group(3)          # text right after closing quote
        # Skip: object keys (followed by :), imports (from "..."), require("...")
        if not val or len(val) < 2:
            return full
        if re.match(r'\s*[:{]', after or ""):
            return full
        if val.startswith("./") or val.startswith("../") or "/" in val[:10]:
            return full
        if val not in string_map:
            string_map[val] = len(string_list)
            encoded = base64.b64encode(
                urllib.parse.quote(val).encode("utf-8")
            ).decode("utf-8")
            string_list.append(encoded)
        idx = string_map[val]
        return f"_0xdec(0x{idx:x})"

    # Capture the character after the closing quote via a 3rd group
    code = re.sub(r'(["\'])((?:[^"\'\\]|\\.)*?)\1(.{0,2})', string_replacer, code)

    arr_name = f"_0x{secrets.token_hex(2)}"
    func_name = "_0xdec"
    rotator_offset = secrets.randbelow(150) + 50

    strings_joined = ",".join(f'"{s}"' for s in string_list)

    prefix_code = f"""var {arr_name} = [{strings_joined}];
(function(arr, offset) {{
  var rotator = function(count) {{
    while (--count) {{ arr.push(arr.shift()); }}
  }};
  rotator(++offset);
}})({arr_name}, 0x{rotator_offset:x});

var {func_name} = function(idx) {{
  idx = idx - 0;
  var str = {arr_name}[idx];
  try {{
    return decodeURIComponent(atob(str));
  }} catch(e) {{
    return atob(str);
  }}
}};
"""

    dead_code = f"""var _0x{secrets.token_hex(2)} = function(_0xa, _0xb) {{
  return (_0xa ^ _0xb) * 0x{secrets.token_hex(2)};
}};
"""

    return f"{prefix_code}\n{dead_code}\n{code}"


def generate_wrangler_toml(worker_name: str, d1_name: str, admin_path: str, uuid_str: str) -> str:
    return f"""name = "{worker_name}"
main = "_worker.js"
compatibility_date = "2026-01-20"
compatibility_flags = ["nodejs_compat"]

[[d1_databases]]
binding = "DB"
database_name = "{d1_name}"
database_id = "placeholder"

[vars]
ADMIN_PATH = "{admin_path}"
UUID = "{uuid_str}"
"""


def generate_readme(worker_name: str) -> str:
    return f"""# {worker_name}

Zeus Panel — Cloudflare Worker VLESS Proxy

## درباره
این یک پنل پروکسی VLESS روی Cloudflare Workers است که برای دور زدن
فیلترینگ اینترنت طراحی شده است.

## منبع
پروژه زئوس: https://github.com/panel-zeus/Z-E-U-S

## نصب
این Worker از طریق ابزار Zeus Deployer به صورت خودکار نصب شده است.
"""

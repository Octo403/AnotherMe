"""Local-only WeChat text cleaning. Never copies raw blobs, media or secret mappings."""
import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import re
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

ZONE = dt.timezone(dt.timedelta(hours=8))
SURNAMES = set("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳唐罗薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵季贾路江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫房裘缪解应宗丁宣邓郁单杭洪包诸左石崔吉龚程邢裴陆荣翁荀羊甄曲封芮储靳段富焦侯宁武刘景詹龙叶黎白蒲赖卓谭温庄晏柴瞿连习艾向古廖庾申桑桂牛寿边燕尚农文晁岳车韶耿关查荆游曾沙鞠丰巩阚简饶辛阎隋翟聂阙敖冷辛阎刁蒯甘巫邬欧阳司马")
LABELS = {"id_card": "身份证号", "phone": "手机号", "landline": "电话号码",
          "email": "邮箱", "bank_card": "银行卡号", "number": "编号",
          "password": "密码", "verification_code": "验证码", "secret": "密钥",
          "account": "账号", "address": "地址", "ip": "IP地址", "url": "链接"}


def initials(name: str) -> str:
    from pypinyin import Style, lazy_pinyin
    return "".join(lazy_pinyin(name, style=Style.FIRST_LETTER)).lower()


def possible_names(value: str) -> list[str]:
    names = []
    for part in re.findall(r"[\u4e00-\u9fff]+", value):
        for suffix in ("老师", "同学", "教授", "师兄", "师姐", "学长", "学姐"):
            if part.endswith(suffix):
                part = part[:-len(suffix)]
                break
        compound = part.startswith(("欧阳", "司马", "上官", "诸葛", "司徒", "司空", "夏侯", "皇甫"))
        if (2 <= len(part) <= 4 and part[0] in SURNAMES) or (compound and 3 <= len(part) <= 5):
            names.append(part)
    return names


class LiteralAliases:
    """Longest-match trie keeps contact aliases out of regex expressions."""
    def __init__(self, aliases: dict[str, str]):
        self.trie = {}
        for name, alias in aliases.items():
            name = unicodedata.normalize("NFKC", name)
            if not name or name == alias:
                continue
            node = self.trie
            for char in name:
                node = node.setdefault(char, {})
            node[None] = (name, alias)

    def replace(self, text: str):
        result, counts, index = [], Counter(), 0
        while index < len(text):
            node, cursor, match = self.trie, index, None
            while cursor < len(text) and text[cursor] in node:
                node = node[text[cursor]]
                cursor += 1
                if None in node:
                    source, alias = node[None]
                    # Avoid replacing short Latin nicknames inside ordinary words.
                    if source.isascii() and source.isalnum():
                        left = index > 0 and text[index - 1].isascii() and text[index - 1].isalnum()
                        right = cursor < len(text) and text[cursor].isascii() and text[cursor].isalnum()
                        if left or right:
                            continue
                    match = (cursor, alias)
            if match:
                index, alias = match
                result.append(alias)
                counts["name_or_contact"] += 1
            else:
                result.append(text[index])
                index += 1
        return "".join(result), counts


class Redactor:
    def __init__(self, aliases=None):
        self.aliases = LiteralAliases(aliases or {})
        self.rules = [
            ("secret", re.compile(r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----", re.S)),
            ("secret", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b")),
            ("secret", re.compile(r"(?i)(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|authorization|bearer|secret|密钥|令牌)\s*(?:是|为|[:：=])?\s*[\"']?[^\s，。；;！？!?<>\[\]【】]{4,}")),
            ("password", re.compile(r"(?i)(?:password|passwd|pwd|口令|密码|提取码|解压码|解压口令|wifi密码)\s*(?:是|为|[:：=]|改成|改为|设为)?\s*[\"']?[A-Za-z0-9!@#$%^&*()_+={}\[\]:/\\.~-]{2,}(?:[ \t]+[A-Za-z0-9!@#$%^&*()_+={}\[\]:/\\.~-]+)*")),
            ("password", re.compile(r"(?:密码|口令|提取码)\s*(?:是|为|[:：=])\s*[^\n，。；;！？!?<>]{1,100}")),
            ("verification_code", re.compile(r"(?:验证码|动态码|校验码|安全码|OTP)\s*(?:是|为|[:：=])?\s*\d{3,10}", re.I)),
            ("id_card", re.compile(r"(?<!\d)\d{6}[ -][12]\d{3}[ -][01]\d[0-3]\d[ -]\d{3}[\dXx](?!\d)")),
            ("id_card", re.compile(r"(?<![A-Za-z0-9])\d{6}[12]\d{3}[01]\d[0-3]\d\d{3}[\dXx](?![A-Za-z0-9])")),
            ("id_card", re.compile(r"(?:身份证(?:号码|号)?|证件(?:号码|号))\s*(?:是|为|[:：=])?\s*[A-Za-z0-9 -]{8,30}")),
            ("phone", re.compile(r"(?<!\d)(?:\+?86[ -]?)?1[3-9]\d(?:[ -]?\d){8}(?!\d)")),
            ("landline", re.compile(r"(?<!\d)(?:\+?86[ -]?)?0\d{2,3}[ -]?\d{7,8}(?:[ -]\d{1,6})?(?!\d)")),
            ("email", re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
            ("account", re.compile(r"(?i)(?:微信号|QQ号?|账号|账户|学号|工号|护照号|车牌号|银行卡号?|卡号|passport)\s*(?:是|为|[:：=])?\s*[A-Za-z0-9_@.-]{3,80}")),
            ("account", re.compile(r"\bwxid_[A-Za-z0-9_]+\b|(?<!\d)\d{5,}@chatroom\b")),
            ("url", re.compile(r"(?i)(?:https?://|www\.)[^\s<>\"'，。；【】]+")),
            ("ip", re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?(?!\d)")),
            ("address", re.compile(r"(?:收货地址|家庭住址|详细地址|住址|地址)\s*(?:是|为|[:：=])\s*[^\n，。；;！？!?<>]{2,160}")),
            ("address", re.compile(r"[\u4e00-\u9fff]{2,12}(?:省|自治区)[\u4e00-\u9fff0-9]{2,50}(?:路|街|巷|村|小区)[\u4e00-\u9fff0-9-]{0,20}(?:号|栋|幢|室)[\u4e00-\u9fff0-9-]{0,10}")),
            # Street-level delivery addresses need not include a province or city.
            ("address", re.compile(r"[\u4e00-\u9fff]{2,20}(?:路|街|巷)\s*\d{1,5}(?:弄|号)(?:[\u4e00-\u9fff0-9 -]{0,60})")),
            ("account", re.compile(r"(?i)\b[A-Z]{2}\d{4}\s+[A-Za-z]{1,5}:/[A-Z0-9]{8,}\b")),
            ("bank_card", re.compile(r"(?<!\d)(?:\d[ -]?){15,19}(?!\d)")),
            ("number", re.compile(r"(?<!\d)\d{5,}(?!\d)")),
        ]

    def _clean_once(self, text: str):
        text = unicodedata.normalize("NFKC", html.unescape(text))
        text = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", text)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        counts = Counter()
        for category, pattern in self.rules:
            text, count = pattern.subn(LABELS[category], text)
            counts[category] += count
        text, aliases = self.aliases.replace(text)
        counts.update(aliases)
        text = re.sub(r"(?:我叫|姓名[:：]?|联系人[:：]?|收件人[:：]?)\s*([\u4e00-\u9fff]{2,4})(?=$|[\s，。；！？])",
                      lambda m: m[0].replace(m[1], initials(m[1])), text)
        return text.strip(), +counts

    def clean(self, text: str):
        total = Counter()
        for _ in range(5):
            updated, counts = self._clean_once(text)
            if updated == text:
                break
            total.update(counts)
            text = updated
            if not counts:
                break
        return text, +total


def contacts_from_export(root: Path, override_path: Path):
    override_path.parent.mkdir(parents=True, exist_ok=True)
    if not override_path.exists():
        override_path.write_text(json.dumps({"aliases": {"黄宣": "hx"}, "preserve": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    overrides = json.loads(override_path.read_text(encoding="utf-8-sig"))
    aliases, contacts = dict(overrides.get("aliases", {})), {}
    preserve = set(overrides.get("preserve", []))
    non_names = set(overrides.get("non_names", []))
    candidates = defaultdict(set)
    for path in sorted((root / "HTML").glob("*_Contacts.csv")):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            # The exporter CSV has no header. Columns: username, display name, avatar, remark.
            for row in csv.reader(stream):
                if len(row) < 2:
                    continue
                username, nickname = row[0], row[1].strip()
                remark = row[3].strip() if len(row) > 3 else ""
                label = remark or nickname
                names = possible_names(label)
                alias = "/".join(aliases.get(name, initials(name)) for name in names) if names else label
                for name in names:
                    if name not in preserve:
                        aliases.setdefault(name, initials(name))
                        candidates[name].add(alias)
                # MD5 here matches the exporter's EXISTING Chat_<id> database identifier.
                # It is not an added fingerprint, integrity check, or anonymization mechanism.
                table = "Chat_" + hashlib.md5(username.encode(), usedforsecurity=False).hexdigest()
                contact = {"alias": alias, "group": username.endswith("@chatroom")}
                contacts[table] = contact
                if username not in preserve:
                    aliases.setdefault(username, alias or "联系人")
                if names and label not in preserve:
                    aliases.setdefault(label, alias)
                for field in (nickname, remark):
                    for name in possible_names(field):
                        if name not in preserve:
                            aliases.setdefault(name, initials(name))
    # Main conversation contacts are not all included in the exported CSV.
    # Read only the HTML header's session identifier and label, never its messages.
    for page in sorted((root / "HTML").rglob("*.html")):
        with page.open(encoding="utf-8-sig") as stream:
            header = stream.read(65536)
        body = re.search(r"<body\b[^>]*\bsid=[\"']([^\"']+)", header, re.I)
        display = re.search(r"<span\b[^>]*class=[\"']sname[\"'][^>]*>(.*?)</span>", header, re.S | re.I)
        if not display:
            continue
        label = html.unescape(re.sub(r"<[^>]+>", "", display[1])).strip()
        names = possible_names(label)
        alias = "/".join(aliases.get(name, initials(name)) for name in names) if names else label
        for name in names:
            if name not in preserve:
                aliases.setdefault(name, initials(name))
        if names and label not in preserve:
            aliases.setdefault(label, alias)
        if not body:
            continue
        username = html.unescape(body[1])
        table = "Chat_" + hashlib.md5(username.encode(), usedforsecurity=False).hexdigest()
        contacts[table] = {"alias": alias, "group": username.endswith("@chatroom")}
        if username not in preserve:
            aliases.setdefault(username, alias or "联系人")
    for name in preserve:
        aliases.pop(name, None)
    # Explicitly reviewed ordinary words must not be replaced inside prose.
    # Their contact identity remains in contacts; only literal text replacement changes.
    for name in non_names:
        if name not in overrides.get("aliases", {}):
            aliases.pop(name, None)
    # Exported contacts CSV omits chatrooms. Obtain only group identifiers, not profile blobs.
    db_path = root / "Databases" / "WCDB_Contact.sqlite"
    if db_path.exists():
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            for (username,) in conn.execute("SELECT userName FROM Friend ORDER BY userName"):
                table = "Chat_" + hashlib.md5(username.encode(), usedforsecurity=False).hexdigest()
                group = username.endswith("@chatroom")
                contacts.setdefault(table, {"alias": "群聊" if group else "联系人", "group": group})
        finally:
            conn.close()
    return aliases, contacts


MEDIA = {3: "图片", 34: "语音", 42: "名片", 43: "视频", 47: "表情", 48: "位置", 50: "通话", 66: "名片"}


def extract_text(row: dict, aliases: Redactor):
    kind = row["type"]
    if row.get("content_encoding") == "zstd" or row.get("content") is None:
        return "【压缩消息未解码】", "compressed_unresolved", False, Counter()
    if kind in MEDIA:
        return f"【{MEDIA[kind]}内容未纳入】", "media_omitted", False, Counter()
    content = row["content"]
    if not isinstance(content, str):
        return "【内容格式未知】", "unsupported", False, Counter()
    # Group text often has a sender prefix; remove it from text, retaining pseudonymous speaker separately.
    content = re.sub(r"^[A-Za-z0-9_@.-]{3,}:\r?\n", "", content)
    if kind == 49 or "<msg" in content:
        if "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper():
            return "【不支持的结构化消息】", "unsupported", False, Counter()
        try:
            element = ET.fromstring(content[content.index("<"):])
            texts = []
            for node in element.iter():
                if node.tag.lower() in {"title", "des", "content", "displayname"} and node.text:
                    texts.append(node.text)
            content = "\n".join(texts)
            if not content:
                return "【结构化消息内容未纳入】", "unsupported", False, Counter()
        except (ET.ParseError, ValueError):
            return "【结构化消息解析失败】", "unsupported", False, Counter()
    text, counts = aliases.clean(content)
    return text, "text" if kind == 1 else "structured_text", kind == 1 and bool(text), counts


def quoted(identifier: str):
    return '"' + identifier.replace('"', '""') + '"'


def audit_databases(root: Path):
    total, tables, first, last = 0, 0, None, None
    for path in sorted((root / "Databases").glob("message_*.sqlite")):
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'Chat_*'"):
                count, start, end = conn.execute(f"SELECT COUNT(*),MIN(CreateTime),MAX(CreateTime) FROM {quoted(name)}").fetchone()
                total += count
                tables += 1
                if start is not None:
                    first = min(first if first is not None else start, start)
                    last = max(last if last is not None else end, end)
        finally:
            conn.close()
    return {"messages": total, "tables": tables, "first_timestamp": first, "last_timestamp": last}


def timestamp_text(timestamp):
    return dt.datetime.fromtimestamp(timestamp, ZONE).isoformat() if timestamp is not None else None


def write_html(conn, directory: Path, page_size=1000):
    directory.mkdir(parents=True, exist_ok=True)
    header = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
              '<meta name="viewport" content="width=device-width,initial-scale=1">'
              '<title>清洗后的微信记录</title><style>'
              'body{font:16px/1.6 system-ui;max-width:960px;margin:24px auto;padding:0 16px}'
              'article{border-bottom:1px solid #ddd;padding:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
              'small{color:#666}nav a{margin-right:16px}</style><body>')
    links = []
    for cid, alias, count in conn.execute("SELECT c.id,c.alias,COUNT(m.id) FROM conversations c JOIN messages m ON c.id=m.conversation_id GROUP BY c.id ORDER BY c.id"):
        pages = (count + page_size - 1) // page_size
        folder = directory / cid
        folder.mkdir(exist_ok=True)
        links.append(f'<li><a href="{cid}/page-0001.html">{html.escape(alias)} · {count} 条 · {pages} 页</a></li>')
        cursor = conn.execute("SELECT timestamp,speaker_id,content,status FROM messages WHERE conversation_id=? ORDER BY timestamp,id", (cid,))
        for page in range(1, pages + 1):
            nav = '<nav><a href="../index.html">会话目录</a>'
            if page > 1:
                nav += f'<a href="page-{page-1:04}.html">上一页</a>'
            if page < pages:
                nav += f'<a href="page-{page+1:04}.html">下一页</a>'
            nav += f'<a href="page-{pages:04}.html">最后一页</a>第 {page}/{pages} 页</nav>'
            with (folder / f"page-{page:04}.html").open("w", encoding="utf-8") as stream:
                stream.write(header + f'<h1>{html.escape(alias)}</h1>' + nav)
                for _ in range(page_size):
                    row = cursor.fetchone()
                    if row is None:
                        break
                    timestamp, speaker, content, status = row
                    stream.write(f'<article><small>{timestamp_text(timestamp)} · {html.escape(speaker)} · {status}</small><pre>{html.escape(content)}</pre></article>')
                stream.write(nav + '</body></html>')
    (directory / "index.html").write_text(header + '<h1>清洗后的微信记录</h1><p>静态分页，可直接打开最后一页；未复制图片、头像、音视频或原始脚本。压缩内容仅保留占位说明。</p><ul>' + ''.join(links) + '</ul></body></html>', encoding="utf-8")


def clean_export(root: Path, output: Path, overrides: Path):
    root, output = root.resolve(), output.resolve()
    if root == output or root in output.parents or output in root.parents:
        raise ValueError("清洗目录必须与原始导出目录分开")
    output.mkdir(parents=True, exist_ok=True)
    if (output / "messages.sqlite3").exists():
        raise ValueError("输出目录已有清洗数据库，请指定新的输出目录")
    aliases, contacts = contacts_from_export(root, overrides)
    redactor = Redactor(aliases)
    conn = sqlite3.connect(output / "messages.sqlite3")
    conn.executescript("""
        CREATE TABLE conversations(id TEXT PRIMARY KEY, alias TEXT, kind TEXT);
        CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation_id TEXT REFERENCES conversations(id),
            timestamp INTEGER, direction TEXT, speaker_id TEXT, type INTEGER,
            content TEXT, status TEXT, eligible_text INTEGER);
        CREATE INDEX messages_by_conversation ON messages(conversation_id,timestamp,id);
    """)
    report = {"source_records": 0, "conversation_count": 0, "replacement_counts": {},
              "status_counts": {}, "direction_counts": {}, "latest_by_status": {},
              "first_timestamp": None, "last_timestamp": None,
              "eligible_private_texts": 0, "eligible_group_texts": 0,
              "eligible_unknown_texts": 0, "unmatched_conversations": 0,
              "unresolved_group_speakers": 0, "residual_rule_hits": 0}
    replacements, statuses, directions = Counter(), Counter(), Counter()
    conversations, speakers = {}, {}
    bounds, monthly, latest = {}, defaultdict(Counter), {}
    pending_secrets = {}
    source = (root / "messages.jsonl").open(encoding="utf-8-sig")
    cleaned = (output / "messages.jsonl").open("w", encoding="utf-8")
    try:
        for index, line in enumerate(source, 1):
            row = json.loads(line)
            key = (row["account"], row["conversation_table"])
            if key not in conversations:
                cid = f"c{len(conversations)+1:04}"
                contact = contacts.get(row["conversation_table"], {})
                group = contact.get("group", False)
                kind = "group" if group else "private" if contact else "unknown"
                if not contact:
                    report["unmatched_conversations"] += 1
                alias, _ = redactor.clean(contact.get("alias") or cid)
                conversations[key] = (cid, group, kind)
                conn.execute("INSERT INTO conversations VALUES(?,?,?)", (cid, alias or cid, kind))
            cid, group, kind = conversations[key]
            speaker = "self" if row["direction"] == "outgoing" else f"other-{cid}"
            content = row.get("content")
            if group and speaker != "self":
                prefix = re.match(r"^([A-Za-z0-9_@.-]{3,}):\r?\n", content or "")
                if prefix:
                    uid = prefix[1]
                    if uid not in speakers:
                        speakers[uid] = f"p{len(speakers)+1:05}"
                    speaker = speakers[uid]
                else:
                    speaker = "group-speaker-unknown"
                    report["unresolved_group_speakers"] += 1
            text, status, eligible, counts = extract_text(row, redactor)
            # A password can be sent in the message AFTER "密码是多少？".
            pending = pending_secrets.get(cid)
            if pending and 0 <= int(row["timestamp"]) - pending[0] <= 600 and status == "text":
                raw_value = re.sub(r"^[A-Za-z0-9_@.-]{3,}:\r?\n", "", content or "")
                raw_value = unicodedata.normalize("NFKC", raw_value).strip()
                if re.fullmatch(r"[A-Za-z0-9!@#$%^&*()_+={}\[\]:/\\.~-]{2,128}", raw_value):
                    text = pending[1]
                    counts["cross_message_secret"] += 1
                    pending_secrets.pop(cid, None)
            if status == "text" and isinstance(content, str):
                marker = re.search(r"密码|口令|提取码|验证码|校验码|密钥|token|password", content, re.I)
                if marker:
                    label = "验证码" if marker[0] in {"验证码", "校验码"} else "密钥" if marker[0].lower() in {"密钥", "token"} else "密码"
                    pending_secrets[cid] = (int(row["timestamp"]), label)
            # Current training candidate stream includes readable private text only.
            eligible = eligible and row["direction"] in {"incoming", "outgoing"}
            item = {"id": index, "conversation_id": cid, "timestamp": int(row["timestamp"]),
                    "datetime": timestamp_text(int(row["timestamp"])), "direction": row["direction"],
                    "speaker_id": speaker, "type": row["type"], "content": text,
                    "status": status, "eligible_text": bool(eligible), "group": group,
                    "conversation_kind": kind}
            cleaned.write(json.dumps(item, ensure_ascii=False) + "\n")
            conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)", (index, cid, item["timestamp"], item["direction"], speaker, item["type"], text, status, eligible))
            replacements.update(counts)
            statuses[status] += 1
            directions[row["direction"]] += 1
            latest[status] = max(latest.get(status, 0), item["timestamp"])
            month = item["datetime"][:7]
            monthly[month][status] += 1
            previous = bounds.setdefault(cid, [item["timestamp"], item["timestamp"], 0])
            previous[0], previous[1] = min(previous[0], item["timestamp"]), max(previous[1], item["timestamp"])
            previous[2] += 1
            if eligible:
                report[f"eligible_{kind}_texts"] += 1
            report["source_records"] = index
            if index % 20000 == 0:
                conn.commit()
                print(json.dumps({"processed": index}, ensure_ascii=False), flush=True)
        conn.commit()
        report["conversation_count"] = len(conversations)
        report["replacement_counts"] = dict(replacements)
        report["status_counts"] = dict(statuses)
        report["direction_counts"] = dict(directions)
        report["latest_by_status"] = {k: timestamp_text(v) for k, v in latest.items()}
        report["first_timestamp"] = min((v[0] for v in bounds.values()), default=None)
        report["last_timestamp"] = max((v[1] for v in bounds.values()), default=None)
        report["first_datetime"] = timestamp_text(report["first_timestamp"])
        report["last_datetime"] = timestamp_text(report["last_timestamp"])
        report["monthly_counts"] = dict(sorted(monthly.items()))
        report["original_database_audit"] = audit_databases(root)
        report["database_count_matches_jsonl"] = report["original_database_audit"]["messages"] == report["source_records"]
        report["database_latest_matches_jsonl"] = report["original_database_audit"]["last_timestamp"] == report["last_timestamp"]
        report["limits"] = ["未解码消息没有正文，不用于训练。", "不复制媒体、联系人原表、压缩字节及原始标识。", "人名依赖联系人词典与手工映射，缩写不是匿名保证。", "无标签密码、未登记姓名、地址和语义隐私仍可能漏检。", "eligible_text 仅表示技术上可读，尚未完成会话切分和人工隐私审核。"]
        # Re-scan stored output with the same rules, keeping only counts, never matched values.
        for content, in conn.execute("SELECT content FROM messages WHERE status IN ('text','structured_text')"):
            _, residual = redactor.clean(content)
            report["residual_rule_hits"] += sum(residual.values())
        report["conversation_ranges"] = {cid: {"first": timestamp_text(v[0]), "last": timestamp_text(v[1]), "messages": v[2]} for cid, v in bounds.items()}
        write_html(conn, output / "HTML")
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        source.close()
        cleaned.close()
        conn.close()
    return report


def main():
    parser = argparse.ArgumentParser(description="本地微信文本脱敏与完整性检查")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--aliases", type=Path, default=Path("data/private/wechat_name_aliases.json"))
    args = parser.parse_args()
    report = clean_export(args.source, args.output, args.aliases)
    print(json.dumps({key: report[key] for key in ("source_records", "conversation_count", "first_datetime", "last_datetime", "status_counts", "replacement_counts", "residual_rule_hits", "database_count_matches_jsonl", "database_latest_matches_jsonl")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

# Contact names and credentials below are fictional test fixtures.
import hashlib
import json
import sqlite3

from aitown.wechat_clean import Redactor, clean_export, contacts_from_export


def test_html_header_recovers_contact_missing_from_csv(tmp_path):
    source = tmp_path / "source"
    (source / "HTML").mkdir(parents=True)
    (source / "HTML" / "example.html").write_text(
        '<html><body uid="owner" sid="demo-header-user"><span class="sname">李四</span>'
        '<p>这个原始 HTML 正文不应复制：SecretInOriginalHTML</p></body></html>', encoding="utf-8")
    row = {"account": "owner", "conversation_table": "Chat_" + hashlib.md5(b"demo-header-user", usedforsecurity=False).hexdigest(),
           "timestamp": 1700000000, "direction": "outgoing", "type": 1,
           "content": "李四，明天见", "content_encoding": "utf-8"}
    (source / "messages.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "clean"
    report = clean_export(source, output, tmp_path / "aliases.json")
    assert report["unmatched_conversations"] == 0
    assert report["eligible_private_texts"] == 1
    conn = sqlite3.connect(output / "messages.sqlite3")
    assert conn.execute("SELECT alias,kind FROM conversations").fetchone() == ("ls", "private")
    assert conn.execute("SELECT content FROM messages").fetchone()[0] == "ls,明天见"
    conn.close()
    rendered = (output / "HTML" / "c0001" / "page-0001.html").read_text(encoding="utf-8")
    assert "SecretInOriginalHTML" not in rendered
    assert "李四" not in rendered


def test_empty_session_attributes_still_contribute_names(tmp_path):
    source = tmp_path / "source"
    (source / "HTML").mkdir(parents=True)
    (source / "HTML" / "example.html").write_text(
        '<body uid="" sid=""><span class="sname">、张小明</span></body>', encoding="utf-8")
    aliases, contacts = contacts_from_export(source, tmp_path / "aliases.json")
    text, _ = Redactor(aliases).clean("张小明明天再来")
    assert text == "zxm明天再来"
    assert contacts == {}

import json
import sqlite3

from aitown.data_pipeline.review import distribution
from aitown.data_pipeline.selection import assess, select
from aitown.wechat_clean import Redactor, contacts_from_export, possible_names


def message(id=1, **changes):
    row = dict(id=id, conversation_id="c0001", timestamp=1000+id, direction="outgoing",
               conversation_kind="private", status="text", type=1, eligible_text=True, content="独立回答")
    row.update(changes)
    return row


def test_selection_exclusions_and_target_ownership():
    good = message()
    assert assess(good, [], {"1": "approved"})["selected"]
    assert not assess(good, [], {})["selected"]
    for changes in ({"conversation_kind": "group"}, {"conversation_kind": "unknown"},
                    {"status": "compressed_unresolved"}, {"type": 49},
                    {"status": "media_omitted"}, {"direction": "unknown"}, {"content": ""}):
        assert not assess(message(**changes), [], {"1": "approved"})["selected"]
    assert assess(message(direction="incoming"), [], {"1": "approved"})["role"] == "context"
    assert assess(good, [], {"1": "excluded"})["reasons"] == ["privacy_excluded"]


def test_missing_context_is_preserved_and_sessions_do_not_mix():
    attachment = message(status="media_omitted", type=34, eligible_text=False, content="【语音内容未纳入】")
    reply = message(id=2, content="听语音，这个可以")
    result = assess(reply, [attachment], {"2": "approved"})
    assert not result["selected"]
    assert result["missing_context_ids"] == [1]
    assert result["quality"] == "insufficient_context"
    long_reply = message(id=2, content="我昨天讨论的那件安排可以先这样处理，晚点再说")
    assert not assess(long_reply, [attachment], {"2": "approved"})["selected"]
    other = message(id=3, conversation_id="c0002", content="这个可以")
    result = list(select([attachment, other], {"3": "approved"}))[-1][1]
    assert result["selected"] and result["missing_context_ids"] == []


def test_address_promotion_and_short_password_redaction():
    cleaner = Redactor()
    for text in ("示例路2500号学生公寓驿站", "样本路50弄示例小区33号402", "地址\n示例东路8号示例学院"):
        cleaned, counts = cleaner.clean(text)
        assert counts["address"] > 0
        assert "2500" not in cleaned and "402" not in cleaned and "8号" not in cleaned
        assert cleaner.clean(cleaned)[0] == cleaned
    assert "XY1234" not in cleaner.clean("复制口令 XY1234 V:/ABCDEF1234")[0]
    assert "q7" not in cleaner.clean("密码：q7")[0]
    # Existing broad long-number masking is retained; ambiguous numbers are review labels.
    assert cleaner.clean("123456")[0] == "编号"


def test_mapping_preserves_words_aliases_and_distinct_contacts(tmp_path):
    root = tmp_path / "raw"
    (root / "HTML").mkdir(parents=True)
    (root / "HTML" / "test_Contacts.csv").write_text(
        "user1,程序设计,,\nuser2,张三,,\nuser3,赵四,,\nuser4,欧阳明老师,,\nuser5,单明,,\n", encoding="utf-8")
    path = tmp_path / "aliases.json"
    path.write_text(json.dumps({"aliases": {"张三": "zs", "赵四": "zs", "单明": "sm"},
                                "preserve": ["zs"], "non_names": ["程序设计"]}, ensure_ascii=False), encoding="utf-8")
    aliases, contacts = contacts_from_export(root, path)
    assert "程序设计" not in aliases
    assert aliases["单明"] == "sm"
    assert "欧阳明" in possible_names("欧阳明老师")
    assert len(contacts) == 5
    assert sum(c["alias"] == "zs" for c in contacts.values()) == 2
    result, _ = Redactor(aliases).clean("张三和赵四讨论程序设计，原代称zs保留")
    assert "张三" not in result and "赵四" not in result
    assert "程序设计" in result and "zs" in result


def test_distribution_totals_and_missing_runs(tmp_path):
    path = tmp_path / "messages.sqlite3"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE conversations(id TEXT, kind TEXT);"
        "CREATE TABLE messages(id INTEGER,conversation_id TEXT,timestamp INTEGER,direction TEXT,"
        "type INTEGER,status TEXT,eligible_text INTEGER,content TEXT);")
    conn.execute("INSERT INTO conversations VALUES('c0001','private')")
    for i in range(12):
        conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)", (i, "c0001", 1700000000+i,
                     "outgoing", 1, "text" if i == 0 else "compressed_unresolved", i == 0, "text"))
    result = distribution(conn)
    assert result["total"] == 12 and result["totals_match_database"]
    assert result["conversations"][0]["longest_missing_run"] == 11
    assert result["conversations"][0]["compressed_heavy"]
    assert sum(row["count"] for row in result["cross_counts"]) == 12
    conn.close()

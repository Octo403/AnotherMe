from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path

import pytest

from aitown.config import Settings
from aitown.question_bank.models import BankConfig, Source
from aitown.question_bank.prepare import parse_file
from aitown.question_bank.repository import BankRepository
from aitown.ui.demo import settings_for_ui
from aitown.ui.service import UIService


PAPER = '''# 人工智能的软件基础 2026 春期末试题回忆

> 考试日期：2026-06-20

## 1. 残差网络

围绕残差网络（Residual Network, ResNet）完成以下内容：

1. 绘制残差网络/残差块的示意图；
2. 计算给定残差结构中的参数量。

## 2. 大模型系统软件科普讲座设计（25 分）

设计一次面向本科生、时长 2 小时的科普讲座。

### a) 讲座目标

设计讲座的具体目标。

### b) 讲座内容、时间安排与架构图

设计主要内容、时间安排，并绘制软件技术架构图。

## 评分标准

- 讲座目标明确；
- 时间安排合理。
'''


def test_markdown_headings_keep_major_questions_subparts_and_line_provenance(tmp_path):
    path = tmp_path/'paper.md'
    path.write_text(PAPER, encoding='utf-8')
    parsed = parse_file(path, {'course_id': '人工智能的软件基础', 'category_hint': 'exam'}, tmp_path/'out')
    assert parsed.status == 'ready' and parsed.year == 2026 and parsed.category == 'recollection'
    assert [q.number for q in parsed.questions] == ['1', '2']
    first, second = parsed.questions
    assert '1. 绘制残差网络' in first.text and '2. 计算给定残差结构' in first.text
    assert '### a) 讲座目标' in second.text and '### b) 讲座内容' in second.text
    assert '评分标准' in second.text and '- 时间安排合理。' in second.text
    assert len(first.subquestions) == len(second.subquestions) == 2
    assert all(not q.images for q in parsed.questions)
    lines = PAPER.splitlines()
    for q in parsed.questions:
        assert q.text == '\n'.join(lines[n-1] for n in q.pages).strip()
        assert all(parsed.pages[n-1].location_kind == 'line' for n in q.pages)


def test_markdown_fenced_examples_do_not_become_question_or_answer_boundaries(tmp_path):
    text = '## 1. 阅读程序\n```text\n## 2. 这是代码中的文字\n参考答案\n答：也是代码中的文字\n```\n解释程序。\n## 2. 证明题\n证明结论。'
    path = tmp_path/'paper.md';path.write_text(text, encoding='utf-8')
    parsed = parse_file(path, {'category_hint': 'exam'}, tmp_path/'out')
    assert len(parsed.questions) == 2
    assert '解释程序。' in parsed.questions[0].text and '答：也是代码中的文字' in parsed.questions[0].text
    assert '证明结论。' in parsed.questions[1].text


@pytest.mark.parametrize('text', [
    '## 1. 证明题\n证明 $x^2$ 非负。\n## 参考答案\n答案秘密',
    '## 1. 证明题\n证明 $x^2$ 非负。\n## 2. 参考答案\n答案秘密',
    '# 期末试卷\n1. 证明 $x^2$ 非负。\n   1. 说明条件。\n2. 计算题\n计算 $x+1$。\n参考答案\n答案秘密',
])
def test_markdown_excludes_answer_sections_and_keeps_nested_lists(tmp_path, text):
    path = tmp_path/'paper.md';path.write_text(text, encoding='utf-8')
    parsed = parse_file(path, {'category_hint': 'exam'}, tmp_path/'out')
    assert parsed.questions
    assert all('答案秘密' not in q.text and '参考答案' not in q.text for q in parsed.questions)
    if '说明条件' in text:
        assert len(parsed.questions) == 2 and '说明条件' in parsed.questions[0].text


@pytest.mark.parametrize('member,suffix,content_format', [('', '.md', 'markdown'), ('nested/paper.md', '.zip', 'markdown'), ('', '.txt', 'plain')])
def test_text_questions_survive_ui_chat_history_restart_and_source_access(tmp_path, member, suffix, content_format):
    config = BankConfig(root=tmp_path/'bank')
    raw = config.root/'raw'/'paper.md'
    raw.parent.mkdir(parents=True);raw.write_text(PAPER, encoding='utf-8')
    source = Source(course_id='人工智能的软件基础', repository='local/reviewed', path='paper'+suffix,
                    member=member, url='https://example.org/paper')
    with BankRepository(config.database) as repo:
        repo.save_discovery([{'id': source.course_id, 'name': source.course_id, 'path': 'local/course'}], [])
        if member:
            with repo.conn:archive = repo.save_source(source.model_copy(update={'member': ''}))
            repo.set_source(archive, status='expanded')
        with repo.conn:sid = repo.save_source(source)
        repo.set_source(sid, local_path=str(raw))
        repo.replace_parsed(sid, parse_file(raw, repo.source(sid), tmp_path/'out', config))
    settings = settings_for_ui(Settings(question_bank_dir=config.root), tmp_path/'app')
    metadata = tmp_path/'ui.sqlite3'
    with closing(UIService(settings, metadata)) as service:
        session = service.call('_create_session', {})['id']
        reply = service.call('_chat', {'session': session, 'question': '@人工智能的软件基础 找残差网络相关的题'})['reply']
        question = reply['questions'][0]
        assert question['content_format'] == content_format and '2. 计算给定残差结构' in question['text']
        assert question['images'] == [] and 'answers' not in question
        assert service.call('_question_source', question['id'], sid)[0] == raw.read_bytes()
    with closing(UIService(settings, metadata)) as service:
        restored = service.call('_history', session)['turns'][0]['reply']['questions'][0]
        assert restored['text'] == question['text'] and restored['content_format'] == content_format


def test_settings_and_material_library_are_separate_panels():
    class Panels(HTMLParser):
        def __init__(self):super().__init__();self.stack = [];self.owners = {}
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get('id'):self.owners[attrs['id']] = [item[1] for item in self.stack]
            if tag not in ('meta', 'link', 'input', 'br', 'hr', 'img'):self.stack.append((tag, attrs.get('id')))
        def handle_endtag(self, tag):
            for i in range(len(self.stack)-1, -1, -1):
                if self.stack[i][0] == tag:self.stack = self.stack[:i];break
    parser = Panels()
    html = (Path(__file__).parents[1]/'src/aitown/ui/static/index.html').read_text(encoding='utf-8')
    parser.feed(html)
    assert 'settings' in parser.owners['question-model-settings']
    assert 'settings' in parser.owners['skills']
    for element in ('course-library', 'documents', 'memories', 'facts'):
        assert 'materials' in parser.owners[element] and 'settings' not in parser.owners[element]
    course_form=html.split('<form id="course-form"',1)[1].split('</form>',1)[0]
    assert '<datalist' not in course_form and 'role="combobox"' in course_form
    assert 'settings' in parser.owners['local-model-url']

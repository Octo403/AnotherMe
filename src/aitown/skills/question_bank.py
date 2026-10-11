from .base import Skill
from ..question_bank.models import GetArgs, QuestionResult, SearchArgs, StatusArgs, StatusResult


class QuestionSearch(Skill):
    name = 'question_search'
    description = '只读课程题库：按课程、知识点、年份和题型找真实题目。未准备时报告 needs_preparation，不下载，不查个人库。顶层 question_ids 可绑定 question_get。'
    arguments = SearchArgs
    result_type = QuestionResult

    def __init__(self, bank):
        self.bank = bank

    def run(self, arguments):
        return self.bank.search(arguments)


class QuestionGet(Skill):
    name = 'question_get'
    description = '批量读取本会话实际题目 ID 的完整原题、图表和出处。include_answer 默认 false；仅用户明确要答案时开启，返回已有来源答案，绝不生成替代答案。'
    arguments = GetArgs
    result_type = QuestionResult

    def __init__(self, bank):
        self.bank = bank

    def run(self, arguments):
        return self.bank.get(arguments)


class CourseStatus(Skill):
    name = 'course_status'
    description = '只读查询指定课程题库的真实准备任务、进度、失败数和覆盖范围。'
    arguments = StatusArgs
    result_type = StatusResult

    def __init__(self, bank):
        self.bank = bank

    def run(self, arguments):
        return self.bank.status(arguments.course)
